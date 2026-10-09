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
    account: Mapping[str, str]


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
    """Build domain, alias, org and account lookups in one pass."""
    corporate, alias, org = {}, {}, {}
    for c in companies:
        corporate[normalize_domain(c.domain)] = c.company_id
        for d in c.alias_domains:
            alias[normalize_domain(d)] = c.company_id
        for o in c.github_orgs:
            org[o.lower()] = c.company_id
    return DomainIndex(corporate, alias, org, account_roots(companies))


def commit_domains(events: Iterable[Event]) -> dict[str, str]:
    """Most frequent commit-email domain per user, from PR events (one pass)."""
    counts: dict[str, Counter[str]] = {}
    for e in events:
        if e.event_type == "pr_authored_private":
            domain = e.props.get("commit_email_domain")
            if domain:
                counts.setdefault(e.user_id, Counter())[normalize_domain(domain)] += 1
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
    conf = cfg.identity
    match: tuple[str | None, str, float] = (None, UNRESOLVED, 0.0)
    if domain not in cfg.freemail and (cid := _lookup(domain, index.corporate)):
        match = (cid, "corporate_domain", conf["corporate_domain"])
    elif domain not in cfg.freemail and (cid := _lookup(domain, index.alias)):
        match = (cid, "alias_domain", conf["alias_domain"])
    elif cid := next(
        (index.org[o.lower()] for o in user.github_orgs if o.lower() in index.org), None
    ):
        method = "freemail_github_org" if domain in cfg.freemail else "github_org"
        match = (cid, method, conf[method])
    elif commit_domain and (
        cid := _lookup(commit_domain, index.corporate) or _lookup(commit_domain, index.alias)
    ):
        match = (cid, "commit_email_domain", conf["commit_email_domain"])
    company_id, method, confidence = match
    account_id = index.account.get(company_id) if company_id else None
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
    commits = commit_domains(events)
    resolutions = {u.user_id: resolve_user(u, index, commits.get(u.user_id), cfg) for u in users}
    return IdentityResult(
        resolutions=resolutions,
        report=identity_report(resolutions.values()),
        org_to_company=index.org,
        company_to_account=index.account,
    )
