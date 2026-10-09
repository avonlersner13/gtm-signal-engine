"""Per-account brief (out/accounts/<slug>.md)."""

from __future__ import annotations

from datetime import datetime

from signal_engine.buying_committee import UNKNOWN_BUYER, first_name
from signal_engine.config import Config
from signal_engine.models import ACCOUNT_STAGES, AccountView, WeekResult
from signal_engine.outputs.fmt import (
    contact_line,
    day,
    md_escape,
    money,
    owner_label,
    signed,
    stage_label,
)
from signal_engine.scoring import plural

MAX_EMAIL_WORDS = 120
MILESTONES_FIRST = (
    ("signed_up", "First signup"),
    ("github_app_installed", "GitHub App installed"),
    ("setup_started", "Setup started"),
    ("wizard_pr_merged", "Wizard setup PR merged"),
    ("workflow_committed", "CI workflow committed"),
    ("baseline_created", "Baseline created"),
    ("first_pr_report", "First PR report"),
    ("required_check_enabled", "Required check enabled"),
    ("mcp_connected", "MCP server connected"),
    ("free_limit_exceeded", "Free 5-user limit exceeded"),
    ("trial_started", "Pro trial started"),
    ("plan_upgraded", "Upgraded to a paid plan"),
)
MILESTONES_LAST = (
    ("auto_seat_allocation_toggled", "Seat auto-allocation toggled"),
    ("user_blocked_no_seat", "User blocked without a seat (latest)"),
    ("ryzen_requested", "Asked about the Ryzen runner"),
    ("runner_budget_set", "Runner budget set"),
    ("demo_requested", "Demo requested"),
    ("trust_center_visit", "Trust Center visit (latest)"),
    ("soc2_report_requested", "SOC 2 report requested"),
    ("sso_page_view", "SSO page viewed"),
    ("codspeedbot_fix_pr_merged", "@codspeedbot fix PR merged (latest)"),
    ("informational_check_enabled", "Check switched to informational"),
    ("wizard_disabled", "Wizard disabled"),
    ("seat_removed", "Seat removed (latest)"),
)
TIMELINE_MAX = 14


def brief_targets(result: WeekResult, cfg: Config) -> list[AccountView]:
    """Top-N accounts by priority plus every account with a founder-owned primary play."""
    top_n = cfg.org["account_briefs_top_n"]
    chosen = {a.account_id for a in result.accounts[:top_n]}
    chosen |= {a.account_id for a in result.accounts if a.plays and a.plays[0].owner == "founder"}
    return [a for a in result.accounts if a.account_id in chosen]


def timeline(a: AccountView) -> list[tuple[datetime, str]]:
    """Key events (first occurrence of milestones, latest of recurring signals)."""
    f = a.features
    if f is None:
        return []
    items = [(f.first_ts[e], label) for e, label in MILESTONES_FIRST if e in f.first_ts]
    items += [(f.last_ts[e], label) for e, label in MILESTONES_LAST if e in f.last_ts]
    return sorted(items)[-TIMELINE_MAX:]


def suggested_email(a: AccountView, cfg: Config) -> tuple[str, str]:
    """(subject, body) for a first email, body at most 120 words."""
    play = a.plays[0] if a.plays else None
    greeting = f"Hi {first_name(a.committee.champion)},"
    if play is None:
        body = (
            f"I'm {cfg.founder_name}, one of the people building CodSpeed. "
            "I saw your team trying it "
            "out and wanted to ask what you're hoping to catch with performance checks. Anything "
            "slowing you down?"
        )
    elif play.message.startswith("Hi ") and ", " in play.message:
        greeting, body = play.message.split(", ", 1)
        greeting += ","
        body = body[:1].upper() + body[1:]
    else:
        body = play.message
    budget = MAX_EMAIL_WORDS - len(greeting.split()) - len(cfg.founder_name.split())
    words = body.split()
    if len(words) > budget:
        body = " ".join(words[:budget]).rstrip(",.;:") + "..."
    subject = f"{a.name} x CodSpeed" + (f": {play.name.lower()}" if play else "")
    return subject, f"{greeting}\n\n{body}\n\n{cfg.founder_name}"


def _header(a: AccountView) -> list[str]:
    s = a.score
    subs = f" · subsidiaries: {', '.join(a.child_names)}" if a.child_names else ""
    return [
        f"# {a.name}",
        "",
        f"`{a.account_id}` · {a.domain} · {a.industry.replace('_', ' ')} · {a.region} · "
        f"~{a.employee_count} employees / {a.eng_headcount} engineers{subs}",
        "",
        f"**Stage:** {stage_label(a.stage)}"
        + (" (churn-risk overlay)" if a.churn_risk else "")
        + f" · **Priority** {s.priority} ({signed(s.priority_delta)}) · **Fit** {s.fit}"
        f" · **Intent** {s.intent} ({signed(s.intent_delta)})",
        "",
        "## Why now",
        "",
        *[f"- {r}" for r in s.reasons],
        *([] if s.reasons else ["- No intent signals yet."]),
        "",
        "## Fit",
        "",
        *[f"- {r}" for r in s.fit_reasons],
        "",
    ]


