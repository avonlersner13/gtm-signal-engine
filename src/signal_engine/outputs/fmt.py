"""Small formatting helpers shared by the output renderers."""

from __future__ import annotations

import re
from datetime import datetime

from signal_engine.models import AccountView, Contact

STAGE_LABELS = {
    "none": "market only",
    "signed_up": "signed up",
    "installed": "installed",
    "activated": "activated",
    "engaged": "engaged",
    "pql": "PQL",
    "pqa": "PQA",
    "opportunity": "opportunity",
}
OWNER_LABELS = {"founder": "founder", "gtm_engineer": "GTM engineer", "automated": "automated"}


def money(value: float) -> str:
    """$12,345 (no cents)."""
    return f"${value:,.0f}"


def signed(value: float) -> str:
    """+3.2 / -1.0 / 0.0."""
    return f"{value:+.1f}" if value else "0.0"


def day(ts: datetime | None) -> str:
    """ISO date in the timestamp's own timezone, or a dash."""
    return ts.date().isoformat() if ts else "-"


def stage_label(stage: str) -> str:
    """Human label for a lifecycle stage."""
    return STAGE_LABELS.get(stage, stage)


def contact_line(contact: Contact | None, fallback: str) -> str:
    """'Name (Title)' or a fallback."""
    return f"{contact.name} ({contact.title})" if contact else fallback


def first_sentences(text: str, max_chars: int = 220) -> str:
    """Leading sentences of ``text`` up to ``max_chars``."""
    sentences = re.split(r"(?<=[.?!])\s+", text.strip())
    out = ""
    for s in sentences:
        if out and len(out) + len(s) + 1 > max_chars:
            break
        out = f"{out} {s}".strip()
    return out


def md_escape(text: str) -> str:
    """Escape pipes so text is safe inside a markdown table cell."""
    return text.replace("|", "\\|")


def revenue_line(a: AccountView) -> str:
    """One-line revenue summary for briefs."""
    r = a.revenue
    parts = []
    if r.seat_arr_annual_billing:
        parts.append(
            f"{money(r.seat_arr_annual_billing)} seat ARR ({r.projected_users} users, annual; "
            f"{money(r.seat_arr_monthly_billing)} if billed monthly)"
        )
    if r.runner_arr:
        parts.append(f"{money(r.runner_arr)} runner ARR")
    if r.enterprise_uplift:
        parts.append("Enterprise uplift (custom)")
    return " + ".join(parts) if parts else "no paid upside projected yet"
