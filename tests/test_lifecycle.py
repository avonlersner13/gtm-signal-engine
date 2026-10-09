"""Every lifecycle transition, prerequisites, and the PQL / PQA criteria."""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import NOW, ago, features, seats, user_features
from signal_engine.lifecycle import (
    account_stages,
    current_stage,
    engaged_recently,
    run_days_engaged_at,
    run_lifecycle,
    user_engaged_at,
)
from signal_engine.models import Opportunity


def ordinal(days_ago: float) -> int:
    return ago(days_ago).toordinal()


def stages(cfg, f, engaged=(), recent=(), pql=(), seat=None, opp=None):
    return account_stages(f, list(engaged), list(recent), list(pql), seat, opp, cfg, NOW)


def test_signed_up_only(cfg) -> None:
    entered = stages(cfg, features(first_ts={"signed_up": ago(10)}))
    assert list(entered) == ["signed_up"]
    assert current_stage(entered) == "signed_up"


def test_signed_up_falls_back_to_first_event(cfg) -> None:
    entered = stages(cfg, features(first_ts={"dashboard_login": ago(3), "docs_visit": ago(5)}))
    assert entered["signed_up"] == ago(5)


def test_installed(cfg) -> None:
    f = features(first_ts={"signed_up": ago(10), "github_app_installed": ago(9)})
    assert current_stage(stages(cfg, f)) == "installed"


def test_installed_requires_signup_prerequisite_time(cfg) -> None:
    f = features(first_ts={"signed_up": ago(5), "github_app_installed": ago(9)})
    assert stages(cfg, f)["installed"] == ago(5)


def test_activated_needs_baseline_and_first_report(cfg) -> None:
    base = {"signed_up": ago(10), "github_app_installed": ago(9), "baseline_created": ago(8)}
    assert current_stage(stages(cfg, features(first_ts=base))) == "installed"
    both = {**base, "first_pr_report": ago(6)}
    entered = stages(cfg, features(first_ts=both))
    assert current_stage(entered) == "activated"
    assert entered["activated"] == ago(6)


def test_activated_requires_installed(cfg) -> None:
    f = features(
        first_ts={"signed_up": ago(10), "baseline_created": ago(8), "first_pr_report": ago(6)}
    )
    assert current_stage(stages(cfg, f)) == "signed_up"


ACTIVE = {
    "signed_up": ago(30),
    "github_app_installed": ago(29),
    "baseline_created": ago(28),
    "first_pr_report": ago(27),
}


def test_engaged_pql_pqa_opportunity_chain(cfg) -> None:
    f = features(first_ts=ACTIVE)
    entered = stages(
        cfg, f, engaged=[ago(20), ago(15)], recent=[ago(20), ago(15)], pql=[ago(20)], opp=ago(2)
    )
    assert current_stage(entered) == "opportunity"
    assert entered["engaged"] == ago(20)
    assert entered["pql"] == ago(20)
    assert entered["pqa"] == ago(15)
    assert entered["opportunity"] == ago(2)


def test_engaged_requires_activation(cfg) -> None:
    f = features(first_ts={"signed_up": ago(30)})
    assert "engaged" not in stages(cfg, f, engaged=[ago(3)])


def test_pqa_needs_two_recently_engaged_users(cfg) -> None:
    f = features(first_ts=ACTIVE)
    one = stages(cfg, f, engaged=[ago(20), ago(15)], recent=[ago(20)])
    assert current_stage(one) == "engaged"


def test_pqa_via_over_cap(cfg) -> None:
    f = features(first_ts=ACTIVE)
    entered = stages(cfg, f, engaged=[ago(20)], seat=seats(over_cap=True, cap_exceeded_at=ago(4)))
    assert entered["pqa"] == ago(4)
    no_time = stages(cfg, f, engaged=[ago(20)], seat=seats(over_cap=True))
    assert no_time["pqa"] == NOW


def test_pqa_via_recent_enterprise_intent(cfg) -> None:
    recent = features(first_ts=ACTIVE, last_ts={"sso_page_view": ago(3)})
    assert stages(cfg, recent, engaged=[ago(20)])["pqa"] == ago(3)
    stale = features(first_ts=ACTIVE, last_ts={"sso_page_view": ago(45)})
    assert "pqa" not in stages(cfg, stale, engaged=[ago(20)])
    pricing = features(first_ts=ACTIVE, last_ts={"pricing_page_view": ago(1)})
    assert "pqa" not in stages(cfg, pricing, engaged=[ago(20)])


def test_opportunity_requires_pqa(cfg) -> None:
    f = features(first_ts=ACTIVE)
    assert "opportunity" not in stages(cfg, f, engaged=[ago(20)], opp=ago(1))


def test_current_stage_none() -> None:
    assert current_stage({}) == "none"


def test_run_days_engagement_window() -> None:
    assert run_days_engaged_at([1, 5, 14], 3, 14) == datetime.fromordinal(14).replace(tzinfo=UTC)
    assert run_days_engaged_at([1, 5, 15], 3, 14) is None
    assert run_days_engaged_at([1, 2], 3, 14) is None


def test_user_engaged_by_ack_or_required_check(cfg) -> None:
    assert user_engaged_at(user_features(first_ts={"regression_acknowledged": ago(4)}), cfg) == ago(
        4
    )
    assert user_engaged_at(user_features(first_ts={"required_check_enabled": ago(6)}), cfg) == ago(
        6
    )
    assert user_engaged_at(user_features(), cfg) is None


def test_engaged_recently(cfg) -> None:
    recent_runs = user_features(run_days=(ordinal(10), ordinal(8), ordinal(5)))
    assert engaged_recently(recent_runs, cfg, NOW)
    old_runs = user_features(run_days=(ordinal(80), ordinal(79), ordinal(78)))
    assert not engaged_recently(old_runs, cfg, NOW)
    ack = user_features(first_ts={"regression_acknowledged": ago(2)})
    assert engaged_recently(ack, cfg, NOW)


def test_run_lifecycle_users_accounts_and_entries(cfg) -> None:
    days = (ordinal(12), ordinal(10), ordinal(9))
    users = {
        "u1": user_features("u1", run_days=days, intent=50.0),
        "u2": user_features("u2", run_days=days, intent=1.0),
        "u3": user_features("u3"),
    }
    accounts = {"c1": features(first_ts=ACTIVE)}
    opp = [
        Opportunity("c1", "multi_team_spread", ago(1)),
        Opportunity("c1", "x", NOW.replace(year=2027)),
    ]
    result = run_lifecycle(accounts, users, {}, opp, cfg, NOW)
    assert result.account_stage["c1"] == "opportunity"
    assert result.engaged_users["c1"] == ("u1", "u2")
    assert result.pql_users["c1"] == ("u1",)
    kinds = {(e.entity_type, e.stage) for e in result.entries}
    assert ("user", "pql") in kinds and ("account", "pqa") in kinds
    assert sum(1 for e in result.entries if e.entity_id == "u2" and e.stage == "pql") == 0
