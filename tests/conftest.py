"""Shared fixtures and small factories for hand-built test data."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

import pytest

from signal_engine.config import Config, load_config
from signal_engine.generate import generate_dataset
from signal_engine.models import (
    AccountFeatures,
    BuyingCommittee,
    Company,
    Contact,
    Dataset,
    Event,
    Repo,
    RevenueEstimate,
    ScanSummary,
    SeatStatus,
    User,
    UserFeatures,
)

CFG = load_config()
NOW = CFG.now


@pytest.fixture(scope="session")
def cfg() -> Config:
    """The repository's real config."""
    return CFG


@pytest.fixture(scope="session")
def small_ds() -> Dataset:
    """A small generated dataset shared across tests."""
    return generate_dataset(CFG, n_companies=60, n_users=700, n_events=9000)


def ago(days: float, base: datetime = NOW) -> datetime:
    """Timestamp ``days`` before ``base``."""
    return base - timedelta(days=days)


def company(cid: str = "c1", **kw: Any) -> Company:
    """A company with sensible defaults."""
    base = {
        "company_id": cid,
        "name": f"Acme {cid}",
        "domain": f"acme{cid}.example",
        "alias_domains": (),
        "github_orgs": (f"acme{cid}",),
        "industry": "devtools",
        "region": "NA",
        "employee_count": 200,
        "eng_headcount": 80,
        "parent_id": None,
    }
    base.update(kw)
    return Company(**base)


def user(
    uid: str,
    email: str,
    orgs: Iterable[str] = (),
    title: str = "Software Engineer",
    login: str | None = None,
) -> User:
    """A user with sensible defaults."""
    return User(uid, email, f"Pat {uid}", title, login or f"login{uid}", tuple(orgs))


def repo(rid: str, private: bool = True, owner: str = "acmec1") -> Repo:
    """A repo."""
    return Repo(rid, owner, f"name-{rid}", private, "python")


class EventLog:
    """Builds a time-ordered event tuple with sequential ids."""

    def __init__(self) -> None:
        self.rows: list[tuple[datetime, str, str, str | None, dict[str, Any]]] = []

    def add(
        self, uid: str, etype: str, days_ago: float, repo_id: str | None = None, **props: Any
    ) -> EventLog:
        """Append an event ``days_ago`` before NOW."""
        self.rows.append((ago(days_ago), uid, etype, repo_id, props))
        return self

    def events(self) -> tuple[Event, ...]:
        """Sorted events."""
        rows = sorted(self.rows, key=lambda r: (r[0], r[1], r[2]))
        return tuple(Event(i, u, ts, t, r, p) for i, (ts, u, t, r, p) in enumerate(rows, start=1))


def features(**kw: Any) -> AccountFeatures:
    """AccountFeatures with empty defaults."""
    base: dict[str, Any] = {
        "account_id": "c1",
        "counts": {7: {}, 14: {}, 30: {}, 90: {}},
        "prev_week": {},
        "decayed": {},
        "first_ts": {},
        "last_ts": {},
        "active_days_30": 0,
        "people_30": 0,
        "runs_30": 0,
        "runs_prev_30": 0,
        "instruments_30": {},
        "graviton_minutes_mtd": 0.0,
        "ryzen_minutes_mtd": 0.0,
        "projected_graviton_minutes": 0.0,
        "companies_active_30": (),
        "mcp_tools_30": {},
        "ignored_reasons_30": (),
    }
    base.update(kw)
    return AccountFeatures(**base)


def user_features(uid: str = "u1", **kw: Any) -> UserFeatures:
    """UserFeatures with empty defaults."""
    base: dict[str, Any] = {
        "user_id": uid,
        "account_id": "c1",
        "run_days": (),
        "intent": 0.0,
        "value_score": 0.0,
        "event_count": 0,
        "first_ts": {},
        "last_ts": None,
    }
    base.update(kw)
    return UserFeatures(**base)


def seats(**kw: Any) -> SeatStatus:
    """SeatStatus with empty defaults."""
    base: dict[str, Any] = {
        "entity_id": "c1",
        "seats_used": 0,
        "seats_7d_ago": 0,
        "seats_30d_ago": 0,
        "free_cap": 5,
        "over_cap": False,
        "cap_exceeded_at": None,
        "trial_started_at": None,
        "trial_active": False,
        "trial_days_left": None,
        "trial_expired": False,
        "paid": False,
        "blocked_users": (),
        "auto_allocation_off": False,
        "active_user_ids": (),
    }
    base.update(kw)
    return SeatStatus(**base)


def scan(**kw: Any) -> ScanSummary:
    """ScanSummary with empty defaults."""
    base: dict[str, Any] = {
        "repos_scanned": 0,
        "repos_codspeed": 0,
        "repos_benchmarks_without_codspeed": 0,
        "frameworks": (),
        "languages": (),
        "walltime_only": False,
        "not_addressable_only": False,
        "evidence": (),
    }
    base.update(kw)
    return ScanSummary(**base)


def revenue(**kw: Any) -> RevenueEstimate:
    """RevenueEstimate with zero defaults."""
    base: dict[str, Any] = {
        "projected_users": 0,
        "seat_arr_annual_billing": 0.0,
        "seat_arr_monthly_billing": 0.0,
        "projected_overage_minutes": 0.0,
        "runner_arr": 0.0,
        "enterprise_uplift": False,
        "total_arr": 0.0,
        "assumptions": (),
    }
    base.update(kw)
    return RevenueEstimate(**base)


def contact(uid: str, persona: str = "ic_engineer", name: str | None = None) -> Contact:
    """A contact."""
    return Contact(uid, name or f"Robin {uid}", "Engineer", persona, f"{uid}@acme.example")


def committee(champion: Contact | None = None, buyer: Contact | None = None) -> BuyingCommittee:
    """A buying committee."""
    return BuyingCommittee(champion, None, buyer)
