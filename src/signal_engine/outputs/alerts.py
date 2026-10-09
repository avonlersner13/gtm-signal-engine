"""Slack Block Kit-style alert payloads (out/alerts.jsonl). Ready for a webhook; never sent."""

from __future__ import annotations

import json
from typing import Any

from signal_engine.config import Config
from signal_engine.models import AccountView, PlayMatch, WeekResult
from signal_engine.outputs.fmt import OWNER_LABELS, money, stage_label


def alert_payload(
    a: AccountView, play: PlayMatch, result: WeekResult, cfg: Config
) -> dict[str, Any]:
    """One Block Kit payload for a high-priority trigger."""
    champion = a.committee.champion.name if a.committee.champion else "unknown"
    why = "\n".join(f"• {r}" for r in a.score.reasons[:3]) or "• fit only"
    fields = [
        ("Account", a.name),
        ("Stage", stage_label(a.stage)),
        ("Priority", f"{a.score.priority} (fit {a.score.fit}, intent {a.score.intent})"),
        ("Owner / SLA", f"{OWNER_LABELS[play.owner]} / {play.sla_hours}h"),
        ("Champion", champion),
        (
            "Est. ARR",
            money(a.revenue.total_arr) + (" + Enterprise" if a.revenue.enterprise_uplift else ""),
        ),
    ]
    return {
        "channel": cfg.org["alert_channel"],
        "text": f"{play.name}: {a.name} (priority {a.score.priority})",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"{play.name}: {a.name}"}},
            {
                "type": "section",
                "fields": [{"type": "mrkdwn", "text": f"*{k}*\n{v}"} for k, v in fields],
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Why now*\n{why}"}},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Suggested opener*\n>{play.message}"},
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"{play.action} · week {result.week} · demo payload, never sent",
                    }
                ],
            },
        ],
        "metadata": {
            "event_type": "signal_engine_play",
            "event_payload": {
                "account_id": a.account_id,
                "play_id": play.play_id,
                "week": result.week,
            },
        },
    }


def render_alerts(result: WeekResult, cfg: Config) -> str:
    """One JSON line per (account, play) at or above alert_min_priority."""
    threshold = cfg.org["alert_min_priority"]
    rows = [
        (play.priority, a.score.priority, a.account_id, alert_payload(a, play, result, cfg))
        for a in result.accounts
        for play in a.plays
        if play.priority >= threshold
    ]
    rows.sort(key=lambda r: (-r[0], -r[1], r[2]))
    return "".join(json.dumps(p, sort_keys=True, ensure_ascii=False) + "\n" for *_, p in rows)
