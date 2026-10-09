"""Seat counting per billing rules: private vs public, bots, windows, trials, blocks."""

from __future__ import annotations

from conftest import NOW, EventLog, company, repo
from signal_engine.models import Resolution
from signal_engine.seats import active_series, compute_seats, latest_crossing, rollup_seats

C1 = company("c1")
REPOS = [repo("priv", private=True), repo("pub", private=False), repo("priv2", private=True)]


def res(*uids: str, bots: tuple[str, ...] = (), company_id: str = "c1") -> dict[str, Resolution]:
    out = {u: Resolution(u, company_id, company_id, "corporate_domain", 1.0, False) for u in uids}
    out.update({b: Resolution(b, company_id, company_id, "github_org", 0.9, True) for b in bots})
    return out


def run(cfg, log: EventLog, resolutions, companies=(C1,)):
    return compute_seats(log.events(), resolutions, REPOS, list(companies), cfg, NOW)


def enabled() -> EventLog:
    log = EventLog()
    log.add("admin", "repo_imported", 200, "priv")
    log.add("admin", "repo_imported", 200, "pub")
    return log


def test_private_pr_and_report_count(cfg) -> None:
    log = (
        enabled().add("u1", "pr_authored_private", 2, "priv").add("u2", "report_viewed", 3, "priv")
    )
    status = run(cfg, log, res("u1", "u2", "admin"))["c1"]
    assert status.seats_used == 2
    assert status.active_user_ids == ("u1", "u2")


def test_public_repo_activity_does_not_count(cfg) -> None:
    log = enabled().add("u1", "pr_authored_private", 2, "pub").add("u2", "report_viewed", 2, "pub")
    assert run(cfg, log, res("u1", "u2", "admin"))["c1"].seats_used == 0


def test_repo_must_be_codspeed_enabled_at_event_time(cfg) -> None:
    log = enabled().add("u1", "pr_authored_private", 2, "priv2")
    log.add("admin", "repo_imported", 1, "priv2").add("u2", "pr_authored_private", 0.5, "priv2")
    assert run(cfg, log, res("u1", "u2", "admin"))["c1"].active_user_ids == ("u2",)


def test_deleted_repo_stops_counting(cfg) -> None:
    log = (
        enabled()
        .add("admin", "repo_deleted", 5, "priv")
        .add("u1", "pr_authored_private", 2, "priv")
    )
    assert run(cfg, log, res("u1", "admin"))["c1"].seats_used == 0


def test_dashboard_report_without_repo_counts(cfg) -> None:
    log = EventLog().add("u1", "report_viewed", 1)
    assert run(cfg, log, res("u1"))["c1"].seats_used == 1


def test_bots_never_count(cfg) -> None:
    log = enabled().add("bot", "pr_authored_private", 1, "priv")
    assert run(cfg, log, res("admin", bots=("bot",)))["c1"].seats_used == 0


def test_unresolved_users_are_ignored(cfg) -> None:
    log = enabled().add("ghost", "pr_authored_private", 1, "priv")
    assert run(cfg, log, res("admin"))["c1"].seats_used == 0


def test_rolling_30_day_window(cfg) -> None:
    log = (
        enabled()
        .add("u1", "pr_authored_private", 29.5, "priv")
        .add("u2", "pr_authored_private", 30.5, "priv")
    )
    status = run(cfg, log, res("u1", "u2", "admin"))["c1"]
    assert status.active_user_ids == ("u1",)
    assert status.seats_used == 1
    assert status.seats_7d_ago == 2
    assert status.seats_30d_ago == 1  # only u2 (30 days ago) was in that window


def test_sixth_user_triggers_trial(cfg) -> None:
    log = enabled()
    for i in range(5):
        log.add(f"u{i}", "pr_authored_private", 20, "priv")
    five = run(cfg, log, res(*[f"u{i}" for i in range(6)], "admin"))["c1"]
    assert five.seats_used == 5
    assert not five.over_cap and not five.trial_active
    log.add("u5", "pr_authored_private", 4, "priv")
    six = run(cfg, log, res(*[f"u{i}" for i in range(6)], "admin"))["c1"]
    assert six.seats_used == 6 and six.over_cap
    assert six.trial_active
    assert six.trial_days_left == 10
    assert six.cap_exceeded_at is not None


