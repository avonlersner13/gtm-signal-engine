"""Shared, cached benchmark inputs. Data generation is never inside a measured call.

Sizes (CodSpeed's CPU simulation runs each benchmark once, so keep them bounded):

* small:  100 companies / 1,500 users / 20,000 events
* medium: 300 companies / 4,000 users / 60,000 events (identity, seats, features, e2e)
"""

from __future__ import annotations

from functools import cache

from signal_engine.config import Config, load_config
from signal_engine.generate import generate_dataset
from signal_engine.identity import resolve_identities
from signal_engine.models import Dataset, IdentityResult
from signal_engine.pipeline import events_until

SIZES = {
    "small": (100, 1_500, 20_000),
    "medium": (300, 4_000, 60_000),
}


@cache
def config() -> Config:
    """The repository config (loaded once per process)."""
    return load_config()


@cache
def dataset(size: str) -> Dataset:
    """Generated dataset for a named size (cached per process)."""
    companies, users, events = SIZES[size]
    return generate_dataset(config(), companies, users, events)


@cache
def identity(size: str) -> IdentityResult:
    """Identity resolution for a named size (cached per process)."""
    ds = dataset(size)
    cfg = config()
    return resolve_identities(ds.users, ds.companies, events_until(ds.events, cfg.now), cfg)
