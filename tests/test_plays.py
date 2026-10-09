"""Each play trigger, precedence, the three-play limit and template filling."""

from __future__ import annotations

from conftest import NOW, ago, committee, contact, features, revenue, scan, seats
from signal_engine.plays import PlayContext, churn_signals, days_since, match_plays

C30 = {7: {}, 14: {}, 30: {}, 90: {}}


def ctx(**kw) -> PlayContext:
    base = {
        "name": "Acme",
        "stage": "pqa",
        "fit": 60.0,
        "seats": seats(),
        "scan": scan(),
        "features": features(),
        "committee": committee(
            contact("u1", name="Robin Vale"), contact("u2", "vp_cto", "Kai Moss")
        ),
        "revenue": revenue(),
        "orgs_active": 1,
        "as_of": NOW,
        "founder": "Arthur",
        "free_minutes": 600,
    }
    base.update(kw)
    return PlayContext(**base)


def ids(c: PlayContext, cfg) -> list[str]:
    return [p.play_id for p in match_plays(c, cfg)]


def test_no_signals_no_play(cfg) -> None:
    assert ids(ctx(), cfg) == []


def test_trial_ending(cfg) -> None:
    c = ctx(seats=seats(trial_active=True, trial_days_left=4, seats_used=6, over_cap=True))
    [play] = match_plays(c, cfg)
    assert play.play_id == "trial_ending"
    assert play.owner == "founder"
    assert "ends in 4 days" in play.message and "6 engineers" in play.message
    assert ids(ctx(seats=seats(trial_active=True, trial_days_left=6)), cfg) == []


def test_blocked_users_message_addresses_buyer(cfg) -> None:
    [play] = match_plays(ctx(seats=seats(blocked_users=("a",))), cfg)
    assert play.play_id == "blocked_users"
    assert play.message.startswith("Hi Kai, heads-up: 1 engineer at Acme is blocked")
    two = match_plays(ctx(seats=seats(blocked_users=("a", "b"))), cfg)[0]
    assert "2 engineers at Acme are blocked" in two.message


def test_cap_approaching(cfg) -> None:
    assert ids(ctx(seats=seats(seats_used=4, seats_7d_ago=3)), cfg) == ["cap_approaching"]
    assert ids(ctx(seats=seats(seats_used=5, seats_7d_ago=4)), cfg) == ["cap_approaching"]
    assert ids(ctx(seats=seats(seats_used=4, seats_7d_ago=4)), cfg) == []
    assert ids(ctx(seats=seats(seats_used=4, seats_7d_ago=2, paid=True)), cfg) == []
    assert ids(ctx(seats=seats(seats_used=3, seats_7d_ago=1)), cfg) == []


def test_multi_team_spread(cfg) -> None:
    [play] = match_plays(ctx(orgs_active=2), cfg)
    assert play.play_id == "multi_team_spread"
    assert "2 GitHub orgs" in play.message


def test_runner_overage_three_ways(cfg) -> None:
    over = ctx(
        features=features(projected_graviton_minutes=900),
        revenue=revenue(projected_overage_minutes=300),
    )
    assert ids(over, cfg) == ["runner_overage"]
    ryzen = ctx(features=features(last_ts={"ryzen_requested": ago(3)}))
    assert ids(ryzen, cfg) == ["runner_overage"]
    budget = ctx(features=features(last_ts={"runner_budget_set": ago(10)}))
    assert ids(budget, cfg) == ["runner_overage"]
    stale = ctx(features=features(last_ts={"runner_budget_set": ago(40)}))
    assert ids(stale, cfg) == []
    assert ids(ctx(features=None), cfg) == []


def test_walltime_only_stack(cfg) -> None:
    c = ctx(
        scan=scan(walltime_only=True, languages=("go",)),
        features=features(instruments_30={"walltime": 3}),
    )
    [play] = match_plays(c, cfg)
    assert play.play_id == "walltime_only_stack"
    assert "Go benchmarks" in play.message
    assert (
        ids(
            ctx(
                stage="installed",
                scan=scan(walltime_only=True),
                features=features(instruments_30={"walltime": 3}),
            ),
            cfg,
        )
        == []
    )
    assert (
        ids(
            ctx(scan=scan(walltime_only=True), features=features(instruments_30={"simulation": 3})),
            cfg,
        )
        == []
    )