def test_explicit_trial_event_wins_and_expires(cfg) -> None:
    log = (
        enabled()
        .add("admin", "trial_started", 20, None)
        .add("u1", "pr_authored_private", 1, "priv")
    )
    status = run(cfg, log, res("u1", "admin"))["c1"]
    assert status.trial_expired
    assert not status.trial_active
    assert status.trial_days_left is None


def test_paid_plan_ends_trial(cfg) -> None:
    log = (
        enabled()
        .add("admin", "trial_started", 5, None)
        .add("admin", "plan_upgraded", 2, None, plan="pro")
    )
    status = run(cfg, log, res("admin"))["c1"]
    assert status.paid
    assert not status.trial_active and not status.trial_expired


def test_blocked_users_and_seat_assignment(cfg) -> None:
    log = enabled().add("admin", "auto_seat_allocation_toggled", 50, None, enabled=False)
    log.add("u7", "user_blocked_no_seat", 5, "priv").add("u8", "user_blocked_no_seat", 4, "priv")
    log.add("admin", "seat_added", 3, None, target_user="u8")
    log.add("u9", "user_blocked_no_seat", 40, "priv")
    status = run(cfg, log, res("admin", "u7", "u8", "u9"))["c1"]
    assert status.blocked_users == ("u7",)
    assert status.auto_allocation_off


def test_reblocked_after_seat_counts_again(cfg) -> None:
    log = enabled().add("admin", "seat_added", 6, None, target_user="u7")
    log.add("u7", "user_blocked_no_seat", 2, "priv")
    assert run(cfg, log, res("admin", "u7"))["c1"].blocked_users == ("u7",)


def test_auto_allocation_toggled_back_on(cfg) -> None:
    log = enabled().add("admin", "auto_seat_allocation_toggled", 10, None, enabled=False)
    log.add("admin", "auto_seat_allocation_toggled", 2, None, enabled=True)
    assert not run(cfg, log, res("admin"))["c1"].auto_allocation_off


def test_companies_without_events_get_empty_status(cfg) -> None:
    status = run(cfg, EventLog(), {}, companies=(C1, company("c2")))
    assert status["c2"].seats_used == 0
    assert status["c2"].free_cap == 5


def test_future_events_are_ignored(cfg) -> None:
    log = enabled().add("u1", "pr_authored_private", -2, "priv")
    assert run(cfg, log, res("u1", "admin"))["c1"].seats_used == 0


def test_active_series_and_crossing() -> None:
    series = active_series({"a": [0, 10], "b": [35], "c": [5, 6, 7]}, history=40, window=30)
    assert series[0] == 2
    assert series[6] == 3
    assert series[36] == 0
    assert latest_crossing([3, 3, 6, 6, 2], cap=5) == 3  # index = days ago; run began 3 days ago
    assert latest_crossing([6, 6, 6], cap=5) == 2  # over cap since before the series began
    assert latest_crossing([1, 2], cap=5) is None
    assert active_series({"z": [500]}, history=10, window=30) == [0] * 11


def test_rollup_sums_children_and_keeps_soonest_trial(cfg) -> None:
    child = company("c2", parent_id="c1")
    log = enabled()
    for i in range(6):
        log.add(f"a{i}", "pr_authored_private", 3, "priv")
    for i in range(2):
        log.add(f"b{i}", "pr_authored_private", 3, "priv")
    resolutions = {**res(*[f"a{i}" for i in range(6)], "admin"), **res("b0", "b1", company_id="c2")}
    per_company = run(cfg, log, resolutions, companies=(C1, child))
    acct = rollup_seats(per_company, {"c1": "c1", "c2": "c1"}, 5)["c1"]
    assert acct.seats_used == 8
    assert acct.over_cap
    assert acct.trial_active and acct.trial_days_left == 11
    single = rollup_seats({"c3": per_company["c2"]}, {"c3": "c3"}, 5)["c3"]
    assert single.entity_id == "c3" and single.seats_used == 2
