"""Deterministic synthetic data generator (companies, users, repos, events, truth)."""

from __future__ import annotations

import random
from datetime import timedelta

from signal_engine.config import Config
from signal_engine.generate.companies import generate_companies
from signal_engine.generate.events import generate_events
from signal_engine.generate.repos import generate_repos
from signal_engine.generate.truth import generate_truth
from signal_engine.generate.users import generate_users
from signal_engine.models import Company, Dataset, Opportunity


def account_root(companies: list[Company]) -> dict[str, str]:
    """Map every company id to its top-level parent id."""
    parent = {c.company_id: c.parent_id for c in companies}
    out = {}
    for cid in parent:
        root, seen = cid, set()
        while parent.get(root) and root not in seen:
            seen.add(root)
            root = parent[root]  # type: ignore[assignment]
        out[cid] = root
    return out


def generate_dataset(
    cfg: Config,
    n_companies: int = 400,
    n_users: int = 5000,
    n_events: int = 150_000,
    seed: int | None = None,
) -> Dataset:
    """Generate a full synthetic dataset. Same seed and sizes give identical data."""
    rng = random.Random(cfg.seed if seed is None else seed)
    companies, plans = generate_companies(rng, n_companies)
    users = generate_users(rng, companies, plans, n_users)
    repos = generate_repos(rng, companies, plans, users)
    events, upgraded = generate_events(rng, companies, plans, users, repos, n_events, cfg.now)
    truth = generate_truth(rng, companies, plans, set(upgraded))
    roots = account_root(companies)
    opportunities = [
        Opportunity(roots[cid], "multi_team_spread", cfg.now - timedelta(days=rng.randint(3, 20)))
        for cid in sorted(upgraded)[:3]
    ]
    return Dataset(
        companies=tuple(companies),
        users=tuple(users.users),
        repos=tuple(repos.repos),
        manifests=tuple(repos.manifests),
        events=tuple(events),
        truth=tuple(truth),
        opportunities=tuple(opportunities),
    )