def _seats(a: AccountView, cfg: Config) -> list[str]:
    st, f = a.seats, a.features
    trial = (
        f"active, ends in {plural(st.trial_days_left, 'day')}"
        if st.trial_active and st.trial_days_left is not None
        else "paid plan"
        if st.paid
        else "expired"
        if st.trial_expired
        else "none"
    )
    lines = [
        "## Seats vs cap",
        "",
        "| Active users (30d) | Free cap | 7 days ago | 30 days ago | Trial | Blocked users "
        "| Auto-allocation |",
        "|---|---|---|---|---|---|---|",
        f"| {st.seats_used} | {st.free_cap} | {st.seats_7d_ago} | {st.seats_30d_ago} | {trial} |"
        f" {len(st.blocked_users)} | {'off' if st.auto_allocation_off else 'on'} |",
        "",
        "## Runner minutes (month to date)",
        "",
    ]
    if f is None or not (f.graviton_minutes_mtd or f.ryzen_minutes_mtd):
        return [*lines, "No macro-runner usage this month.", ""]
    free = cfg.pricing.graviton_free_minutes
    return [
        *lines,
        f"Graviton {f.graviton_minutes_mtd} min "
        f"(projected {f.projected_graviton_minutes} vs {free} free)"
        f" · Ryzen {f.ryzen_minutes_mtd} min",
        "",
    ]


def _history(a: AccountView, result: WeekResult) -> list[str]:
    lines = ["## Stage history", "", "| Stage | Entered |", "|---|---|"]
    lines += [
        f"| {stage_label(s)} | {day(a.stage_entries[s])} |"
        for s in ACCOUNT_STAGES
        if s in a.stage_entries
    ]
    if a.churn_risk:
        lines.append(f"| churn risk (overlay) | {day(result.as_of)} |")
    lines += ["", "## Timeline of key events", ""]
    events = timeline(a)
    lines += [f"- {day(ts)}: {label}" for ts, label in events] or ["- No product events yet."]
    return [*lines, ""]


def _committee(a: AccountView) -> list[str]:
    c = a.committee
    return [
        "## Buying committee",
        "",
        f"- **Champion:** {contact_line(c.champion, 'none identified yet')}",
        f"- **Technical evaluator:** {contact_line(c.technical_evaluator, 'not seen yet')}",
        f"- **Economic buyer:** {contact_line(c.economic_buyer, UNKNOWN_BUYER)}",
        f"- Known people: {len(a.people)} · engaged: {len(a.engaged_users)}"
        f" · PQLs: {len(a.pql_users)}",
        "",
    ]


def _plays(a: AccountView) -> list[str]:
    lines = ["## Plays", ""]
    if not a.plays:
        return [*lines, "No play triggered this week.", ""]
    for i, p in enumerate(a.plays):
        kind = "Primary" if i == 0 else "Secondary"
        lines += [
            f"{i + 1}. **{kind}: {p.name}** ({owner_label(p)}, SLA {p.sla_hours}h). {p.action}",
            f"   > {md_escape(p.message)}",
        ]
    return [*lines, ""]


def _revenue(a: AccountView) -> list[str]:
    r = a.revenue
    return [
        "## Revenue estimate",
        "",
        f"- Seat ARR: {money(r.seat_arr_annual_billing)} billed annually"
        f" ({money(r.seat_arr_monthly_billing)} billed monthly)"
        f" for {r.projected_users} projected users",
        f"- Runner ARR: {money(r.runner_arr)}"
        f" ({r.projected_overage_minutes} projected overage min/month)",
        f"- Enterprise uplift: {'yes (custom pricing)' if r.enterprise_uplift else 'no'}",
        "",
        "Assumptions:",
        *[f"- {x}" for x in r.assumptions],
        "",
    ]


def render_account_brief(a: AccountView, result: WeekResult, cfg: Config) -> str:
    """Render one account brief as markdown."""
    subject, body = suggested_email(a, cfg)
    evidence = [f"- {e}" for e in a.scan.evidence] or ["- No repos scanned."]
    lines = [
        *_header(a),
        *_seats(a, cfg),
        *_history(a, result),
        *_committee(a),
        *_plays(a),
        "## Suggested first email",
        "",
        f"**Subject:** {subject}",
        "",
        body,
        "",
        "## Scanner evidence",
        "",
        *evidence,
        "",
        *_revenue(a),
    ]
    return "\n".join(lines)