def test_security_review(cfg) -> None:
    c = ctx(
        features=features(last_ts={"trust_center_visit": ago(2), "soc2_report_requested": ago(5)})
    )
    [play] = match_plays(c, cfg)
    assert play.play_id == "security_review"
    assert "the Trust Center and our SOC 2 report" in play.message
    assert ids(ctx(features=features(last_ts={"sso_page_view": ago(20)})), cfg) == []


def test_ai_native_power_user(cfg) -> None:
    counts = {7: {}, 14: {}, 30: {"mcp_tool_call": 12}, 90: {"codspeedbot_fix_pr_merged": 3}}
    c = ctx(features=features(first_ts={"mcp_connected": ago(30)}, counts=counts))
    [play] = match_plays(c, cfg)
    assert play.play_id == "ai_native_power_user"
    assert "merged 3 @codspeedbot fix PRs" in play.message
    no_mcp = ctx(features=features(counts=counts))
    assert ids(no_mcp, cfg) == []


def test_activation_rescue(cfg) -> None:
    stuck = ctx(stage="installed", features=features(first_ts={"github_app_installed": ago(9)}))
    [play] = match_plays(stuck, cfg)
    assert play.play_id == "activation_rescue"
    assert play.owner == "automated"
    assert "9 days ago" in play.message
    fresh = ctx(stage="installed", features=features(first_ts={"github_app_installed": ago(3)}))
    assert ids(fresh, cfg) == []
    done = ctx(
        features=features(first_ts={"github_app_installed": ago(30), "baseline_created": ago(29)})
    )
    assert ids(done, cfg) == []
    assert ids(ctx(features=None), cfg) == []


def test_churn_risk_signals(cfg) -> None:
    counts = {7: {}, 14: {}, 30: {"benchmark_ignored": 3}, 90: {}}
    f = features(
        counts=counts,
        last_ts={
            "informational_check_enabled": ago(3),
            "wizard_disabled": ago(4),
            "seat_removed": ago(50),
        },
        runs_30=5,
        runs_prev_30=40,
    )
    c = ctx(features=f)
    params = next(p.params for p in cfg.plays if p.play_id == "churn_risk")
    assert churn_signals(c, params) == [
        "3 benchmarks ignored",
        "check switched to informational",
        "Wizard disabled",
        "runs down 88%",
    ]
    assert ids(c, cfg) == ["churn_risk"]
    assert churn_signals(ctx(features=None), params) == []


def test_outbound_and_not_addressable(cfg) -> None:
    market = ctx(
        features=None,
        stage="none",
        fit=60.0,
        scan=scan(repos_benchmarks_without_codspeed=2, frameworks=("criterion",)),
    )
    [play] = match_plays(market, cfg)
    assert play.play_id == "outbound"
    assert "criterion" in play.message
    low_fit = ctx(features=None, fit=20.0, scan=scan(repos_benchmarks_without_codspeed=2))
    assert ids(low_fit, cfg) == []
    sbt = ctx(features=None, scan=scan(not_addressable_only=True, frameworks=("sbt-jmh",)))
    assert ids(sbt, cfg) == ["not_addressable"]


def test_precedence_and_max_three(cfg) -> None:
    c = ctx(
        seats=seats(
            trial_active=True, trial_days_left=2, blocked_users=("a",), seats_used=6, over_cap=True
        ),
        orgs_active=3,
        features=features(last_ts={"trust_center_visit": ago(1), "ryzen_requested": ago(1)}),
    )
    assert ids(c, cfg) == ["trial_ending", "blocked_users", "security_review"]


def test_templates_fall_back_without_champion(cfg) -> None:
    c = ctx(committee=committee(), seats=seats(trial_active=True, trial_days_left=1, seats_used=6))
    [play] = match_plays(c, cfg)
    assert play.message.startswith("Hi there, Arthur here.")
    assert "ends in 1 day." in play.message


def test_days_since() -> None:
    assert days_since(ago(3.5), NOW) == 3
