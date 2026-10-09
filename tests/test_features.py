"""Feature vectors: windows, previous week, decay, runner minutes, bots."""

from __future__ import annotations

import math

import pytest

from conftest import NOW, EventLog
from signal_engine.features import capped_intent, compute_features
from signal_engine.models import Resolution


def res(**pairs: str) -> dict[str, Resolution]:
    return {
        u: Resolution(u, c, c, "corporate_domain", 1.0, u.startswith("bot"))
        for u, c in pairs.items()
    }


def test_window_counts_and_previous_week(cfg) -> None:
    log = EventLog()
    for days in (1, 6, 10, 20, 45, 120):
        log.add("u1", "run_completed", days, "r1", instrument="simulation")
    log.add("u1", "run_completed", 8, "r1")
    f = compute_features(log.events(), res(u1="c1"), cfg, NOW).accounts["c1"]
    assert f.counts[7]["run_completed"] == 2
    assert f.counts[14]["run_completed"] == 4
    assert f.counts[30]["run_completed"] == 5
    assert f.counts[90]["run_completed"] == 6
    assert f.prev_week == {"run_completed": 2}
    assert f.runs_30 == 5
    assert f.runs_prev_30 == 1
    assert f.instruments_30 == {"simulation": 5}


def test_decay_half_life(cfg) -> None:
    log = EventLog().add("u1", "docs_visit", 0).add("u1", "docs_visit", cfg.half_life_days)
    f = compute_features(log.events(), res(u1="c1"), cfg, NOW).accounts["c1"]
    assert f.decayed["docs_visit"] == pytest.approx(1.5, abs=1e-6)


def test_bots_unresolved_and_future_events_are_skipped(cfg) -> None:
    log = EventLog().add("bot1", "run_completed", 1).add("ghost", "run_completed", 1)
    log.add("u1", "run_completed", 1).add("u1", "run_completed", -1)
    fs = compute_features(log.events(), res(u1="c1", bot1="c1"), cfg, NOW)
    assert fs.accounts["c1"].counts[90]["run_completed"] == 1
    assert set(fs.users) == {"u1"}


def test_runner_minutes_month_to_date_and_projection(cfg) -> None:
    log = EventLog()
    log.add("u1", "macro_runner_minutes", 2, "r1", runner="graviton", minutes=100.0)
    log.add("u1", "macro_runner_minutes", 3, "r1", runner="ryzen", minutes=20.0)
    log.add("u1", "macro_runner_minutes", 20, "r1", runner="graviton", minutes=500.0)
    f = compute_features(log.events(), res(u1="c1"), cfg, NOW).accounts["c1"]
    assert f.graviton_minutes_mtd == 100.0
    assert f.ryzen_minutes_mtd == 20.0
    elapsed = (NOW - NOW.replace(day=1, hour=0, minute=0)).total_seconds() / 86400
    assert f.projected_graviton_minutes == pytest.approx(round(100 / elapsed * 31, 1))


def test_recent_details_people_companies_tools_reasons(cfg) -> None:
    log = EventLog()
    log.add("u1", "mcp_tool_call", 1, tool="compare_runs").add(
        "u2", "mcp_tool_call", 2, tool="get_run"
    )
    log.add("u2", "benchmark_ignored", 3, "r1", reason="too noisy")
    log.add("u3", "dashboard_login", 45)
    resolutions = {
        "u1": Resolution("u1", "c1", "c1", "corporate_domain", 1.0, False),
        "u2": Resolution("u2", "c2", "c1", "corporate_domain", 1.0, False),
        "u3": Resolution("u3", "c1", "c1", "corporate_domain", 1.0, False),
    }
    f = compute_features(log.events(), resolutions, cfg, NOW).accounts["c1"]
    assert f.people_30 == 2
    assert f.companies_active_30 == ("c1", "c2")
    assert f.mcp_tools_30 == {"compare_runs": 1, "get_run": 1}
    assert f.ignored_reasons_30 == ("too noisy",)
    assert f.first_ts["dashboard_login"] < f.last_ts["mcp_tool_call"]


def test_user_features_intent_value_and_run_days(cfg) -> None:
    log = EventLog()
    for d in (1, 2, 3):
        log.add("u1", "run_completed", d, "r1")
    log.add("u1", "regression_acknowledged", 1, "r1")
    uf = compute_features(log.events(), res(u1="c1"), cfg, NOW).users["u1"]
    assert len(uf.run_days) == 3
    assert uf.event_count == 4
    assert uf.intent > 0
    plain = sum(math.exp(-d * math.log(2) / cfg.half_life_days) for d in (1, 2, 3))
    assert uf.value_score > plain


def test_capped_intent_respects_caps_and_negatives(cfg) -> None:
    assert capped_intent({"run_completed": 1000.0}, cfg) == cfg.signals["run_completed"].cap
    assert capped_intent({"seat_removed": 100.0}, cfg) == cfg.signals["seat_removed"].cap
    assert capped_intent({"not_a_signal": 5.0}, cfg) == 0.0
