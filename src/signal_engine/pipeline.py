"""Orchestration: identity -> scan -> seats -> features -> lifecycle -> score -> plays.

:func:`compute_week` is pure (in-memory dataset in, :class:`WeekResult` out) so it can
be benchmarked and tested without I/O. :func:`run_week` adds SQLite persistence and
writes the output files.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from signal_engine.buying_committee import buying_committee, make_contact
from signal_engine.config import Config
from signal_engine.features import FeatureSet, compute_features
from signal_engine.generate.companies import slugify
from signal_engine.identity import resolve_identities
from signal_engine.ingest import store
from signal_engine.lifecycle import run_lifecycle
from signal_engine.models import (
    AccountView,
    Company,
    Contact,
    Dataset,
    Event,
    IdentityResult,
    LifecycleResult,
    RepoScan,
    Score,
    SeatStatus,
    StageEntry,
    UserFeatures,
    WeekResult,
)
from signal_engine.plays import PlayContext, apply_founder_capacity, churn_signals, match_plays
from signal_engine.revenue import estimate_revenue
from signal_engine.scanner import scan_manifests, summarize
from signal_engine.scoring import AccountInputs, score_account
from signal_engine.seats import compute_seats, empty_status, rollup_seats


@dataclass(frozen=True, slots=True)
class _Accounts:
    """Lookups grouped by parent account, built once."""

    family: Mapping[str, list[Company]]
    repos: Mapping[str, list[str]]
    people: Mapping[str, list[str]]


@dataclass(frozen=True, slots=True)
class WeekState:
    """Shared per-run state (everything computed before per-account scoring)."""

    groups: _Accounts
    scans: Mapping[str, RepoScan]
    seats: Mapping[str, SeatStatus]
    feats: FeatureSet
    lifecycle: LifecycleResult
    private: Mapping[str, bool]
    orgs_by_user: Mapping[str, tuple[str, ...]]
    contacts: Mapping[str, Contact]
    previous: Mapping[str, Score]
    cfg: Config
    as_of: datetime
    week: str
    previous_stages: Mapping[str, str] | None
    churn_params: Mapping[str, float]


def events_until(events: Sequence[Event], as_of: datetime) -> Sequence[Event]:
    """Time-ordered events with ts <= as_of (binary search, no copy of the tail)."""
    cut = bisect.bisect_right(events, as_of, key=lambda e: e.ts)
    return events[:cut]


def _group_accounts(ds: Dataset, identity: IdentityResult) -> _Accounts:
    roots = identity.company_to_account
    family: dict[str, list[Company]] = defaultdict(list)
    for c in sorted(ds.companies, key=lambda c: (c.parent_id is not None, c.company_id)):
        family[roots[c.company_id]].append(c)
    login_account = {
        u.github_login.lower(): identity.resolutions[u.user_id].account_id for u in ds.users
    }
    repos: dict[str, list[str]] = defaultdict(list)
    for r in ds.repos:
        owner = r.owner.lower()
        company = identity.org_to_company.get(owner)
        account = roots.get(company) if company else login_account.get(owner)
        if account:
            repos[account].append(r.repo_id)
    people: dict[str, list[str]] = defaultdict(list)
    for res in identity.resolutions.values():
        if res.account_id and not res.is_bot:
            people[res.account_id].append(res.user_id)
    return _Accounts(family, repos, people)


def _orgs_active(
    user_ids: set[str],
    orgs_by_user: Mapping[str, tuple[str, ...]],
    family: Sequence[Company],
    companies_active: int,
) -> int:
    own = {o.lower() for c in family for o in c.github_orgs}
    orgs = {o.lower() for uid in user_ids for o in orgs_by_user.get(uid, ()) if o.lower() in own}
    return max(len(orgs), companies_active)


def _contacts(
    people: Sequence[str], contacts: Mapping[str, Contact], users: Mapping[str, UserFeatures]
) -> tuple[Contact, ...]:
    def rank(uid: str) -> tuple[float, str]:
        return (-(users[uid].value_score if uid in users else 0.0), uid)

    return tuple(contacts[uid] for uid in sorted(people, key=rank))


@dataclass(frozen=True, slots=True)
class AccountDraft:
    """Scored account plus the context its plays are matched against."""

    inputs: AccountInputs
    score: Score
    people: tuple[Contact, ...]
    context: PlayContext


def prepare_week(
    ds: Dataset,
    cfg: Config,
    as_of: datetime,
    week: str,
    previous: Mapping[str, Score] | None = None,
    previous_stages: Mapping[str, str] | None = None,
) -> tuple[WeekState, IdentityResult]:
    """identity -> scan -> seats -> features -> lifecycle, plus per-account lookups."""
    events = events_until(ds.events, as_of)
    identity = resolve_identities(ds.users, ds.companies, events, cfg)
    scans = scan_manifests(ds.manifests)
    seats = rollup_seats(
        compute_seats(events, identity.resolutions, ds.repos, ds.companies, cfg, as_of),
        identity.company_to_account,
        cfg.pricing.free_cap,
    )
    feats = compute_features(events, identity.resolutions, cfg, as_of)
    lifecycle = run_lifecycle(feats.accounts, feats.users, seats, ds.opportunities, cfg, as_of)
    contacts = {
        u.user_id: make_contact(u, cfg)
        for u in ds.users
        if not identity.resolutions[u.user_id].is_bot
    }
    wk = WeekState(
        groups=_group_accounts(ds, identity),
        scans=scans,
        seats=seats,
        feats=feats,
        lifecycle=lifecycle,
        private={r.repo_id: r.is_private for r in ds.repos},
        orgs_by_user={u.user_id: u.github_orgs for u in ds.users},
        contacts=contacts,
        previous=previous or {},
        cfg=cfg,
        as_of=as_of,
        week=week,
        previous_stages=previous_stages,
        churn_params=next((p.params for p in cfg.plays if p.trigger == "churn_risk"), {}),
    )
    return wk, identity


def compute_week(
    ds: Dataset,
    cfg: Config,
    as_of: datetime,
    week: str,
    previous: Mapping[str, Score] | None = None,
    previous_stages: Mapping[str, str] | None = None,
) -> WeekResult:
    """Run the full weekly pipeline in memory.

    ``previous`` (last week's scores) drives deltas; ``previous_stages`` decides which PQAs
    are new this week (without it, a PQA is new if it entered the stage in the last 7 days).
    """
    wk, identity = prepare_week(ds, cfg, as_of, week, previous, previous_stages)
    lifecycle = wk.lifecycle
    views = [_account_view(acct, family, wk) for acct, family in sorted(wk.groups.family.items())]
    views.sort(key=lambda v: (-v.score.priority, v.account_id))
    views = apply_founder_capacity(views, cfg.org["founder_weekly_capacity"])
    churn = tuple(
        StageEntry("account", v.account_id, "churn_risk", as_of) for v in views if v.churn_risk
    )
    return WeekResult(
        week=week,
        as_of=as_of,
        accounts=tuple(views),
        identity=identity.report,
        lifecycle=replace(lifecycle, entries=lifecycle.entries + churn),
        scans=wk.scans,
        user_contacts=wk.contacts,
        resolutions=identity.resolutions,
    )


def account_inputs(acct: str, family: list[Company], wk: WeekState) -> AccountInputs:
    """Everything scoring needs about one account."""
    repo_ids = wk.groups.repos.get(acct, [])
    scan = summarize(wk.scans[r] for r in repo_ids if r in wk.scans)
    features = wk.feats.accounts.get(acct)
    seat = wk.seats.get(acct, empty_status(acct, wk.cfg.pricing.free_cap))
    engaged = wk.lifecycle.engaged_users.get(acct, ())
    n_companies = len(features.companies_active_30) if features else 0
    orgs = _orgs_active(
        set(seat.active_user_ids) | set(engaged), wk.orgs_by_user, family, n_companies
    )
    ratio = sum(wk.private[r] for r in repo_ids) / len(repo_ids) if repo_ids else None
    return AccountInputs(acct, family, features, seat, scan, ratio, len(engaged), orgs)


def draft_account(acct: str, family: list[Company], wk: WeekState) -> AccountDraft:
    """Score one account and build its committee, revenue estimate and play context."""
    cfg, users = wk.cfg, wk.feats.users
    inputs = account_inputs(acct, family, wk)
    score = score_account(inputs, cfg, wk.week, wk.previous.get(acct))
    people = _contacts(wk.groups.people.get(acct, []), wk.contacts, users)
    ctx = PlayContext(
        name=family[0].name,
        stage=wk.lifecycle.account_stage.get(acct, "none"),
        fit=score.fit,
        seats=inputs.seats,
        scan=inputs.scan,
        features=inputs.features,
        committee=buying_committee(people, users),
        revenue=estimate_revenue(inputs.seats, inputs.features, inputs.scan, family, cfg, wk.as_of),
        orgs_active=inputs.orgs_active,
        as_of=wk.as_of,
        founder=cfg.founder_name,
        free_minutes=cfg.pricing.graviton_free_minutes,
    )
    return AccountDraft(inputs, score, people, ctx)


def _account_view(acct: str, family: list[Company], wk: WeekState) -> AccountView:
    lc = wk.lifecycle
    root = family[0]
    draft = draft_account(acct, family, wk)
    ctx = draft.context
    plays = match_plays(ctx, wk.cfg)
    churn = tuple(churn_signals(ctx, wk.churn_params))
    entered = lc.account_entries.get(acct, {})
    pqa_at = entered.get("pqa")
    return AccountView(
        account_id=acct,
        name=root.name,
        slug=slugify(root.name),
        domain=root.domain,
        industry=root.industry,
        region=root.region,
        employee_count=sum(c.employee_count for c in family),
        eng_headcount=sum(c.eng_headcount for c in family),
        child_names=tuple(c.name for c in family[1:]),
        stage=ctx.stage,
        stage_entries=entered,
        churn_risk=bool(churn),
        churn_signals=churn,
        pqa=pqa_at is not None,
        new_pqa=_is_new_pqa(acct, pqa_at, wk),
        seats=ctx.seats,
        scan=ctx.scan,
        features=ctx.features,
        score=draft.score,
        plays=plays,
        revenue=ctx.revenue,
        committee=ctx.committee,
        people=draft.people,
        pql_users=lc.pql_users.get(acct, ()),
        engaged_users=lc.engaged_users.get(acct, ()),
    )


def _is_new_pqa(acct: str, pqa_at: datetime | None, wk: WeekState) -> bool:
    if pqa_at is None:
        return False
    if wk.previous_stages:
        return wk.previous_stages.get(acct) not in ("pqa", "opportunity")
    return pqa_at > wk.as_of - timedelta(days=7)


def run_week(db_path: Path | str, cfg: Config, week: date, out_dir: Path | str) -> WeekResult:
    """Load from SQLite, compute the week, persist history and write all outputs."""
    from signal_engine.outputs import write_outputs

    conn = store.connect(db_path)
    try:
        ds = store.load_dataset(conn)
        week_str = week.isoformat()
        prev_week = store.previous_week(conn, week_str)
        previous = store.load_scores(conn, prev_week) if prev_week else {}
        stages = store.load_stages(conn, prev_week) if prev_week else {}
        result = compute_week(ds, cfg, cfg.as_of_for_week(week), week_str, previous, stages)
        store.save_week(conn, result)
    finally:
        conn.close()
    write_outputs(result, cfg, Path(out_dir))
    return result
