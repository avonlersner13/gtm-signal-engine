"""Identity resolution: users -> companies -> parent accounts, with confidence scores.

Waterfall (first match wins; confidences come from signals.toml):

1. corporate email domain               -> 1.0
2. known alias domain                   -> 0.95
3. GitHub org membership                -> 0.9
4. freemail address + GitHub org        -> 0.8
5. commit-email domain seen on PRs      -> 0.7
6. unresolved                           -> 0

Subsidiaries roll up to their top-level parent (the account); the child company is
kept for reporting. ``[bot]`` users are resolved but excluded from people counts.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from signal_engine.config import Config
from signal_engine.models import (
    Company,
    Event,
    IdentityReport,
    IdentityResult,
    Resolution,
    User,
)

UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class DomainIndex:
    """Lookup tables built once per run."""

    corporate: Mapping[str, str]
    alias: Mapping[str, str]
    org: Mapping[str, str]
    companies: Sequence[Company]


def normalize_domain(raw: str) -> str:
    """Lower-case a domain or email and strip whitespace, ``www.`` and trailing dots."""
    domain = raw.strip().lower().rsplit("@", 1)[-1].rstrip(".")
    return domain[4:] if domain.startswith("www.") else domain


def _candidates(domain: str) -> list[str]:
    """``eu.mail.acme.example`` -> itself, ``mail.acme.example``, ``acme.example``."""
    parts = domain.split(".")
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]


def account_roots(companies: Iterable[Company]) -> dict[str, str]:
    """Map each company id to its top-level parent (cycle-safe)."""
    parent = {c.company_id: c.parent_id for c in companies}
    roots: dict[str, str] = {}
    for cid in parent:
        node, seen = cid, {cid}
        while (nxt := parent.get(node)) and nxt in parent and nxt not in seen:
            seen.add(nxt)
            node = nxt
        roots[cid] = node
    return roots


def build_index(companies: Sequence[Company]) -> DomainIndex:
    """Build domain, alias and org lookups in one pass."""
    corporate, alias, org = {}, {}, {}
    for c in companies:
        corporate[normalize_domain(c.domain)] = c.company_id
        for d in c.alias_domains:
            alias[normalize_domain(d)] = c.company_id
        for o in c.github_orgs:
            org[o.lower()] = c.company_id
    return DomainIndex(corporate, alias, org, tuple(companies))


def find_company(companies: Sequence[Company], company_id: str) -> Company | None:
    """Return the company with ``company_id``, or None if it is unknown."""
    for company in companies:
        if company.company_id == company_id:
            return company
    return None


def company_for_orgs(orgs: Sequence[str], companies: Sequence[Company]) -> str | None:
    """Company owning the first of ``orgs`` that any company lists (case-insensitive)."""
    for org in orgs:
        wanted = org.lower()
        owner = None
        for company in companies:
            for company_org in company.github_orgs:
                if company_org.lower() == wanted:
                    owner = company.company_id
        if owner is not None:
            return owner
    return None


def parent_account(company_id: str, companies: Sequence[Company]) -> str:
    """Walk up the parent chain to the top-level account (cycle-safe)."""
    node, seen = company_id, {company_id}
    while True:
        company = find_company(companies, node)
        parent = company.parent_id if company else None
        if not parent or parent in seen or find_company(companies, parent) is None:
            return node
        seen.add(parent)
        node = parent


def find_user(users: Sequence[User], user_id: str) -> User | None:
    """Return the user with ``user_id``, or None if there is no such user."""
    for user in users:
        if user.user_id == user_id:
            return user
    return None


def commit_domains(events: Iterable[Event], users: Sequence[User] | None = None) -> dict[str, str]:
    """Most frequent commit-email domain per user, from their PR events.

    When ``users`` is given, only PRs authored by one of those users are counted.
    """
    counts: dict[str, Counter[str]] = {}
    for event in events:
        if event.event_type != "pr_authored_private":
            continue
        domain = event.props.get("commit_email_domain")
        if not domain:
            continue
        if users is not None and event.user_id not in counts:
            author = find_user(users, event.user_id)
            if author is None:
                continue
        counts.setdefault(event.user_id, Counter())[normalize_domain(domain)] += 1
    return {uid: min(c.items(), key=lambda kv: (-kv[1], kv[0]))[0] for uid, c in counts.items()}


def _lookup(domain: str, table: Mapping[str, str]) -> str | None:
    for cand in _candidates(domain):
        if cand in table:
            return table[cand]
    return None


def resolve_user(
    user: User, index: DomainIndex, commit_domain: str | None, cfg: Config
) -> Resolution:
    """Run the waterfall for one user."""
    domain = normalize_domain(user.email)
    is_bot = user.github_login.endswith(cfg.bot_suffix)
    org_company = company_for_orgs(user.github_orgs, index.companies)
    conf = cfg.identity
    match: tuple[str | None, str, float] = (None, UNRESOLVED, 0.0)
    if domain not in cfg.freemail and (cid := _lookup(domain, index.corporate)):
        match = (cid, "corporate_domain", conf["corporate_domain"])
    elif domain not in cfg.freemail and (cid := _lookup(domain, index.alias)):
        match = (cid, "alias_domain", conf["alias_domain"])
    elif cid := org_company:
        method = "freemail_github_org" if domain in cfg.freemail else "github_org"
        match = (cid, method, conf[method])
    elif commit_domain and (
        cid := _lookup(commit_domain, index.corporate) or _lookup(commit_domain, index.alias)
    ):
        match = (cid, "commit_email_domain", conf["commit_email_domain"])
    company_id, method, confidence = match
    account_id = parent_account(company_id, index.companies) if company_id else None
    return Resolution(user.user_id, company_id, account_id, method, confidence, is_bot)


def identity_report(resolutions: Iterable[Resolution]) -> IdentityReport:
    """Unresolved and fallback rates over people (bots excluded)."""
    by_method: Counter[str] = Counter()
    bots = 0
    for r in resolutions:
        if r.is_bot:
            bots += 1
        else:
            by_method[r.method] += 1
    people = sum(by_method.values())
    unresolved = by_method.get(UNRESOLVED, 0)
    fallback = people - unresolved - by_method.get("corporate_domain", 0)
    return IdentityReport(
        people=people,
        bots_excluded=bots,
        by_method=dict(sorted(by_method.items())),
        unresolved_rate=round(unresolved / people, 4) if people else 0.0,
        fallback_rate=round(fallback / people, 4) if people else 0.0,
    )


def resolve_identities(
    users: Sequence[User], companies: Sequence[Company], events: Iterable[Event], cfg: Config
) -> IdentityResult:
    """Resolve every user to a company and parent account."""
    index = build_index(companies)
    commits = commit_domains(events, users)
    resolutions = {u.user_id: resolve_user(u, index, commits.get(u.user_id), cfg) for u in users}
    return IdentityResult(
        resolutions=resolutions,
        report=identity_report(resolutions.values()),
        org_to_company=index.org,
        company_to_account=account_roots(companies),
    )
