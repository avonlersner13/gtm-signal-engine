"""Fit (0-100) and intent (0-100) scores, combined priority, and specific reasons.

Every weight, cap, band and threshold comes from config/signals.toml. Each rule that
fires adds a reason with real numbers ("6 active users, 5-seat cap exceeded, trial ends
in 4 days"), never a vague label.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from signal_engine.config import Config
from signal_engine.models import AccountFeatures, Company, ScanSummary, Score, SeatStatus

PLACEHOLDER_RE = re.compile(r"\{(n7|n14|n30|n90)\}")
# "1 <up to two words> <lowercase noun|PR>s" -> singular; capitalized words (names) never match
ONE_PLURAL_RE = re.compile(r"\b1 ((?:\S+ ){0,2}?)([a-z]+?|PR)s\b")
LANGUAGE_LABELS = {
    "rust": "Rust",
    "cpp": "C++",
    "python": "Python",
    "node": "Node/TS",
    "go": "Go",
    "jvm": "JVM",
    "scala": "Scala",
}
MAX_REASONS = 6


@dataclass(frozen=True, slots=True)
class AccountInputs:
    """Everything scoring needs about one account."""

    account_id: str
    family: Sequence[Company]
    features: AccountFeatures | None
    seats: SeatStatus
    scan: ScanSummary
    private_repo_ratio: float | None
    engaged_users: int
    orgs_active: int


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Clamp ``value`` to [lo, hi]."""
    return max(lo, min(hi, value))


def band_points(value: float, bands: Sequence[tuple[float, float]]) -> float:
    """Points of the highest band whose minimum is <= value."""
    points = 0.0
    for minimum, pts in bands:
        if value >= minimum:
            points = pts
    return points


def fit_score(inputs: AccountInputs, cfg: Config) -> tuple[float, tuple[str, ...]]:
    """Firmographic + technographic fit, with reasons."""
    fit = cfg.fit
    root = inputs.family[0]
    eng = sum(c.eng_headcount for c in inputs.family)
    employees = sum(c.employee_count for c in inputs.family)
    parts: list[tuple[float, str]] = [
        (band_points(eng, fit["eng_bands"]), f"~{eng} engineers"),
    ]
    langs = [lang for lang in inputs.scan.languages if lang in fit["language_points"]]
    if langs:
        best = max(langs, key=lambda lang: (fit["language_points"][lang], lang))
        pts = min(fit["language_max"], fit["language_points"][best])
        label = "/".join(LANGUAGE_LABELS.get(lang, lang) for lang in langs)
        walltime = sorted(set(langs) & fit["walltime_only"])
        note = " (walltime-only: macro runners)" if walltime else ""
        parts.append((pts, f"{label} stack{note}"))
    if inputs.private_repo_ratio is not None:
        ratio = inputs.private_repo_ratio
        parts.append((ratio * fit["private_max"], f"{round(ratio * 100)}% private repos"))
    industry = fit["industry_points"].get(root.industry, fit["industry_default"])
    parts.append((industry, f"{root.industry.replace('_', ' ')} industry"))
    if len(inputs.family) > 1:
        parts.append(
            (band_points(employees, fit["parent_bands"]), f"parent account ~{employees} employees")
        )
    market = inputs.scan.repos_benchmarks_without_codspeed
    if market:
        pts = min(fit["market_cap"], market * fit["market_per_repo"])
        parts.append((pts, f"{market} repos already benchmark without CodSpeed"))
    total = clamp(sum(p for p, _ in parts))
    reasons = tuple(text for pts, text in sorted(parts, key=lambda x: (-x[0], x[1])) if pts > 0)
    return round(total, 1), reasons


def event_contributions(features: AccountFeatures, cfg: Config) -> list[tuple[float, str | None]]:
    """(points, reason) for every event-backed signal with non-zero decayed count."""
    out: list[tuple[float, str | None]] = []
    for event in sorted(features.decayed):
        rule = cfg.signals.get(event)
        if rule is None:
            continue
        raw = rule.weight * features.decayed[event]
        points = min(raw, rule.cap) if rule.cap >= 0 else max(raw, rule.cap)
        counts = {f"n{w}": features.counts.get(w, {}).get(event, 0) for w in cfg.windows}
        needed = PLACEHOLDER_RE.findall(rule.reason)
        if any(counts.get(n, 0) == 0 for n in needed):
            reason = None
        else:
            fields = {n: counts.get(n, 0) for n in ("n7", "n14", "n30", "n90")}
            reason = singularize_ones(rule.reason.format(**fields))
        out.append((points, reason))
    return out


def singularize_ones(text: str) -> str:
    """Fix template grammar for a count of one: '1 regressions caught' -> '1 regression caught'."""
    return ONE_PLURAL_RE.sub(r"1 \1\2", text)


