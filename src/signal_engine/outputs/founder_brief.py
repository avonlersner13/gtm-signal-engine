"""The one-page Monday brief the founder reads (out/founder_brief_<week>.md)."""

from __future__ import annotations

from collections.abc import Sequence

from signal_engine.buying_committee import UNKNOWN_BUYER
from signal_engine.config import Config
from signal_engine.models import AccountView, WeekResult
from signal_engine.outputs.fmt import (
    OWNER_LABELS,
    contact_line,
    first_sentences,
    md_escape,
    money,
    revenue_line,
    signed,
    stage_label,
)

TOP_N = 5
MOVERS_N = 3
CHURN_N = 5


def pipeline_accounts(result: WeekResult, cfg: Config) -> list[AccountView]:
    """Accounts whose primary play counts toward pipeline (plays.toml ``pipeline``)."""
    counts = {p.play_id for p in cfg.plays if p.pipeline}
    return [a for a in result.accounts if a.plays and a.plays[0].play_id in counts]


def founder_touches(result: WeekResult) -> list[AccountView]:
    """Accounts whose primary play is owned by the founder."""
    return [a for a in result.accounts if a.plays and a.plays[0].owner == "founder"]


def biggest_movers(accounts: Sequence[AccountView], n: int = MOVERS_N) -> list[AccountView]:
    """Accounts with the largest absolute week-over-week priority change."""
    moved = [a for a in accounts if a.score.priority_delta]
    return sorted(moved, key=lambda a: (-abs(a.score.priority_delta), a.account_id))[:n]


def _headline(result: WeekResult, cfg: Config) -> list[str]:
    pipe = pipeline_accounts(result, cfg)
    total = sum(a.revenue.total_arr for a in pipe)
    enterprise = sum(1 for a in pipe if a.revenue.enterprise_uplift)
    new_pqas = [a for a in result.accounts if a.new_pqa]
    founder = founder_touches(result)
    lines = [
        "## Headline",
        "",
        "| New PQAs this week | Pipeline estimate (ARR) | Founder touches this week | PQAs total |",
        "|---|---|---|---|",
        f"| {len(new_pqas)} | {money(total)} across {len(pipe)} accounts"
        f" (+{enterprise} Enterprise flags) | {len(founder)} |"
        f" {sum(a.pqa for a in result.accounts)} |",
        "",
    ]
    if new_pqas:
        lines.append("New PQAs: " + ", ".join(a.name for a in new_pqas[:8]) + ".")
        lines.append("")
    capacity = cfg.org["founder_weekly_capacity"]
    if founder:
        names = ", ".join(f"{a.name} ({a.plays[0].name.lower()})" for a in founder[:capacity])
        more = (
            f", plus {len(founder) - capacity} more in crm_accounts.csv"
            if len(founder) > capacity
            else ""
        )
        lines += [f"Founder touches, in priority order: {names}{more}.", ""]
    return lines


def _top_account(rank: int, a: AccountView) -> list[str]:
    play = a.plays[0] if a.plays else None
    others = ", ".join(p.name for p in a.plays[1:])
    play_line = (
        f"{play.name} ({OWNER_LABELS[play.owner]}, SLA {play.sla_hours}h)"
        + (f". Also: {others}" if others else "")
        if play
        else "no play triggered"
    )
    buyer = contact_line(a.committee.economic_buyer, UNKNOWN_BUYER)
    return [
        f"### {rank}. {a.name} · {stage_label(a.stage)} · priority {a.score.priority}"
        f" ({signed(a.score.priority_delta)})",
        f"- **Why now:** {'; '.join(a.score.reasons[:3]) or 'fit only'}",
        f"- **Play:** {play_line}",
        f"- **Champion:** {contact_line(a.committee.champion, 'none identified')}"
        f" · economic buyer: {buyer}",
        f'- **Opener:** "{first_sentences(play.message)}"' if play else "- **Opener:** -",
        f"- **Revenue:** {revenue_line(a)}",
        "",
    ]


def _movers(result: WeekResult) -> list[str]:
    lines = ["## Biggest movers", ""]
    movers = biggest_movers(result.accounts)
    if not movers:
        return [
            *lines,
            "No prior week in history yet: run two consecutive weeks to see movers.",
            "",
        ]
    lines += [
        "| Account | Priority | Change | Intent change | Top reason |",
        "|---|---|---|---|---|",
    ]
    for a in movers:
        reason = md_escape(a.score.reasons[0]) if a.score.reasons else "-"
        lines.append(
            f"| {a.name} | {a.score.priority} | {signed(a.score.priority_delta)} |"
            f" {signed(a.score.intent_delta)} | {reason} |"
        )
    return [*lines, ""]


def _churn(result: WeekResult) -> list[str]:
    risks = [a for a in result.accounts if a.churn_risk]
    lines = ["## Churn risks", ""]
    if not risks:
        return [*lines, "None this week.", ""]
    for a in risks[:CHURN_N]:
        champion = a.committee.champion.name if a.committee.champion else "no champion"
        lines.append(
            f"- **{a.name}** ({stage_label(a.stage)}, {champion}): {', '.join(a.churn_signals)}"
        )
    if len(risks) > CHURN_N:
        lines.append(f"- ...and {len(risks) - CHURN_N} more (see crm_accounts.csv)")
    return [*lines, ""]


def product_feedback(result: WeekResult) -> str:
    """One concrete insight about where activation stalls."""
    feats = [a.features for a in result.accounts if a.features]
    installed = [f for f in feats if "github_app_installed" in f.first_ts]
    no_baseline = [f for f in installed if "baseline_created" not in f.first_ts]
    no_setup = sum(1 for f in no_baseline if "setup_started" not in f.first_ts)
    wizard_stuck = sum(
        1
        for f in no_baseline
        if "wizard_pr_opened" in f.first_ts and "wizard_pr_merged" not in f.first_ts
    )
    unsupported = sum(1 for a in result.accounts if a.scan.not_addressable_only)
    if not installed:
        return "Not enough installs yet to say where activation stalls."
    share = round(100 * len(no_baseline) / len(installed))
    return (
        f"{len(no_baseline)} of {len(installed)} installed accounts ({share}%) never created a "
        f"baseline. {no_setup} of them never opened Setup after installing the GitHub App, and "
        f"{wizard_stuck} have a Wizard PR that was never merged, so the drop is between install "
        f"and Setup, not in CI. Separately, {unsupported} market accounts benchmark only with "
        "sbt-jmh (Scala), which isn't supported yet."
    )


def render_founder_brief(result: WeekResult, cfg: Config) -> str:
    """Render the founder brief as markdown."""
    lines = [
        f"# Monday pipeline brief: week of {result.week}",
        "",
        f"For {cfg.founder_name}. As of {result.as_of.isoformat()}. Synthetic demo data; every "
        "signal name and weight is a hypothesis.",
        "",
        *_headline(result, cfg),
        "## Top 5 accounts",
        "",
    ]
    for rank, account in enumerate(result.accounts[:TOP_N], start=1):
        lines += _top_account(rank, account)
    lines += _movers(result)
    lines += _churn(result)
    lines += ["## Product feedback", "", product_feedback(result), ""]
    return "\n".join(lines)
