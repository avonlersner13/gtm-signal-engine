"""Scoring rules: fit components, decay, negatives, clamping, derived signals, momentum."""

from __future__ import annotations

from conftest import company, features, scan, seats
from signal_engine.models import Score
from signal_engine.scoring import (
    AccountInputs,
    band_points,
    clamp,
    derived_contributions,
    event_contributions,
    fit_score,
    intent_score,
    plural,
    priority_score,
    run_dropped,
    score_account,
    singularize_ones,
)


def inputs(**kw) -> AccountInputs:
    base = {
        "account_id": "c1",
        "family": [company("c1")],
        "features": None,
        "seats": seats(),
        "scan": scan(),
        "private_repo_ratio": None,
        "engaged_users": 0,
        "orgs_active": 1,
    }
    base.update(kw)
    return AccountInputs(**base)


def test_band_points() -> None:
    bands = ((0, 1.0), (10, 5.0), (100, 9.0))
    assert band_points(0, bands) == 1.0
    assert band_points(50, bands) == 5.0
    assert band_points(1000, bands) == 9.0
    assert band_points(-1, bands) == 0.0


def test_fit_components_and_reasons(cfg) -> None:
    fam = [
        company("c1", eng_headcount=300, employee_count=2000, industry="fintech"),
        company("c2", eng_headcount=10, employee_count=4000),
    ]
    s = scan(
        languages=("go", "rust"), repos_benchmarks_without_codspeed=5, frameworks=("criterion",)
    )
    fit, reasons = fit_score(inputs(family=fam, scan=s, private_repo_ratio=0.5), cfg)
    expected = 25 + 25 + 7.5 + 15 + 15 + 12  # 310 engineers -> 250+ band
    assert fit == expected
    assert "Go/Rust stack (walltime-only: macro runners)" in reasons
    assert "50% private repos" in reasons
    assert any("parent account" in r for r in reasons)
    assert "5 repos already benchmark without CodSpeed" in reasons


def test_fit_unknown_industry_uses_default(cfg) -> None:
    fit, reasons = fit_score(
        inputs(family=[company("c1", eng_headcount=1, industry="mining")]), cfg
    )
    assert fit == cfg.fit["industry_default"] + 4.0
    assert reasons[0] == "mining industry"


def test_event_contribution_decay_cap_and_reason(cfg) -> None:
    f = features(
        decayed={"regression_detected": 2.5, "run_completed": 1000.0, "unknown_event": 1.0},
        counts={7: {}, 14: {}, 30: {"regression_detected": 3, "run_completed": 400}, 90: {}},
    )
    parts = {r: p for p, r in event_contributions(f, cfg)}
    assert parts["3 regressions caught in 30 days"] == 2.5
    assert parts["400 benchmark runs in 30 days"] == cfg.signals["run_completed"].cap


def test_reason_hidden_when_window_count_is_zero(cfg) -> None:
    f = features(
        decayed={"regression_detected": 0.2},
        counts={7: {}, 14: {}, 30: {}, 90: {"regression_detected": 1}},
    )
    [(points, reason)] = event_contributions(f, cfg)
    assert points > 0 and reason is None


def test_negative_signals_subtract_and_cap(cfg) -> None:
    f = features(
        decayed={"seat_removed": 10.0, "informational_check_enabled": 1.0},
        counts={7: {}, 14: {}, 30: {"seat_removed": 10}, 90: {}},
    )
    parts = {r: p for p, r in event_contributions(f, cfg)}
    assert parts["10 seats removed in 30 days"] == -8.0
    assert parts["check switched to informational (non-blocking)"] == -8.0


def test_intent_clamps_to_0_and_100(cfg) -> None:
    neg = features(
        decayed={"seat_removed": 10.0, "wizard_disabled": 1.0},
        counts={7: {}, 14: {}, 30: {"seat_removed": 2}, 90: {}},
    )
    assert intent_score(inputs(features=neg), cfg)[0] == 0.0
    huge = {e: 1000.0 for e, r in cfg.signals.items() if r.weight > 0}
    pos = features(decayed=huge, counts={7: {}, 14: {}, 30: dict.fromkeys(huge, 9), 90: {}})
    score, reasons = intent_score(
        inputs(features=pos, seats=seats(over_cap=True, seats_used=9)), cfg
    )
    assert score == 100.0
    assert len(reasons) == 6