def plural(n: int, word: str) -> str:
    """'1 day', '4 days'."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _trial_suffix(seats: SeatStatus) -> str:
    if seats.trial_active and seats.trial_days_left is not None:
        return f", trial ends in {plural(seats.trial_days_left, 'day')}"
    if seats.paid:
        return ", on a paid plan"
    if seats.trial_expired:
        return ", trial expired"
    return ""


def derived_contributions(inputs: AccountInputs, cfg: Config) -> list[tuple[float, str | None]]:
    """(points, reason) for derived signals: seats, runners, spread, market, run drop."""
    d, seats, f = cfg.derived, inputs.seats, inputs.features
    cap, free_minutes = seats.free_cap, cfg.pricing.graviton_free_minutes
    out: list[tuple[float, str | None]] = []
    if seats.over_cap:
        fields = {
            "seats_used": seats.seats_used,
            "free_cap": cap,
            "trial_suffix": _trial_suffix(seats),
        }
        out.append((d["over_cap"]["points"], d["over_cap"]["reason"].format(**fields)))
    if seats.trial_active:
        out.append((d["trial_active"]["points"], None))
    if not seats.over_cap and seats.seats_used >= cap - 1 and seats.seats_used > seats.seats_7d_ago:
        fields = {
            "seats_used": seats.seats_used,
            "free_cap": cap,
            "seats_7d_ago": seats.seats_7d_ago,
        }
        out.append((d["near_cap"]["points"], d["near_cap"]["reason"].format(**fields)))
    if seats.blocked_users:
        n = len(seats.blocked_users)
        rule = d["blocked_users"]
        text = rule["reason"].format(blocked=n, blocked_engineers=plural(n, "engineer"))
        out.append((min(rule["cap"], n * rule["per_unit"]), text))
    if f is not None and f.projected_graviton_minutes > free_minutes:
        fields = {
            "projected_minutes": round(f.projected_graviton_minutes),
            "free_minutes": free_minutes,
        }
        out.append((d["runner_overage"]["points"], d["runner_overage"]["reason"].format(**fields)))
    if inputs.orgs_active >= 2:
        rule = d["multi_org"]
        pts = min(rule["cap"], inputs.orgs_active * rule["per_unit"])
        out.append((pts, rule["reason"].format(orgs=inputs.orgs_active)))
    if inputs.engaged_users:
        rule = d["engaged_users"]
        pts = min(rule["cap"], inputs.engaged_users * rule["per_unit"])
        out.append((pts, rule["reason"].format(engaged=inputs.engaged_users)))
    market = inputs.scan.repos_benchmarks_without_codspeed
    if market:
        rule = d["market_benchmarks"]
        frameworks = ", ".join(inputs.scan.frameworks) or "other tools"
        out.append(
            (
                min(rule["cap"], market * rule["per_unit"]),
                rule["reason"].format(repos=market, frameworks=frameworks),
            )
        )
    if f is not None and run_dropped(f, d["run_drop"]):
        pct = round(100 * (1 - f.runs_30 / f.runs_prev_30))
        fields = {"drop_pct": pct, "runs_30": f.runs_30, "runs_prev_30": f.runs_prev_30}
        out.append((d["run_drop"]["points"], d["run_drop"]["reason"].format(**fields)))
    return out


def run_dropped(f: AccountFeatures, rule: Mapping[str, float]) -> bool:
    """True if runs fell by more than drop_pct vs the prior 30 days."""
    prior = f.runs_prev_30
    if prior < rule["min_prior_runs"]:
        return False
    return f.runs_30 < prior * (1 - rule["drop_pct"] / 100.0)


def intent_score(inputs: AccountInputs, cfg: Config) -> tuple[float, tuple[str, ...]]:
    """Usage/intent score with recency decay, negative signals and clamping."""
    parts = derived_contributions(inputs, cfg)
    if inputs.features is not None:
        parts = event_contributions(inputs.features, cfg) + parts
    raw = sum(p for p, _ in parts)
    score = clamp(100.0 * raw / cfg.intent_scale)
    ranked = sorted(
        ((p, r) for p, r in parts if r and abs(p) >= 0.5), key=lambda x: (-abs(x[0]), x[1])
    )
    return round(score, 1), tuple(r for _, r in ranked[:MAX_REASONS])


def priority_score(fit: float, intent: float, intent_delta: float, cfg: Config) -> float:
    """Weighted blend of fit and intent plus a capped momentum bonus."""
    momentum = clamp(intent_delta * cfg.momentum_per_point, -cfg.momentum_cap, cfg.momentum_cap)
    return round(clamp(cfg.fit_weight * fit + cfg.intent_weight * intent + momentum), 1)


def score_account(inputs: AccountInputs, cfg: Config, week: str, previous: Score | None) -> Score:
    """Score one account; deltas are against last week's stored score (0 if none)."""
    fit, fit_reasons = fit_score(inputs, cfg)
    intent, reasons = intent_score(inputs, cfg)
    delta = round(intent - previous.intent, 1) if previous else 0.0
    priority = priority_score(fit, intent, delta, cfg)
    p_delta = round(priority - previous.priority, 1) if previous else 0.0
    return Score(
        inputs.account_id, week, fit, intent, priority, delta, p_delta, reasons, fit_reasons
    )
