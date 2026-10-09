"""Frozen dataclasses for every entity the engine reads or produces.

Event names mirror the documented CodSpeed product surface, but every internal
event name is a hypothesis for this independent demo, not a CodSpeed schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

EVENT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "acquisition": ("signed_up", "dashboard_login", "docs_visit", "demo_requested"),
    "activation": (
        "github_app_installed",
        "repo_imported",
        "setup_started",
        "wizard_pr_opened",
        "wizard_pr_merged",
        "workflow_committed",
        "backtest_run",
        "baseline_created",
        "first_pr_report",
    ),
    "depth": (
        "run_completed",
        "cli_auth_login",
        "local_upload",
        "sharding_enabled",
        "partial_runs_enabled",
        "mongodb_instrument_enabled",
    ),
    "value": (
        "regression_detected",
        "regression_acknowledged",
        "improvement_detected",
        "required_check_enabled",
        "informational_check_enabled",
        "threshold_changed",
        "flamegraph_viewed",
    ),
    "ai": (
        "codspeedbot_mention",
        "codspeedbot_fix_pr_opened",
        "codspeedbot_fix_pr_merged",
        "wizard_disabled",
        "mcp_connected",
        "mcp_tool_call",
        "skill_installed",
    ),
    "hygiene": ("benchmark_ignored", "benchmark_archived", "seat_removed", "repo_deleted"),
    "monetization": (
        "pr_authored_private",
        "report_viewed",
        "free_limit_exceeded",
        "trial_started",
        "trial_ending",
        "user_blocked_no_seat",
        "seat_added",
        "auto_seat_allocation_toggled",
        "plan_upgraded",
        "macro_runner_minutes",
        "ryzen_requested",
        "runner_budget_set",
    ),
    "enterprise": (
        "pricing_page_view",
        "enterprise_page_view",
        "sso_page_view",
        "trust_center_visit",
        "soc2_report_requested",
        "security_docs_visit",
    ),
}

EVENT_TYPES: frozenset[str] = frozenset(t for ts in EVENT_CATEGORIES.values() for t in ts)
EVENT_CATEGORY: dict[str, str] = {t: c for c, ts in EVENT_CATEGORIES.items() for t in ts}

ACCOUNT_STAGES: tuple[str, ...] = (
    "signed_up",
    "installed",
    "activated",
    "engaged",
    "pql",
    "pqa",
    "opportunity",
)

PERSONAS: tuple[str, ...] = (
    "ic_engineer",
    "staff_principal",
    "eng_manager",
    "platform_devex",
    "vp_cto",
)


@dataclass(frozen=True, slots=True)
class Company:
    """A company (legal entity) with firmographics; subsidiaries point at a parent."""

    company_id: str
    name: str
    domain: str
    alias_domains: tuple[str, ...]
    github_orgs: tuple[str, ...]
    industry: str
    region: str
    employee_count: int
    eng_headcount: int
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class User:
    """A person (or bot) known to the product."""

    user_id: str
    email: str
    name: str
    title: str
    github_login: str
    github_orgs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Repo:
    """A GitHub repository owned by an org or a personal account."""

    repo_id: str
    owner: str
    name: str
    is_private: bool
    language: str


@dataclass(frozen=True, slots=True)
class Manifest:
    """One file from a repository that the scanner inspects."""

    repo_id: str
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class Event:
    """A product-usage event."""

    event_id: int
    user_id: str
    ts: datetime
    event_type: str
    repo_id: str | None
    props: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """Hidden future outcome labels. Only evaluate.py may read these."""

    company_id: str
    converted_to_paid: bool
    expanded: bool
    churned: bool


@dataclass(frozen=True, slots=True)
class Opportunity:
    """A play the team accepted for an account (simulated CRM flag)."""

    account_id: str
    play_id: str
    accepted_at: datetime


@dataclass(frozen=True, slots=True)
class Dataset:
    """Everything the pipeline reads, in memory."""

    companies: tuple[Company, ...]
    users: tuple[User, ...]
    repos: tuple[Repo, ...]
    manifests: tuple[Manifest, ...]
    events: tuple[Event, ...]
    truth: tuple[GroundTruth, ...] = ()
    opportunities: tuple[Opportunity, ...] = ()


@dataclass(frozen=True, slots=True)
class Resolution:
    """Where a user resolved to, how, and with what confidence."""

    user_id: str
    company_id: str | None
    account_id: str | None
    method: str
    confidence: float
    is_bot: bool


@dataclass(frozen=True, slots=True)
class IdentityReport:
    """Aggregate quality metrics for identity resolution (bots excluded)."""

    people: int
    bots_excluded: int
    by_method: Mapping[str, int]
    unresolved_rate: float
    fallback_rate: float


@dataclass(frozen=True, slots=True)
class IdentityResult:
    """Per-user resolutions plus the aggregate report."""

    resolutions: Mapping[str, Resolution]
    report: IdentityReport
    org_to_company: Mapping[str, str]
    company_to_account: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RepoScan:
    """Scanner verdict for one repository."""

    repo_id: str
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]
    codspeed: tuple[str, ...]
    status: str
    walltime_only: bool
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScanSummary:
    """Scanner verdicts rolled up to an account."""

    repos_scanned: int
    repos_codspeed: int
    repos_benchmarks_without_codspeed: int
    frameworks: tuple[str, ...]
    languages: tuple[str, ...]
    walltime_only: bool
    not_addressable_only: bool
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SeatStatus:
    """Active-user counting per the documented billing rules."""

    entity_id: str
    seats_used: int
    seats_7d_ago: int
    seats_30d_ago: int
    free_cap: int
    over_cap: bool
    cap_exceeded_at: datetime | None
    trial_started_at: datetime | None
    trial_active: bool
    trial_days_left: int | None
    trial_expired: bool
    paid: bool
    blocked_users: tuple[str, ...]
    auto_allocation_off: bool
    active_user_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UserFeatures:
    """Per-user aggregates used by lifecycle (PQL) and the buying committee."""

    user_id: str
    account_id: str
    run_days: tuple[int, ...]
    intent: float
    value_score: float
    event_count: int
    first_ts: Mapping[str, datetime]
    last_ts: datetime | None


@dataclass(frozen=True, slots=True)
class AccountFeatures:
    """Per-account feature vector across 7/14/30/90-day windows."""

    account_id: str
    counts: Mapping[int, Mapping[str, int]]
    prev_week: Mapping[str, int]
    decayed: Mapping[str, float]
    first_ts: Mapping[str, datetime]
    last_ts: Mapping[str, datetime]
    active_days_30: int
    people_30: int
    runs_30: int
    runs_prev_30: int
    instruments_30: Mapping[str, int]
    graviton_minutes_mtd: float
    ryzen_minutes_mtd: float
    projected_graviton_minutes: float
    companies_active_30: tuple[str, ...]
    mcp_tools_30: Mapping[str, int]
    ignored_reasons_30: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StageEntry:
    """One lifecycle transition: entity entered stage at a timestamp."""

    entity_type: str
    entity_id: str
    stage: str
    entered_at: datetime


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    """Lifecycle verdicts for accounts and users."""

    account_stage: Mapping[str, str]
    account_entries: Mapping[str, Mapping[str, datetime]]
    engaged_users: Mapping[str, tuple[str, ...]]
    pql_users: Mapping[str, tuple[str, ...]]
    entries: tuple[StageEntry, ...]


@dataclass(frozen=True, slots=True)
class Score:
    """Fit, intent and priority for one account in one week."""

    account_id: str
    week: str
    fit: float
    intent: float
    priority: float
    intent_delta: float
    priority_delta: float
    reasons: tuple[str, ...]
    fit_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlayMatch:
    """A play the account qualifies for, with the message filled in."""

    play_id: str
    name: str
    owner: str
    sla_hours: int
    priority: int
    action: str
    message: str


@dataclass(frozen=True, slots=True)
class RevenueEstimate:
    """Expansion opportunity in USD ARR, with the assumptions spelled out."""

    projected_users: int
    seat_arr_annual_billing: float
    seat_arr_monthly_billing: float
    projected_overage_minutes: float
    runner_arr: float
    enterprise_uplift: bool
    total_arr: float
    assumptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Contact:
    """A person in the buying committee."""

    user_id: str
    name: str
    title: str
    persona: str
    email: str


@dataclass(frozen=True, slots=True)
class BuyingCommittee:
    """Champion, technical evaluator and likely economic buyer."""

    champion: Contact | None
    technical_evaluator: Contact | None
    economic_buyer: Contact | None


@dataclass(frozen=True, slots=True)
class AccountView:
    """Everything the outputs need about one account for one week."""

    account_id: str
    name: str
    slug: str
    domain: str
    industry: str
    region: str
    employee_count: int
    eng_headcount: int
    child_names: tuple[str, ...]
    stage: str
    stage_entries: Mapping[str, datetime]
    churn_risk: bool
    churn_signals: tuple[str, ...]
    pqa: bool
    new_pqa: bool
    seats: SeatStatus
    scan: ScanSummary
    features: AccountFeatures | None
    score: Score
    plays: tuple[PlayMatch, ...]
    revenue: RevenueEstimate
    committee: BuyingCommittee
    people: tuple[Contact, ...]
    pql_users: tuple[str, ...]
    engaged_users: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WeekResult:
    """Output of one weekly pipeline run."""

    week: str
    as_of: datetime
    accounts: tuple[AccountView, ...]
    identity: IdentityReport
    lifecycle: LifecycleResult
    scans: Mapping[str, RepoScan]
    user_contacts: Mapping[str, Contact]
    resolutions: Mapping[str, Resolution]
