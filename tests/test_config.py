"""Config loading and validation errors."""

from __future__ import annotations

import copy
import tomllib
from datetime import date
from pathlib import Path

import pytest

from signal_engine.config import ConfigError, build_config, default_config_dir, load_config

ROOT = Path(__file__).resolve().parents[1] / "config"


def raw() -> tuple[dict, dict, dict]:
    return tuple(
        tomllib.loads((ROOT / f"{n}.toml").read_text()) for n in ("signals", "plays", "pricing")
    )  # type: ignore[return-value]


def test_loads_repo_config() -> None:
    cfg = load_config(ROOT)
    assert cfg.founder_name == "Arthur"
    assert cfg.pricing.pro_annual == 15.0 and cfg.pricing.pro_monthly == 20.0
    assert cfg.pricing.free_cap == 5 and cfg.pricing.trial_days == 14
    assert cfg.pricing.graviton_free_minutes == 600
    assert cfg.windows == (7, 14, 30, 90)
    assert [p.priority for p in cfg.plays] == sorted((p.priority for p in cfg.plays), reverse=True)
    assert len(cfg.plays) == 12
    assert cfg.as_of_for_week(date(2026, 10, 5)).isoformat() == "2026-10-05T09:00:00-07:00"


def test_default_config_dir_falls_back_to_source_tree(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert (default_config_dir() / "signals.toml").exists()


def mutate(fn) -> None:
    signals, plays, pricing = (copy.deepcopy(x) for x in raw())
    fn(signals, plays, pricing)
    build_config(signals, plays, pricing)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda s, p, c: s.pop("general"), "missing required key 'general'"),
        (lambda s, p, c: s["general"].update(half_life_days=0), "half_life_days must be > 0"),
        (lambda s, p, c: s["general"].update(half_life_days="soon"), "expected float"),
        (lambda s, p, c: s["general"].update(windows_days=[7, 14]), "include 30"),
        (lambda s, p, c: s["general"].update(now="2026-10-12T09:00:00"), "UTC offset"),
        (lambda s, p, c: s["general"].update(now="next monday"), "not ISO-8601"),
        (lambda s, p, c: s["general"].update(fit_weight=1.5), "between 0 and 1"),
        (
            lambda s, p, c: s["identity"].update(alias_domain=1.0, corporate_domain=0.9),
            "must not increase",
        ),
        (lambda s, p, c: s["personas"].update(default="wizard"), "unknown persona"),
        (lambda s, p, c: s["seats"].update(active_events=["pr_opened"]), "unknown event types"),
        (
            lambda s, p, c: s["intent"]["signals"].update(
                bogus={"weight": 1, "cap": 1, "reason": "x"}
            ),
            "unknown event type 'bogus'",
        ),
        (lambda s, p, c: s["intent"]["signals"]["run_completed"].update(cap=-1), "share a sign"),
        (lambda s, p, c: s["intent"]["derived"].pop("over_cap"), "missing ['over_cap']"),
        (lambda s, p, c: s["fit"]["eng_headcount"].update(bands=[[10, 1], [0, 2]]), "sorted"),
        (lambda s, p, c: s["fit"]["eng_headcount"].update(bands=[[10]]), "[minimum, points]"),
        (lambda s, p, c: s["intent"].update(scale=0), "intent.scale must be > 0"),
        (lambda s, p, c: s["lifecycle"].update(engaged_min_run_days=True), "expected float"),
        (lambda s, p, c: p["plays"][0].update(trigger="vibes"), "unknown trigger"),
        (lambda s, p, c: p["plays"][0].update(owner="intern"), "owner must be one of"),
        (lambda s, p, c: p["plays"].append(dict(p["plays"][0])), "duplicate play id"),
        (lambda s, p, c: p["plays"][0].update(pipeline="yes"), "expected bool"),
        (lambda s, p, c: p["org"].pop("founder_name"), "missing required key 'founder_name'"),
        (lambda s, p, c: c["seats"].update(free_cap=0), "free_cap must be > 0"),
        (lambda s, p, c: c["assumptions"].update(outbound_adoption_rate=2), "between 0 and 1"),
    ],
)
def test_validation_errors(change, message: str) -> None:
    with pytest.raises(
        ConfigError, match=message.replace("[", r"\[").replace("]", r"\]").replace("(", r"\(")
    ):
        mutate(change)


def test_missing_file_and_invalid_toml(tmp_path) -> None:
    with pytest.raises(ConfigError, match="missing config file"):
        load_config(tmp_path)
    for name in ("signals", "plays", "pricing"):
        (tmp_path / f"{name}.toml").write_text((ROOT / f"{name}.toml").read_text())
    (tmp_path / "plays.toml").write_text("[org\nfounder_name = ")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(tmp_path)
