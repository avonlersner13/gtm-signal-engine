"""Trigger detection and play matching (play library in config/plays.toml).

Each trigger is a small function of the account context and the play's ``params``.
Plays are ranked by configured priority: the best match is the primary play, the next
two are secondary. Templates are filled with the account's real numbers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from signal_engine.buying_committee import first_name
from signal_engine.config import Config, PlayDef
from signal_engine.models import (
    AccountFeatures,
    AccountView,
    BuyingCommittee,
    PlayMatch,
    RevenueEstimate,
    ScanSummary,
    SeatStatus,
)
from signal_engine.revenue import SECURITY_EVENTS
from signal_engine.scoring import LANGUAGE_LABELS, plural, singularize_ones

MAX_PLAYS = 3
OVER_FOUNDER_CAPACITY = "over founder capacity"
SECURITY_LABELS = {
    "trust_center_visit": "the Trust Center",
    "soc2_report_requested": "our SOC 2 report",
    "sso_page_view": "SSO/SAML",
    "security_docs_visit": "the security docs",
}


@dataclass(frozen=True, slots=True)
class PlayContext:
    """What triggers and templates can see about one account."""

    name: str
    stage: str
    fit: float
    seats: SeatStatus
    scan: ScanSummary
    features: AccountFeatures | None
    committee: BuyingCommittee
    revenue: RevenueEstimate
    orgs_active: int
    as_of: datetime
    founder: str
    free_minutes: int


def _recent(f: AccountFeatures | None, event: str, as_of: datetime, days: float) -> bool:
    if f is None:
        return False
    last = f.last_ts.get(event)
    return last is not None and last > as_of - timedelta(days=days)


def _count(f: AccountFeatures | None, event: str, window: int = 30) -> int:
    return f.counts.get(window, {}).get(event, 0) if f else 0


def churn_signals(ctx: PlayContext, p: Mapping[str, float]) -> list[str]:
    """Human-readable churn signs (empty if healthy)."""
    f, out = ctx.features, []
    days = p.get("window_days", 30)
    ignored = _count(f, "benchmark_ignored")
    if ignored >= p.get("min_ignored", 2):
        out.append(f"{ignored} benchmarks ignored")
    for event, label in (
        ("informational_check_enabled", "check switched to informational"),
        ("wizard_disabled", "Wizard disabled"),
        ("seat_removed", "seats removed"),
    ):
        if _recent(f, event, ctx.as_of, days):
            out.append(label)
    if f is not None and f.runs_prev_30 >= p.get("min_prior_runs", 10):
        drop = 100 * (1 - f.runs_30 / f.runs_prev_30)
        if drop > p.get("drop_pct", 50.0):
            out.append(f"runs down {round(drop)}%")
    return out


def _trial_ending(ctx: PlayContext, p: Mapping[str, float]) -> bool:
    s = ctx.seats
    return (
        s.trial_active and s.trial_days_left is not None and s.trial_days_left <= p["max_days_left"]
    )


def _blocked_users(ctx: PlayContext, p: Mapping[str, float]) -> bool:
    return len(ctx.seats.blocked_users) >= p["min_blocked"]


def _cap_approaching(ctx: PlayContext, p: Mapping[str, float]) -> bool:
    s = ctx.seats
    near = s.free_cap - p["min_seats_below_cap"] <= s.seats_used <= s.free_cap
    return near and s.seats_used > s.seats_7d_ago and not s.paid and not s.over_cap


def _multi_team_spread(ctx: PlayContext, p: Mapping[str, float]) -> bool:
    return ctx.orgs_active >= p["min_orgs"]


def _runner_overage(ctx: PlayContext, p: Mapping[str, float]) -> bool:
    f, days = ctx.features, p["lookback_days"]
    if f is None:
        return False
    return (
        ctx.revenue.projected_overage_minutes > 0
        or _recent(f, "ryzen_requested", ctx.as_of, days)
        or _recent(f, "runner_budget_set", ctx.as_of, days)
    )


def _walltime_only_stack(ctx: PlayContext, p: Mapping[str, float]) -> bool:
    f = ctx.features
    activated = ctx.stage not in ("none", "signed_up", "installed")
    walltime_runs = f.instruments_30.get("walltime", 0) if f else 0
    return ctx.scan.walltime_only and activated and walltime_runs >= p["min_walltime_runs"]


def _security_review(ctx: PlayContext, p: Mapping[str, float]) -> bool:
    return any(_recent(ctx.features, e, ctx.as_of, p["window_days"]) for e in SECURITY_EVENTS)


def _ai_native(ctx: PlayContext, p: Mapping[str, float]) -> bool:
    f = ctx.features
    connected = f is not None and "mcp_connected" in f.first_ts
    return connected and _count(f, "codspeedbot_fix_pr_merged", 90) >= p["min_fix_prs_merged"]


def _activation_rescue(ctx: PlayContext, p: Mapping[str, float]) -> bool:
    f = ctx.features
    if f is None or "github_app_installed" not in f.first_ts or "baseline_created" in f.first_ts:
        return False
    return days_since(f.first_ts["github_app_installed"], ctx.as_of) >= p["min_days_since_install"]


def _churn_risk(ctx: PlayContext, p: Mapping[str, float]) -> bool:
    return bool(churn_signals(ctx, p))


def _outbound(ctx: PlayContext, p: Mapping[str, float]) -> bool:
    market = ctx.features is None and ctx.scan.repos_benchmarks_without_codspeed > 0
    return market and ctx.fit >= p["min_fit"]


def _not_addressable(ctx: PlayContext, p: Mapping[str, float]) -> bool:
    return ctx.scan.not_addressable_only


TRIGGERS: dict[str, Callable[[PlayContext, Mapping[str, float]], bool]] = {
    "trial_ending": _trial_ending,
    "blocked_users": _blocked_users,
    "cap_approaching": _cap_approaching,
    "multi_team_spread": _multi_team_spread,
    "runner_overage": _runner_overage,
    "walltime_only_stack": _walltime_only_stack,
    "security_review": _security_review,
    "ai_native_power_user": _ai_native,
    "activation_rescue": _activation_rescue,
    "churn_risk": _churn_risk,
    "outbound": _outbound,
    "not_addressable": _not_addressable,
}


def days_since(ts: datetime, as_of: datetime) -> int:
    """Whole days between ``ts`` and ``as_of``."""
    return int((as_of - ts) / timedelta(days=1))


class _SafeDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def template_fields(ctx: PlayContext, play: PlayDef) -> dict[str, Any]:
    """Real numbers from the account for message templates."""
    f, s, c = ctx.features, ctx.seats, ctx.committee
    security = [SECURITY_LABELS[e] for e in SECURITY_EVENTS if _recent(f, e, ctx.as_of, 30)]
    walltime_langs = [LANGUAGE_LABELS[lang] for lang in ctx.scan.languages if lang in ("go", "jvm")]
    installed = f.first_ts.get("github_app_installed") if f else None
    return {
        "account": ctx.name,
        "champion": c.champion.name if c.champion else "the champion",
        "champion_first": first_name(c.champion),
        "buyer": first_name(c.economic_buyer, first_name(c.champion)),
        "founder": ctx.founder,
        "seats_used": s.seats_used,
        "free_cap": s.free_cap,
        "seats_7d_ago": s.seats_7d_ago,
        "trial_days_left": s.trial_days_left if s.trial_days_left is not None else "a few",
        "trial_ends": f"in {plural(s.trial_days_left, 'day')}" if s.trial_days_left else "soon",
        "blocked": len(s.blocked_users),
        "blocked_engineers": plural(len(s.blocked_users), "engineer"),
        "blocked_verb": "is" if len(s.blocked_users) == 1 else "are",
        "projected_minutes": round(f.projected_graviton_minutes) if f else 0,
        "free_minutes": ctx.free_minutes,
        "frameworks": ", ".join(ctx.scan.frameworks) or "your current tooling",
        "languages": "/".join(walltime_langs) or "Go/JVM",
        "orgs": ctx.orgs_active,
        "days_since_install": days_since(installed, ctx.as_of) if installed else 0,
        "security_signals": " and ".join(security) or "our security material",
        "fix_prs": _count(f, "codspeedbot_fix_pr_merged", 90),
        "mcp_calls": _count(f, "mcp_tool_call"),
        "regressions_30": _count(f, "regression_detected"),
        "runs_30": f.runs_30 if f else 0,
        "est_arr": f"${ctx.revenue.total_arr:,.0f}",
        "churn_signals": ", ".join(churn_signals(ctx, play.params)) or "a drop in activity",
    }


def match_plays(ctx: PlayContext, cfg: Config) -> tuple[PlayMatch, ...]:
    """Primary play plus up to two secondary plays, highest priority first."""
    out: list[PlayMatch] = []
    for play in cfg.plays:
        if not TRIGGERS[play.trigger](ctx, play.params):
            continue
        message = singularize_ones(play.template.format_map(_SafeDict(template_fields(ctx, play))))
        out.append(
            PlayMatch(
                play.play_id,
                play.name,
                play.owner,
                play.sla_hours,
                play.priority,
                play.action,
                message,
            )
        )
        if len(out) == MAX_PLAYS:
            break
    return tuple(out)


def founder_eligible(accounts: Sequence[AccountView]) -> list[AccountView]:
    """Accounts whose primary play is founder-owned, best first.

    Ranked by play priority, then account priority (ties broken by account id).
    """
    eligible = [a for a in accounts if a.plays and a.plays[0].owner == "founder"]
    return sorted(eligible, key=lambda a: (-a.plays[0].priority, -a.score.priority, a.account_id))


def apply_founder_capacity(accounts: Sequence[AccountView], capacity: int) -> list[AccountView]:
    """Keep the top ``capacity`` founder-owned accounts; queue the rest for the GTM engineer.

    Overflow accounts keep their plays, but every founder-owned play on them moves to
    ``gtm_engineer`` with the note "over founder capacity". Account order is unchanged.
    """
    overflow = {a.account_id for a in founder_eligible(accounts)[capacity:]}
    return [_queue_for_gtm(a) if a.account_id in overflow else a for a in accounts]


def _queue_for_gtm(account: AccountView) -> AccountView:
    plays = tuple(
        replace(p, owner="gtm_engineer", note=OVER_FOUNDER_CAPACITY) if p.owner == "founder" else p
        for p in account.plays
    )
    return replace(account, plays=plays)
