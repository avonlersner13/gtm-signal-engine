"""Revenue math from documented pricing."""

from __future__ import annotations

import pytest

from conftest import NOW, ago, company, features, scan, seats
from signal_engine.revenue import estimate_revenue, has_security_signal, projected_users


def test_projected_users_growth_and_blocked(cfg) -> None:
    s = seats(seats_used=7, seats_30d_ago=4, blocked_users=("a", "b"))
    assert projected_users(s, features(), scan(), 100, cfg) == 7 + 3 + 2
    shrinking = seats(seats_used=6, seats_30d_ago=9)
    assert projected_users(shrinking, features(), scan(), 100, cfg) == 6


def test_projected_users_market_only_uses_adoption(cfg) -> None:
    market = scan(repos_benchmarks_without_codspeed=1)
    assert projected_users(seats(), None, market, 200, cfg) == 16
    assert projected_users(seats(), None, scan(), 200, cfg) == 0
    assert projected_users(seats(), features(), market, 200, cfg) == 0


def test_seat_arr_annual_and_monthly(cfg) -> None:
    r = estimate_revenue(
        seats(seats_used=8, seats_30d_ago=8), features(), scan(), [company()], cfg, NOW
    )
    assert r.projected_users == 8
    assert r.seat_arr_annual_billing == 8 * 15 * 12
    assert r.seat_arr_monthly_billing == 8 * 20 * 12
    assert r.total_arr == 1440.0
    assert any("$15/user/month" in a for a in r.assumptions)


def test_no_seat_revenue_within_free_plan(cfg) -> None:
    r = estimate_revenue(
        seats(seats_used=5, seats_30d_ago=5), features(), scan(), [company()], cfg, NOW
    )
    assert r.seat_arr_annual_billing == 0.0
    assert r.total_arr == 0.0


def test_runner_overage_and_ryzen(cfg) -> None:
    f = features(projected_graviton_minutes=1600.0, first_ts={"ryzen_requested": ago(2)})
    r = estimate_revenue(seats(), f, scan(), [company()], cfg, NOW)
    assert r.projected_overage_minutes == 1000.0
    expected = 1000 * 0.032 * 12 + 1600 * 0.25 * 0.06 * 12
    assert r.runner_arr == pytest.approx(expected, abs=0.01)
    assert r.total_arr == r.runner_arr


def test_enterprise_flag_from_security_signals(cfg) -> None:
    assert has_security_signal(features(last_ts={"sso_page_view": ago(5)}), NOW)
    assert not has_security_signal(features(last_ts={"sso_page_view": ago(45)}), NOW)
    assert not has_security_signal(None, NOW)
    r = estimate_revenue(
        seats(), features(last_ts={"soc2_report_requested": ago(1)}), scan(), [company()], cfg, NOW
    )
    assert r.enterprise_uplift
    assert r.total_arr == 0.0


def test_market_only_assumption_text(cfg) -> None:
    fam = [company(eng_headcount=100)]
    r = estimate_revenue(seats(), None, scan(repos_benchmarks_without_codspeed=2), fam, cfg, NOW)
    assert r.projected_users == 8
    assert r.seat_arr_annual_billing == 8 * 15 * 12
    assert any("adoption (market-only)" in a for a in r.assumptions)
