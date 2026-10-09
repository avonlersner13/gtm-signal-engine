"""Per-account and per-user feature vectors from a single streaming pass over events.

Windows (7/14/30/90 days) are counted with streaming counters; nothing is materialized
per window. Recency-decayed counts (half-life from signals.toml) feed the intent score.
``[bot]`` users and unresolved users are skipped.
"""

from __future__ import annotations

import calendar
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from signal_engine.config import Config
from signal_engine.models import AccountFeatures, Event, Resolution, UserFeatures

DAY = timedelta(days=1)


@dataclass(slots=True)
class _AccountAcc:
    counts: dict[int, Counter[str]]
    prev_week: Counter[str] = field(default_factory=Counter)
    decayed: dict[str, float] = field(default_factory=dict)
    first_ts: dict[str, datetime] = field(default_factory=dict)
    last_ts: dict[str, datetime] = field(default_factory=dict)
    active_days: set[int] = field(default_factory=set)
    people: set[str] = field(default_factory=set)
    runs_30: int = 0
    runs_prev_30: int = 0
    instruments: Counter[str] = field(default_factory=Counter)
    graviton_mtd: float = 0.0
    ryzen_mtd: float = 0.0
    companies: set[str] = field(default_factory=set)
    mcp_tools: Counter[str] = field(default_factory=Counter)
    ignored_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _UserAcc:
    account_id: str
    run_days: set[int] = field(default_factory=set)
    decayed: dict[str, float] = field(default_factory=dict)
    value_score: float = 0.0
    count: int = 0
    first_ts: dict[str, datetime] = field(default_factory=dict)
    last_ts: datetime | None = None


@dataclass(frozen=True, slots=True)
class FeatureSet:
    """Account and user features for one as-of date."""

    accounts: Mapping[str, AccountFeatures]
    users: Mapping[str, UserFeatures]


@dataclass(frozen=True, slots=True)
class _Clock:
    """Window cutoffs computed once per run."""

    as_of: datetime
    cutoffs: tuple[tuple[int, datetime], ...]
    prev_week: tuple[datetime, datetime]
    prev_30: tuple[datetime, datetime]
    cut_30: datetime
    month_start: datetime
    decay_rate: float


def _clock(cfg: Config, as_of: datetime) -> _Clock:
    month_start = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return _Clock(
        as_of=as_of,
        cutoffs=tuple((w, as_of - w * DAY) for w in cfg.windows),
        prev_week=(as_of - 14 * DAY, as_of - 7 * DAY),
        prev_30=(as_of - 60 * DAY, as_of - 30 * DAY),
        cut_30=as_of - 30 * DAY,
        month_start=month_start,
        decay_rate=math.log(2) / cfg.half_life_days,
    )


def capped_intent(decayed: Mapping[str, float], cfg: Config) -> float:
    """Sum of per-signal contributions clamp(weight*decayed, cap), before scaling."""
    total = 0.0
    for event, value in decayed.items():
        rule = cfg.signals.get(event)
        if rule is None:
            continue
        raw = rule.weight * value
        total += min(raw, rule.cap) if rule.cap >= 0 else max(raw, rule.cap)
    return total


def _account_event(
    acc: _AccountAcc, e: Event, clock: _Clock, decay: float, uid: str, company: str
) -> None:
    etype, ts = e.event_type, e.ts
    for window, cutoff in clock.cutoffs:
        if ts > cutoff:
            acc.counts[window][etype] += 1
    if clock.prev_week[0] < ts <= clock.prev_week[1]:
        acc.prev_week[etype] += 1
    acc.decayed[etype] = acc.decayed.get(etype, 0.0) + decay
    acc.first_ts.setdefault(etype, ts)
    acc.last_ts[etype] = ts
    if ts > clock.cut_30:
        acc.active_days.add(ts.toordinal())
        acc.people.add(uid)
        acc.companies.add(company)
        _recent_detail(acc, e)
    elif etype == "run_completed" and clock.prev_30[0] < ts:
        acc.runs_prev_30 += 1
    if etype == "macro_runner_minutes" and ts >= clock.month_start:
        minutes = float(e.props.get("minutes", 0.0))
        if e.props.get("runner") == "ryzen":
            acc.ryzen_mtd += minutes
        else:
            acc.graviton_mtd += minutes


