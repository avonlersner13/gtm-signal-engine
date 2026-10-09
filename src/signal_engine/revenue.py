"""Revenue opportunity per account in USD ARR, from documented pricing (pricing.toml).

* seat ARR       = projected active users x $15/user/month x 12 (annual billing);
                   the monthly-billing alternative ($20) is reported alongside.
                   Zero while the projection stays within the 5-user Free plan.
* runner ARR     = projected Graviton overage minutes x $0.032 x 12, plus Ryzen
                   minutes x $0.06 x 12 when Ryzen was requested.
* Enterprise     = flagged (price is "custom") on security-review or SSO signals.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from signal_engine.config import Config
from signal_engine.models import AccountFeatures, Company, RevenueEstimate, ScanSummary, SeatStatus

SECURITY_EVENTS = (
    "trust_center_visit",
    "soc2_report_requested",
    "sso_page_view",
    "security_docs_visit",
)


def projected_users(
    seats: SeatStatus, features: AccountFeatures | None, scan: ScanSummary, eng: int, cfg: Config
) -> int:
    """Seats now + growth over the lookback + blocked users; market-only uses adoption rate."""
    if seats.seats_used > 0:
        growth = max(0, seats.seats_used - seats.seats_30d_ago)
        return seats.seats_used + growth + len(seats.blocked_users)
    if features is None and scan.repos_benchmarks_without_codspeed > 0:
        return round(eng * cfg.pricing.outbound_adoption_rate)
    return 0


def has_security_signal(features: AccountFeatures | None, as_of: datetime, days: int = 30) -> bool:
    """Security-review or SSO activity within ``days``."""
    if features is None:
        return False
    cutoff = as_of - timedelta(days=days)
    return any(features.last_ts.get(e, cutoff) > cutoff for e in SECURITY_EVENTS)


def estimate_revenue(
    seats: SeatStatus,
    features: AccountFeatures | None,
    scan: ScanSummary,
    family: Sequence[Company],
    cfg: Config,
    as_of: datetime,
) -> RevenueEstimate:
    """Estimate seat and runner ARR plus the Enterprise flag, with assumptions."""
    p = cfg.pricing
    eng = sum(c.eng_headcount for c in family)
    users = projected_users(seats, features, scan, eng, cfg)
    billable = users if users > p.free_cap else 0
    seat_annual = billable * p.pro_annual * 12
    seat_monthly = billable * p.pro_monthly * 12
    projected = features.projected_graviton_minutes if features else 0.0
    overage = max(0.0, projected - p.graviton_free_minutes)
    ryzen_minutes = 0.0
    if features is not None and "ryzen_requested" in features.first_ts:
        ryzen_minutes = projected * p.ryzen_share_if_requested
    runner_arr = overage * p.graviton_rate * 12 + ryzen_minutes * p.ryzen_rate * 12
    enterprise = has_security_signal(features, as_of)
    assumptions = (
        f"Pro at ${p.pro_annual:.0f}/user/month billed annually (${p.pro_monthly:.0f} monthly); "
        f"Free covers up to {p.free_cap} active users",
        (
            f"projected users = {seats.seats_used} active now + growth over "
            f"{p.growth_lookback_days} days + {len(seats.blocked_users)} blocked"
        )
        if seats.seats_used
        else (
            f"projected users = {eng} engineers x {p.outbound_adoption_rate:.0%} adoption"
            " (market-only)"
        ),
        f"{p.graviton_free_minutes} free Graviton minutes/month, then ${p.graviton_rate}/min; "
        f"Ryzen ${p.ryzen_rate}/min ({p.ryzen_share_if_requested:.0%} of minutes once requested)",
        "Enterprise (SSO/SAML, SOC 2 reports, on-prem) is custom-priced: flagged, not valued",
    )
    return RevenueEstimate(
        projected_users=users,
        seat_arr_annual_billing=round(seat_annual, 2),
        seat_arr_monthly_billing=round(seat_monthly, 2),
        projected_overage_minutes=round(overage, 1),
        runner_arr=round(runner_arr, 2),
        enterprise_uplift=enterprise,
        total_arr=round(seat_annual + runner_arr, 2),
        assumptions=assumptions,
    )
