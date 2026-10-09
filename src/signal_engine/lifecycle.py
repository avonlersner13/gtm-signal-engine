"""Lifecycle state machine with entry criteria (thresholds in signals.toml).

Account stages, in order, each with a prerequisite:

* signed_up    -- any user signed up
* installed    -- GitHub App installed                        (after signed_up)
* activated    -- baseline_created AND first_pr_report         (after installed)
* engaged      -- a user ran benchmarks on 3+ distinct days in 14 days, acknowledged a
                  regression, or enabled the required check   (after activated)
* pql          -- an engaged user's intent score >= threshold  (after engaged)
* pqa          -- 2+ users engaged in the last 30 days OR over the free cap OR an
                  enterprise-intent signal in the last 30 days (after engaged)
* opportunity  -- a play was accepted (simulated CRM flag)     (after pqa)

``churn_risk`` is an overlay added by the pipeline from the churn play, not a stage.
A stage's entry time is the later of its own criteria time and its prerequisite's.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta

from signal_engine.config import Config
from signal_engine.models import (
    ACCOUNT_STAGES,
    AccountFeatures,
    LifecycleResult,
    Opportunity,
    SeatStatus,
    StageEntry,
    UserFeatures,
)

PREREQUISITE = {
    "installed": "signed_up",
    "activated": "installed",
    "engaged": "activated",
    "pql": "engaged",
    "pqa": "engaged",
    "opportunity": "pqa",
}


def _ordinal_to_dt(ordinal: int) -> datetime:
    return datetime.fromordinal(ordinal).replace(tzinfo=UTC)


def run_days_engaged_at(run_days: Sequence[int], min_days: int, window: int) -> datetime | None:
    """Earliest day the user had runs on ``min_days`` distinct days within ``window`` days."""
    for i in range(len(run_days) - min_days + 1):
        if run_days[i + min_days - 1] - run_days[i] < window:
            return _ordinal_to_dt(run_days[i + min_days - 1])
    return None


def user_engaged_at(user: UserFeatures, cfg: Config) -> datetime | None:
    """When the user first met any engagement criterion (None if never)."""
    lc = cfg.lifecycle
    candidates = [
        run_days_engaged_at(user.run_days, lc["engaged_min_run_days"], lc["engaged_window_days"]),
        user.first_ts.get("regression_acknowledged"),
        user.first_ts.get("required_check_enabled"),
    ]
    found = [c for c in candidates if c is not None]
    return min(found) if found else None


def engaged_recently(user: UserFeatures, cfg: Config, as_of: datetime) -> bool:
    """True if the user met an engagement criterion within the PQA recency window."""
    lc = cfg.lifecycle
    cutoff = as_of - timedelta(days=lc["pqa_engaged_recency_days"])
    recent_days = [d for d in user.run_days if d > cutoff.toordinal()]
    if run_days_engaged_at(recent_days, lc["engaged_min_run_days"], lc["engaged_window_days"]):
        return True
    return any(
        (ts := user.first_ts.get(e)) is not None and ts > cutoff
        for e in ("regression_acknowledged", "required_check_enabled")
    )


def _later(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None or b is None:
        return None
    return max(a, b)


def _pqa_criteria(
    recent_engaged_times: list[datetime],
    seats: SeatStatus | None,
    features: AccountFeatures,
    cfg: Config,
    as_of: datetime,
) -> datetime | None:
    lc = cfg.lifecycle
    found: list[datetime] = []
    if len(recent_engaged_times) >= lc["pqa_min_engaged_users"]:
        found.append(sorted(recent_engaged_times)[lc["pqa_min_engaged_users"] - 1])
    if seats is not None and seats.over_cap:
        found.append(seats.cap_exceeded_at or as_of)
    window_start = as_of - timedelta(days=lc["enterprise_intent_window_days"])
    for event in lc["enterprise_intent_events"]:
        last = features.last_ts.get(event)
        if last is not None and last > window_start:
            found.append(last)
    return min(found) if found else None


def account_stages(
    features: AccountFeatures,
    engaged_times: list[datetime],
    recent_engaged_times: list[datetime],
    pql_times: list[datetime],
    seats: SeatStatus | None,
    opportunity_at: datetime | None,
    cfg: Config,
    as_of: datetime,
) -> dict[str, datetime]:
    """Entry timestamps for every stage the account has reached."""
    first = features.first_ts
    baseline, report = first.get("baseline_created"), first.get("first_pr_report")
    criteria: dict[str, datetime | None] = {
        "signed_up": first.get("signed_up") or min(first.values(), default=None),
        "installed": first.get("github_app_installed"),
        "activated": max(baseline, report) if baseline and report else None,
        "engaged": min(engaged_times, default=None),
        "pql": min(pql_times, default=None),
        "pqa": _pqa_criteria(recent_engaged_times, seats, features, cfg, as_of),
        "opportunity": opportunity_at,
    }
    entered: dict[str, datetime] = {}
    for stage in ACCOUNT_STAGES:
        prereq = PREREQUISITE.get(stage)
        when = criteria[stage] if prereq is None else _later(criteria[stage], entered.get(prereq))
        if when is not None:
            entered[stage] = when
    return entered


def current_stage(entered: Mapping[str, datetime]) -> str:
    """Furthest stage reached, or 'none'."""
    reached = [s for s in ACCOUNT_STAGES if s in entered]
    return reached[-1] if reached else "none"


def run_lifecycle(
    account_features: Mapping[str, AccountFeatures],
    user_features: Mapping[str, UserFeatures],
    seats: Mapping[str, SeatStatus],
    opportunities: Iterable[Opportunity],
    cfg: Config,
    as_of: datetime,
) -> LifecycleResult:
    """Compute user and account stages plus every transition."""
    threshold = cfg.lifecycle["pql_min_user_intent"]
    engaged: dict[str, list[tuple[datetime, str]]] = {}
    recent: dict[str, list[datetime]] = {}
    pql: dict[str, list[tuple[datetime, str]]] = {}
    entries: list[StageEntry] = []
    for uid, uf in user_features.items():
        at = user_engaged_at(uf, cfg)
        if at is None:
            continue
        engaged.setdefault(uf.account_id, []).append((at, uid))
        entries.append(StageEntry("user", uid, "engaged", at))
        if engaged_recently(uf, cfg, as_of):
            recent.setdefault(uf.account_id, []).append(at)
        if uf.intent >= threshold:
            pql.setdefault(uf.account_id, []).append((at, uid))
            entries.append(StageEntry("user", uid, "pql", at))
    opp_at = {o.account_id: o.accepted_at for o in opportunities if o.accepted_at <= as_of}
    stage, all_entered = {}, {}
    for acct, af in account_features.items():
        entered = account_stages(
            af,
            [t for t, _ in engaged.get(acct, [])],
            recent.get(acct, []),
            [t for t, _ in pql.get(acct, [])],
            seats.get(acct),
            opp_at.get(acct),
            cfg,
            as_of,
        )
        stage[acct] = current_stage(entered)
        all_entered[acct] = entered
        entries.extend(StageEntry("account", acct, s, t) for s, t in entered.items())
    return LifecycleResult(
        account_stage=stage,
        account_entries=all_entered,
        engaged_users={a: tuple(sorted(u for _, u in v)) for a, v in engaged.items()},
        pql_users={a: tuple(sorted(u for _, u in v)) for a, v in pql.items()},
        entries=tuple(
            sorted(entries, key=lambda e: (e.entity_type, e.entity_id, e.entered_at, e.stage))
        ),
    )
