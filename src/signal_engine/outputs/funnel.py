"""Funnel report (out/funnel.md): stage counts, conversion and median days between stages."""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from signal_engine.lifecycle import PREREQUISITE
from signal_engine.models import ACCOUNT_STAGES, WeekResult
from signal_engine.outputs.fmt import stage_label


@dataclass(frozen=True, slots=True)
class FunnelRow:
    """One stage of the funnel."""

    stage: str
    reached: int
    from_stage: str | None
    conversion: float | None
    median_days: float | None


def funnel_rows(result: WeekResult) -> list[FunnelRow]:
    """Accounts reaching each stage, conversion from the prerequisite, median days."""
    entries = [a.stage_entries for a in result.accounts]
    rows = []
    for stage in ACCOUNT_STAGES:
        reached = [e for e in entries if stage in e]
        prereq = PREREQUISITE.get(stage)
        base = sum(1 for e in entries if prereq in e) if prereq else None
        gaps = [(e[stage] - e[prereq]).total_seconds() / 86400 for e in reached if prereq]
        rows.append(
            FunnelRow(
                stage=stage,
                reached=len(reached),
                from_stage=prereq,
                conversion=round(len(reached) / base, 3) if base else None,
                median_days=round(statistics.median(gaps), 1) if gaps else None,
            )
        )
    return rows


def render_funnel(result: WeekResult) -> str:
    """Render the funnel as markdown."""
    total = len(result.accounts)
    lines = [
        f"# Funnel: week of {result.week}",
        "",
        f"{total} accounts tracked (including market-only accounts found by the scanner).",
        "",
        "| Stage | Accounts reached | From | Conversion | Median days from previous stage |",
        "|---|---|---|---|---|",
    ]
    for r in funnel_rows(result):
        conv = f"{r.conversion:.0%}" if r.conversion is not None else "-"
        med = f"{r.median_days}" if r.median_days is not None else "-"
        src = stage_label(r.from_stage) if r.from_stage else "-"
        lines.append(f"| {stage_label(r.stage)} | {r.reached} | {src} | {conv} | {med} |")
    churn = sum(1 for a in result.accounts if a.churn_risk)
    users_engaged = sum(len(a.engaged_users) for a in result.accounts)
    pqls = sum(len(a.pql_users) for a in result.accounts)
    ident = result.identity
    lines += [
        "",
        f"Churn-risk overlay: {churn} accounts. People: {users_engaged} engaged users,"
        f" {pqls} PQLs.",
        "",
        "## Identity resolution",
        "",
        f"{ident.people} people ({ident.bots_excluded} bots excluded). Unresolved: "
        f"{ident.unresolved_rate:.1%}. Resolved by a fallback (not corporate domain): "
        f"{ident.fallback_rate:.1%}.",
        "",
        "| Method | People |",
        "|---|---|",
        *[f"| {m} | {n} |" for m, n in ident.by_method.items()],
        "",
    ]
    return "\n".join(lines)
