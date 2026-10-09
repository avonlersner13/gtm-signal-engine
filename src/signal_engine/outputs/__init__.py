"""Output writers: founder brief, account briefs, CRM export, alerts, funnel."""

from __future__ import annotations

from pathlib import Path

from signal_engine.config import Config
from signal_engine.models import WeekResult
from signal_engine.outputs.account_brief import brief_targets, render_account_brief
from signal_engine.outputs.alerts import render_alerts
from signal_engine.outputs.crm_export import render_crm_accounts, render_crm_contacts
from signal_engine.outputs.founder_brief import render_founder_brief
from signal_engine.outputs.funnel import render_funnel


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 text with Unix newlines (byte-identical across platforms)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_outputs(result: WeekResult, cfg: Config, out_dir: Path) -> list[Path]:
    """Write every weekly output file; return the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    accounts_dir = out_dir / "accounts"
    accounts_dir.mkdir(exist_ok=True)
    for stale in accounts_dir.glob("*.md"):
        stale.unlink()
    files = {
        out_dir / f"founder_brief_{result.week}.md": render_founder_brief(result, cfg),
        out_dir / "crm_accounts.csv": render_crm_accounts(result),
        out_dir / "crm_contacts.csv": render_crm_contacts(result),
        out_dir / "alerts.jsonl": render_alerts(result, cfg),
        out_dir / "funnel.md": render_funnel(result),
    }
    for account in brief_targets(result, cfg):
        files[accounts_dir / f"{account.slug}.md"] = render_account_brief(account, result, cfg)
    for path, text in files.items():
        write_text(path, text)
    return sorted(files)
