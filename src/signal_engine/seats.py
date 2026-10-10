"""Active-user (seat) counting per CodSpeed's documented billing rules.

An active user authored a PR on a private, CodSpeed-enabled repo, or opened the
detailed performance report, within a rolling 30-day window. ``[bot]`` accounts and
public-repo activity don't count. More than 5 active users on the Free plan starts a
14-day Pro trial; with automatic seat allocation off, new users are blocked until an
admin assigns a seat.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from signal_engine.config import Config
from signal_engine.models import Company, Event, Repo, Resolution, SeatStatus

DAY = timedelta(days=1)


@dataclass(slots=True)
class _Acc:
    """Per-company accumulator filled during the single event pass."""

    activity: dict[str, set[int]] = field(default_factory=dict)
    trial_started: datetime | None = None
    paid: bool = False
    blocked: dict[str, datetime] = field(default_factory=dict)
    seated: dict[str, datetime] = field(default_factory=dict)
    auto_off: bool = False


def empty_status(entity_id: str, free_cap: int) -> SeatStatus:
    """Seat status for an entity with no activity."""
    return SeatStatus(
        entity_id, 0, 0, 0, free_cap, False, None, None, False, None, False, False, (), False, ()
    )


def find_repo(repos: Sequence[Repo], repo_id: str) -> Repo | None:
    """Return the repo with ``repo_id``, or None if it is unknown."""
    matches = [repo for repo in repos if repo.repo_id == repo_id]
    return matches[0] if matches else None


def _counts_as_active(
    e: Event, active_events: frozenset[str], enabled: set[str], repos: Sequence[Repo]
) -> bool:
    """Private-repo PR or report view on a CodSpeed-enabled repo (or a dashboard report)."""
    if e.event_type not in active_events:
        return False
    if e.repo_id is None:
        return e.event_type == "report_viewed"
    repo = find_repo(repos, e.repo_id)
    is_private = repo is not None and repo.is_private
    return is_private and e.repo_id in enabled


def _accumulate(
    events: Iterable[Event],
    resolutions: Mapping[str, Resolution],
    repos: Sequence[Repo],
    cfg: Config,
    as_of: datetime,
) -> dict[str, _Acc]:
    """One pass over time-ordered events; repo enablement is tracked as of each event."""
    enabled: set[str] = set()
    accs: dict[str, _Acc] = {}
    for e in events:
        if e.ts > as_of:
            break
        etype = e.event_type
        if etype == "repo_imported" and e.repo_id:
            enabled.add(e.repo_id)
        elif etype == "repo_deleted" and e.repo_id:
            enabled.discard(e.repo_id)
        res = resolutions.get(e.user_id)
        if res is None or res.company_id is None:
            continue
        acc = accs.get(res.company_id)
        if acc is None:
            acc = accs[res.company_id] = _Acc()
        if not res.is_bot and _counts_as_active(e, cfg.seat_active_events, enabled, repos):
            ago = int((as_of - e.ts) / DAY)
            horizon = cfg.seat_history_days + cfg.seat_window_days
            if ago <= horizon:
                acc.activity.setdefault(e.user_id, set()).add(ago)
        elif etype == "trial_started":
            acc.trial_started = e.ts
        elif etype == "plan_upgraded":
            acc.paid = True
        elif etype == "user_blocked_no_seat" and e.ts > as_of - cfg.seat_window_days * DAY:
            acc.blocked[e.user_id] = e.ts
        elif etype == "seat_added" and e.props.get("target_user"):
            acc.seated[str(e.props["target_user"])] = e.ts
        elif etype == "auto_seat_allocation_toggled":
            acc.auto_off = not bool(e.props.get("enabled", True))
    return accs


def active_series(activity: Mapping[str, Iterable[int]], history: int, window: int) -> list[int]:
    """Active-user count for each point ``p`` days ago (0..history), rolling ``window`` days.

    A user is active at ``p`` if they had activity ``a`` days ago with p <= a < p + window.
    Built with a difference array over merged per-user intervals: O(activity + history).
    """
    diff = [0] * (history + 2)
    for days in activity.values():
        intervals: list[list[int]] = []
        for a in sorted(days):
            lo, hi = max(0, a - window + 1), min(history, a)
            if lo > hi:
                continue
            if intervals and lo <= intervals[-1][1] + 1:
                intervals[-1][1] = max(intervals[-1][1], hi)
            else:
                intervals.append([lo, hi])
        for lo, hi in intervals:
            diff[lo] += 1
            diff[hi + 1] -= 1
    series, running = [], 0
    for p in range(history + 1):
        running += diff[p]
        series.append(running)
    return series


def latest_crossing(series: Sequence[int], cap: int) -> int | None:
    """Days ago of the most recent day the count rose above ``cap`` (None if never)."""
    for p, value in enumerate(series):
        if value > cap and (p + 1 == len(series) or series[p + 1] <= cap):
            return p
    return None


def _status(cid: str, acc: _Acc, cfg: Config, as_of: datetime) -> SeatStatus:
    cap = cfg.pricing.free_cap
    series = active_series(acc.activity, cfg.seat_history_days, cfg.seat_window_days)
    crossing = latest_crossing(series, cap)
    cap_at = as_of - crossing * DAY if crossing is not None else None
    trial_start = acc.trial_started or cap_at
    days_left = None
    if trial_start is not None:
        days_left = cfg.pricing.trial_days - int((as_of - trial_start) / DAY)
    trial_active = bool(trial_start and not acc.paid and days_left is not None and days_left > 0)
    blocked = tuple(sorted(u for u, ts in acc.blocked.items() if acc.seated.get(u, ts) <= ts))
    return SeatStatus(
        entity_id=cid,
        seats_used=series[0],
        seats_7d_ago=series[7] if len(series) > 7 else 0,
        seats_30d_ago=series[30] if len(series) > 30 else 0,
        free_cap=cap,
        over_cap=series[0] > cap,
        cap_exceeded_at=cap_at,
        trial_started_at=trial_start,
        trial_active=trial_active,
        trial_days_left=days_left if trial_active else None,
        trial_expired=bool(trial_start and not acc.paid and not trial_active),
        paid=acc.paid,
        blocked_users=blocked,
        auto_allocation_off=acc.auto_off,
        active_user_ids=tuple(
            sorted(u for u, d in acc.activity.items() if min(d) < cfg.seat_window_days)
        ),
    )


def compute_seats(
    events: Iterable[Event],
    resolutions: Mapping[str, Resolution],
    repos: Sequence[Repo],
    companies: Sequence[Company],
    cfg: Config,
    as_of: datetime,
) -> dict[str, SeatStatus]:
    """Seat status per company as of ``as_of``."""
    accs = _accumulate(events, resolutions, repos, cfg, as_of)
    cap = cfg.pricing.free_cap
    return {
        c.company_id: _status(c.company_id, accs[c.company_id], cfg, as_of)
        if c.company_id in accs
        else empty_status(c.company_id, cap)
        for c in companies
    }


def rollup_seats(
    per_company: Mapping[str, SeatStatus], company_to_account: Mapping[str, str], free_cap: int
) -> dict[str, SeatStatus]:
    """Aggregate company seat status to parent accounts."""
    groups: dict[str, list[SeatStatus]] = {}
    for cid, status in sorted(per_company.items()):
        groups.setdefault(company_to_account.get(cid, cid), []).append(status)
    return {acct: _merge(acct, items, free_cap) for acct, items in groups.items()}


def _merge(acct: str, items: list[SeatStatus], free_cap: int) -> SeatStatus:
    if len(items) == 1:
        return SeatStatus(acct, *[getattr(items[0], f) for f in SeatStatus.__slots__[1:]])
    trials = [s for s in items if s.trial_active]
    soonest = min(trials, key=lambda s: s.trial_days_left or 0) if trials else None
    crossings = [s.cap_exceeded_at for s in items if s.cap_exceeded_at]
    return SeatStatus(
        entity_id=acct,
        seats_used=sum(s.seats_used for s in items),
        seats_7d_ago=sum(s.seats_7d_ago for s in items),
        seats_30d_ago=sum(s.seats_30d_ago for s in items),
        free_cap=free_cap,
        over_cap=any(s.over_cap for s in items),
        cap_exceeded_at=max(crossings) if crossings else None,
        trial_started_at=soonest.trial_started_at if soonest else None,
        trial_active=soonest is not None,
        trial_days_left=soonest.trial_days_left if soonest else None,
        trial_expired=any(s.trial_expired for s in items) and soonest is None,
        paid=any(s.paid for s in items),
        blocked_users=tuple(sorted(u for s in items for u in s.blocked_users)),
        auto_allocation_off=any(s.auto_allocation_off for s in items),
        active_user_ids=tuple(sorted(u for s in items for u in s.active_user_ids)),
    )
