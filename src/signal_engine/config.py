"""Load and validate config/signals.toml, config/plays.toml and config/pricing.toml."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from signal_engine.models import EVENT_TYPES, PERSONAS

KNOWN_TRIGGERS: frozenset[str] = frozenset(
    {
        "trial_ending",
        "blocked_users",
        "cap_approaching",
        "multi_team_spread",
        "runner_overage",
        "walltime_only_stack",
        "security_review",
        "ai_native_power_user",
        "activation_rescue",
        "churn_risk",
        "outbound",
        "not_addressable",
    }
)
OWNERS: frozenset[str] = frozenset({"founder", "gtm_engineer", "automated"})
DERIVED_SIGNALS: frozenset[str] = frozenset(
    {
        "over_cap",
        "trial_active",
        "near_cap",
        "blocked_users",
        "runner_overage",
        "multi_org",
        "engaged_users",
        "market_benchmarks",
        "run_drop",
    }
)


class ConfigError(ValueError):
    """Raised when a config file is missing, malformed or fails validation."""


@dataclass(frozen=True, slots=True)
class SignalRule:
    """One weighted intent signal backed by an event type."""

    event: str
    weight: float
    cap: float
    reason: str


@dataclass(frozen=True, slots=True)
class PlayDef:
    """One play from the play library."""

    play_id: str
    name: str
    trigger: str
    priority: int
    owner: str
    sla_hours: int
    pipeline: bool
    params: Mapping[str, float]
    action: str
    success_metric: str
    template: str


@dataclass(frozen=True, slots=True)
class Pricing:
    """Documented prices plus revenue-projection assumptions."""

    free_cap: int
    pro_annual: float
    pro_monthly: float
    trial_days: int
    graviton_free_minutes: int
    graviton_rate: float
    ryzen_rate: float
    enterprise_features: tuple[str, ...]
    growth_lookback_days: int
    outbound_adoption_rate: float
    ryzen_share_if_requested: float


@dataclass(frozen=True, slots=True)
class Config:
    """Validated configuration for one pipeline run."""

    now: datetime
    seed: int
    half_life_days: float
    windows: tuple[int, ...]
    fit_weight: float
    intent_weight: float
    momentum_per_point: float
    momentum_cap: float
    identity: Mapping[str, float]
    bot_suffix: str
    freemail: frozenset[str]
    persona_order: tuple[tuple[str, tuple[str, ...]], ...]
    persona_default: str
    lifecycle: Mapping[str, Any]
    seat_window_days: int
    seat_history_days: int
    seat_active_events: frozenset[str]
    fit: Mapping[str, Any]
    intent_scale: float
    signals: Mapping[str, SignalRule]
    derived: Mapping[str, Mapping[str, Any]]
    plays: tuple[PlayDef, ...]
    org: Mapping[str, Any]
    pricing: Pricing

    @property
    def founder_name(self) -> str:
        """Name of the founder who reads the weekly brief."""
        return str(self.org["founder_name"])

    def as_of_for_week(self, week: date) -> datetime:
        """Return the as-of timestamp for a week: that date at the configured time of day."""
        return datetime.combine(week, time(self.now.hour, self.now.minute), self.now.tzinfo)


def default_config_dir() -> Path:
    """Return ./config if present, else the config/ folder next to the source tree."""
    cwd = Path.cwd() / "config"
    if (cwd / "signals.toml").exists():
        return cwd
    return Path(__file__).resolve().parents[2] / "config"


def load_config(config_dir: Path | str | None = None) -> Config:
    """Load and validate the three TOML files from ``config_dir``."""
    base = Path(config_dir) if config_dir else default_config_dir()
    signals = _read_toml(base / "signals.toml")
    plays = _read_toml(base / "plays.toml")
    pricing = _read_toml(base / "pricing.toml")
    return build_config(signals, plays, pricing)


def build_config(
    signals: Mapping[str, Any], plays: Mapping[str, Any], pricing: Mapping[str, Any]
) -> Config:
    """Validate raw TOML dictionaries and build a :class:`Config`."""
    general = _section(signals, "general", "signals.toml")
    identity = _section(signals, "identity", "signals.toml")
    personas = _section(signals, "personas", "signals.toml")
    lifecycle = _section(signals, "lifecycle", "signals.toml")
    seats = _section(signals, "seats", "signals.toml")
    fit = _section(signals, "fit", "signals.toml")
    intent = _section(signals, "intent", "signals.toml")
    half_life = _num(general, "half_life_days", "general")
    if half_life <= 0:
        raise ConfigError("general.half_life_days must be > 0")
    windows = tuple(int(w) for w in _req(general, "windows_days", list, "general"))
    if not windows or any(w <= 0 for w in windows) or 30 not in windows:
        raise ConfigError("general.windows_days must be positive and include 30")
    return Config(
        now=_parse_now(_req(general, "now", str, "general")),
        seed=int(_num(general, "seed", "general")),
        half_life_days=half_life,
        windows=tuple(sorted(windows)),
        fit_weight=_unit(general, "fit_weight", "general"),
        intent_weight=_unit(general, "intent_weight", "general"),
        momentum_per_point=_num(general, "momentum_per_point", "general"),
        momentum_cap=_num(general, "momentum_cap", "general"),
        identity=_identity(identity),
        bot_suffix=_req(identity, "bot_suffix", str, "identity"),
        freemail=frozenset(d.lower() for d in _req(identity, "freemail_domains", list, "identity")),
        persona_order=_personas(personas),
        persona_default=_persona_name(_req(personas, "default", str, "personas")),
        lifecycle=_lifecycle(lifecycle),
        seat_window_days=int(_num(seats, "window_days", "seats")),
        seat_history_days=int(_num(seats, "history_days", "seats")),
        seat_active_events=_events(_req(seats, "active_events", list, "seats"), "seats"),
        fit=_fit(fit),
        intent_scale=_positive(intent, "scale", "intent"),
        signals=_signals(_section(intent, "signals", "intent")),
        derived=_derived(_section(intent, "derived", "intent")),
        plays=_plays(plays),
        org=_org(plays),
        pricing=_pricing(pricing),
    )


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name}: invalid TOML: {exc}") from exc


def _section(data: Mapping[str, Any], key: str, where: str) -> Mapping[str, Any]:
    return _req(data, key, dict, where)


def _req(data: Mapping[str, Any], key: str, kind: type, where: str) -> Any:
    if key not in data:
        raise ConfigError(f"{where}: missing required key '{key}'")
    value = data[key]
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
        raise ConfigError(f"{where}.{key}: expected {kind.__name__}, got {type(value).__name__}")
    return value


def _num(data: Mapping[str, Any], key: str, where: str) -> float:
    return float(_req(data, key, float, where))


def _positive(data: Mapping[str, Any], key: str, where: str) -> float:
    value = _num(data, key, where)
    if value <= 0:
        raise ConfigError(f"{where}.{key} must be > 0")
    return value


def _unit(data: Mapping[str, Any], key: str, where: str) -> float:
    value = _num(data, key, where)
    if not 0.0 <= value <= 1.0:
        raise ConfigError(f"{where}.{key} must be between 0 and 1")
    return value


def _parse_now(raw: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ConfigError(f"general.now is not ISO-8601: {raw}") from exc
    if value.tzinfo is None:
        raise ConfigError("general.now must include a UTC offset")
    return value


def _identity(section: Mapping[str, Any]) -> dict[str, float]:
    keys = (
        "corporate_domain",
        "alias_domain",
        "github_org",
        "freemail_github_org",
        "commit_email_domain",
    )
    out = {k: _unit(section, k, "identity") for k in keys}
    values = [out[k] for k in keys]
    if values != sorted(values, reverse=True):
        raise ConfigError("identity confidences must not increase down the waterfall")
    return out


def _persona_name(name: str) -> str:
    if name not in PERSONAS:
        raise ConfigError(f"unknown persona '{name}' (expected one of {sorted(PERSONAS)})")
    return name


def _personas(section: Mapping[str, Any]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    keywords = _section(section, "keywords", "personas")
    order = _req(section, "order", list, "personas")
    out = []
    for name in order:
        _persona_name(name)
        words = _req(keywords, name, list, "personas.keywords")
        out.append((name, tuple(w.lower() for w in words)))
    return tuple(out)


def _events(names: list[Any], where: str) -> frozenset[str]:
    unknown = sorted(str(n) for n in names if n not in EVENT_TYPES)
    if unknown:
        raise ConfigError(f"{where}: unknown event types {unknown}")
    return frozenset(names)


def _lifecycle(section: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "engaged_min_run_days": int(_positive(section, "engaged_min_run_days", "lifecycle")),
        "engaged_window_days": int(_positive(section, "engaged_window_days", "lifecycle")),
        "pql_min_user_intent": _num(section, "pql_min_user_intent", "lifecycle"),
        "pqa_min_engaged_users": int(_positive(section, "pqa_min_engaged_users", "lifecycle")),
        "pqa_engaged_recency_days": int(
            _positive(section, "pqa_engaged_recency_days", "lifecycle")
        ),
        "enterprise_intent_window_days": int(
            _positive(section, "enterprise_intent_window_days", "lifecycle")
        ),
        "value_event_multiplier": _positive(section, "value_event_multiplier", "lifecycle"),
    }
    for key in ("enterprise_intent_events", "value_events"):
        out[key] = _events(_req(section, key, list, "lifecycle"), f"lifecycle.{key}")
    return out


def _bands(raw: list[Any], where: str) -> tuple[tuple[float, float], ...]:
    bands = []
    for item in raw:
        if not isinstance(item, list) or len(item) != 2:
            raise ConfigError(f"{where}: each band must be [minimum, points]")
        bands.append((float(item[0]), float(item[1])))
    if [b[0] for b in bands] != sorted(b[0] for b in bands):
        raise ConfigError(f"{where}: bands must be sorted by minimum")
    return tuple(bands)


def _fit(section: Mapping[str, Any]) -> dict[str, Any]:
    lang = _section(section, "language", "fit")
    industry = _section(section, "industry", "fit")
    market = _section(section, "market", "fit")
    return {
        "eng_bands": _bands(_req(section["eng_headcount"], "bands", list, "fit"), "fit.eng"),
        "parent_bands": _bands(_req(section["parent_size"], "bands", list, "fit"), "fit.parent"),
        "language_max": _positive(lang, "max_points", "fit.language"),
        "language_points": {k: float(v) for k, v in _section(lang, "points", "fit").items()},
        "walltime_only": frozenset(_req(lang, "walltime_only", list, "fit.language")),
        "private_max": _positive(section["private_repos"], "max_points", "fit.private_repos"),
        "industry_default": _num(industry, "default", "fit.industry"),
        "industry_points": {k: float(v) for k, v in _section(industry, "points", "fit").items()},
        "market_per_repo": _num(market, "points_per_repo", "fit.market"),
        "market_cap": _num(market, "cap", "fit.market"),
    }


def _signals(section: Mapping[str, Any]) -> dict[str, SignalRule]:
    out = {}
    for event, raw in section.items():
        if event not in EVENT_TYPES:
            raise ConfigError(f"intent.signals: unknown event type '{event}'")
        weight = _num(raw, "weight", f"intent.signals.{event}")
        cap = _num(raw, "cap", f"intent.signals.{event}")
        if weight * cap < 0 or (weight != 0 and cap == 0):
            raise ConfigError(f"intent.signals.{event}: weight and cap must share a sign")
        reason = _req(raw, "reason", str, f"intent.signals.{event}")
        out[event] = SignalRule(event, weight, cap, reason)
    return out


def _derived(section: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    missing = sorted(DERIVED_SIGNALS - set(section))
    if missing:
        raise ConfigError(f"intent.derived: missing {missing}")
    return {k: dict(v) for k, v in section.items() if k in DERIVED_SIGNALS}


def _plays(data: Mapping[str, Any]) -> tuple[PlayDef, ...]:
    raw = _req(data, "plays", list, "plays.toml")
    seen: set[str] = set()
    out = []
    for item in raw:
        pid = _req(item, "id", str, "plays")
        where = f"plays.{pid}"
        if pid in seen:
            raise ConfigError(f"{where}: duplicate play id")
        seen.add(pid)
        trigger = _req(item, "trigger", str, where)
        if trigger not in KNOWN_TRIGGERS:
            raise ConfigError(f"{where}: unknown trigger '{trigger}'")
        owner = _req(item, "owner", str, where)
        if owner not in OWNERS:
            raise ConfigError(f"{where}: owner must be one of {sorted(OWNERS)}")
        out.append(
            PlayDef(
                play_id=pid,
                name=_req(item, "name", str, where),
                trigger=trigger,
                priority=int(_num(item, "priority", where)),
                owner=owner,
                sla_hours=int(_num(item, "sla_hours", where)),
                pipeline=_req(item, "pipeline", bool, where),
                params={k: float(v) for k, v in _req(item, "params", dict, where).items()},
                action=_req(item, "action", str, where),
                success_metric=_req(item, "success_metric", str, where),
                template=_req(item, "template", str, where),
            )
        )
    return tuple(sorted(out, key=lambda p: (-p.priority, p.play_id)))


def _org(data: Mapping[str, Any]) -> dict[str, Any]:
    org = dict(_section(data, "org", "plays.toml"))
    _req(org, "founder_name", str, "org")
    _req(org, "alert_channel", str, "org")
    org["alert_min_priority"] = int(_num(org, "alert_min_priority", "org"))
    org["account_briefs_top_n"] = int(_num(org, "account_briefs_top_n", "org"))
    org["founder_weekly_capacity"] = int(_num(org, "founder_weekly_capacity", "org"))
    return org


def _pricing(data: Mapping[str, Any]) -> Pricing:
    seats = _section(data, "seats", "pricing.toml")
    runners = _section(data, "runners", "pricing.toml")
    enterprise = _section(data, "enterprise", "pricing.toml")
    assumptions = _section(data, "assumptions", "pricing.toml")
    return Pricing(
        free_cap=int(_positive(seats, "free_cap", "seats")),
        pro_annual=_positive(seats, "pro_annual_per_user_month", "seats"),
        pro_monthly=_positive(seats, "pro_monthly_per_user_month", "seats"),
        trial_days=int(_positive(seats, "trial_days", "seats")),
        graviton_free_minutes=int(_num(runners, "graviton_free_minutes", "runners")),
        graviton_rate=_positive(runners, "graviton_per_minute", "runners"),
        ryzen_rate=_positive(runners, "ryzen_per_minute", "runners"),
        enterprise_features=tuple(_req(enterprise, "features", list, "enterprise")),
        growth_lookback_days=int(_positive(assumptions, "seat_growth_lookback_days", "a")),
        outbound_adoption_rate=_unit(assumptions, "outbound_adoption_rate", "assumptions"),
        ryzen_share_if_requested=_unit(assumptions, "ryzen_share_if_requested", "assumptions"),
    )


def days_between(later: datetime, earlier: datetime) -> float:
    """Return the fractional number of days from ``earlier`` to ``later``."""
    return (later - earlier) / timedelta(days=1)