def _recent_detail(acc: _AccountAcc, e: Event) -> None:
    etype = e.event_type
    if etype == "run_completed":
        acc.runs_30 += 1
        acc.instruments[str(e.props.get("instrument", "simulation"))] += 1
    elif etype == "mcp_tool_call":
        acc.mcp_tools[str(e.props.get("tool", "unknown"))] += 1
    elif etype == "benchmark_ignored":
        acc.ignored_reasons.append(str(e.props.get("reason", "")))


def _user_event(
    acc: _UserAcc, e: Event, decay: float, value_events: frozenset[str], mult: float
) -> None:
    etype = e.event_type
    acc.count += 1
    acc.decayed[etype] = acc.decayed.get(etype, 0.0) + decay
    acc.value_score += decay * (mult if etype in value_events else 1.0)
    acc.first_ts.setdefault(etype, e.ts)
    acc.last_ts = e.ts
    if etype == "run_completed":
        acc.run_days.add(e.ts.toordinal())


def compute_features(
    events: Iterable[Event],
    resolutions: Mapping[str, Resolution],
    cfg: Config,
    as_of: datetime,
) -> FeatureSet:
    """Build account and user features from time-ordered events up to ``as_of``."""
    clock = _clock(cfg, as_of)
    value_events = cfg.lifecycle["value_events"]
    mult = cfg.lifecycle["value_event_multiplier"]
    accounts: dict[str, _AccountAcc] = {}
    users: dict[str, _UserAcc] = {}
    for e in events:
        if e.ts > as_of:
            break
        res = resolutions.get(e.user_id)
        if res is None or res.account_id is None or res.is_bot:
            continue
        decay = math.exp(-((as_of - e.ts) / DAY) * clock.decay_rate)
        acc = accounts.get(res.account_id)
        if acc is None:
            acc = accounts[res.account_id] = _AccountAcc({w: Counter() for w in cfg.windows})
        _account_event(acc, e, clock, decay, e.user_id, res.company_id or res.account_id)
        uacc = users.get(e.user_id)
        if uacc is None:
            uacc = users[e.user_id] = _UserAcc(res.account_id)
        _user_event(uacc, e, decay, value_events, mult)
    return FeatureSet(
        accounts={a: _freeze_account(a, acc, clock) for a, acc in sorted(accounts.items())},
        users={u: _freeze_user(u, acc, cfg) for u, acc in sorted(users.items())},
    )


def _freeze_account(account_id: str, acc: _AccountAcc, clock: _Clock) -> AccountFeatures:
    as_of = clock.as_of
    days_in_month = calendar.monthrange(as_of.year, as_of.month)[1]
    elapsed = max(1.0, (as_of - clock.month_start) / DAY)
    return AccountFeatures(
        account_id=account_id,
        counts={w: dict(c) for w, c in acc.counts.items()},
        prev_week=dict(acc.prev_week),
        decayed={k: round(v, 6) for k, v in acc.decayed.items()},
        first_ts=dict(acc.first_ts),
        last_ts=dict(acc.last_ts),
        active_days_30=len(acc.active_days),
        people_30=len(acc.people),
        runs_30=acc.runs_30,
        runs_prev_30=acc.runs_prev_30,
        instruments_30=dict(acc.instruments),
        graviton_minutes_mtd=round(acc.graviton_mtd, 1),
        ryzen_minutes_mtd=round(acc.ryzen_mtd, 1),
        projected_graviton_minutes=round(acc.graviton_mtd / elapsed * days_in_month, 1),
        companies_active_30=tuple(sorted(acc.companies)),
        mcp_tools_30=dict(acc.mcp_tools),
        ignored_reasons_30=tuple(acc.ignored_reasons),
    )


def _freeze_user(user_id: str, acc: _UserAcc, cfg: Config) -> UserFeatures:
    intent = 100.0 * capped_intent(acc.decayed, cfg) / cfg.intent_scale
    return UserFeatures(
        user_id=user_id,
        account_id=acc.account_id,
        run_days=tuple(sorted(acc.run_days)),
        intent=round(min(100.0, max(0.0, intent)), 2),
        value_score=round(acc.value_score, 4),
        event_count=acc.count,
        first_ts=dict(acc.first_ts),
        last_ts=acc.last_ts,
    )