def test_derived_seat_signals(cfg) -> None:
    trial = seats(over_cap=True, seats_used=6, trial_active=True, trial_days_left=4)
    texts = [r for _, r in derived_contributions(inputs(seats=trial), cfg) if r]
    assert "6 active users, 5-seat cap exceeded, trial ends in 4 days" in texts
    one_day = seats(over_cap=True, seats_used=7, trial_active=True, trial_days_left=1)
    assert "trial ends in 1 day" in derived_contributions(inputs(seats=one_day), cfg)[0][1]
    paid = derived_contributions(inputs(seats=seats(over_cap=True, seats_used=8, paid=True)), cfg)
    assert paid[0][1].endswith("on a paid plan")
    expired = derived_contributions(
        inputs(seats=seats(over_cap=True, seats_used=8, trial_expired=True)), cfg
    )
    assert expired[0][1].endswith("trial expired")
    plain = derived_contributions(inputs(seats=seats(over_cap=True, seats_used=6)), cfg)
    assert plain[0][1] == "6 active users, 5-seat cap exceeded"


def test_near_cap_requires_growth(cfg) -> None:
    rising = derived_contributions(inputs(seats=seats(seats_used=4, seats_7d_ago=3)), cfg)
    assert rising[0][1] == "4/5 free seats used, up from 3 a week ago"
    flat = derived_contributions(inputs(seats=seats(seats_used=4, seats_7d_ago=4)), cfg)
    assert flat == []


def test_derived_blocked_runner_orgs_engaged_market_drop(cfg) -> None:
    f = features(projected_graviton_minutes=900.0, runs_30=10, runs_prev_30=40)
    s = scan(repos_benchmarks_without_codspeed=2, frameworks=("divan",))
    parts = derived_contributions(
        inputs(
            features=f, seats=seats(blocked_users=("a",)), scan=s, orgs_active=3, engaged_users=5
        ),
        cfg,
    )
    texts = {r: p for p, r in parts}
    assert texts["1 engineer blocked without a seat (auto-allocation off)"] == 5.0
    assert texts["projected 900 Graviton minutes this month vs 600 free"] == 10.0
    assert texts["3 GitHub orgs active under one parent account"] == 8.0
    assert texts["5 engaged engineers"] == 8.0
    assert texts["2 repos benchmark with divan but not CodSpeed"] == 6.0
    assert texts["runs down 75% vs the prior 30 days (10 vs 40)"] == -8.0


def test_run_drop_needs_enough_prior_runs(cfg) -> None:
    rule = cfg.derived["run_drop"]
    assert not run_dropped(features(runs_30=0, runs_prev_30=5), rule)
    assert not run_dropped(features(runs_30=30, runs_prev_30=40), rule)
    assert run_dropped(features(runs_30=19, runs_prev_30=40), rule)


def test_priority_blend_and_momentum_clamp(cfg) -> None:
    assert priority_score(50, 50, 0, cfg) == 50.0
    assert priority_score(50, 50, 10, cfg) == 53.0
    assert priority_score(50, 50, 1000, cfg) == 60.0
    assert priority_score(50, 50, -1000, cfg) == 40.0
    assert priority_score(100, 100, 100, cfg) == 100.0
    assert clamp(-3) == 0.0


def test_score_account_deltas(cfg) -> None:
    prev = Score("c1", "2026-10-05", 10.0, 30.0, 50.0, 0.0, 0.0, (), ())
    f = features(decayed={"baseline_created": 1.0}, counts={7: {}, 14: {}, 30: {}, 90: {}})
    score = score_account(inputs(features=f), cfg, "2026-10-12", prev)
    assert score.intent_delta == round(score.intent - 30.0, 1)
    assert score.priority_delta == round(score.priority - 50.0, 1)
    first = score_account(inputs(features=f), cfg, "2026-10-12", None)
    assert first.intent_delta == 0.0 and first.priority_delta == 0.0


def test_grammar_helpers() -> None:
    assert plural(1, "day") == "1 day" and plural(3, "day") == "3 days"
    assert singularize_ones("1 regressions caught") == "1 regression caught"
    assert singularize_ones("1 @codspeedbot fix PRs merged") == "1 @codspeedbot fix PR merged"
    assert singularize_ones("1 engineer at Atlas Labs") == "1 engineer at Atlas Labs"
    assert singularize_ones("11 regressions") == "11 regressions"
