"""The synthetic generator: determinism, sizes, safety of names/domains, journeys."""

from __future__ import annotations

import random
from collections import Counter

from signal_engine.generate import account_root, generate_dataset
from signal_engine.generate.companies import generate_companies
from signal_engine.generate.repos import manifest_files
from signal_engine.models import EVENT_TYPES
from signal_engine.scanner import scan_repo


def test_same_seed_same_data(cfg) -> None:
    a = generate_dataset(cfg, 40, 400, 4000, seed=7)
    b = generate_dataset(cfg, 40, 400, 4000, seed=7)
    c = generate_dataset(cfg, 40, 400, 4000, seed=8)
    assert a == b
    assert a != c


def test_sizes_and_ordering(small_ds) -> None:
    assert len(small_ds.companies) == 60
    assert len(small_ds.users) == 700
    assert len(small_ds.events) >= 9000
    ts = [e.ts for e in small_ds.events]
    assert ts == sorted(ts)
    assert [e.event_id for e in small_ds.events] == list(range(1, len(ts) + 1))
    assert {e.event_type for e in small_ds.events} <= EVENT_TYPES


def test_only_fake_example_domains(small_ds) -> None:
    assert all(c.domain.endswith(".example") for c in small_ds.companies)
    assert all(d.endswith(".example") for c in small_ds.companies for d in c.alias_domains)
    assert all(u.email.endswith(".example") for u in small_ds.users)


def test_events_never_after_now(small_ds, cfg) -> None:
    assert max(e.ts for e in small_ds.events) <= cfg.now


def test_bots_and_subsidiaries_exist(cfg) -> None:
    ds = generate_dataset(cfg, 200, 2000, 5000)
    assert any(u.github_login.endswith("[bot]") for u in ds.users)
    assert any(c.parent_id for c in ds.companies)
    roots = account_root(list(ds.companies))
    assert all(roots[c.company_id] != c.company_id for c in ds.companies if c.parent_id)


def test_key_journeys_appear(cfg) -> None:
    ds = generate_dataset(cfg, 200, 2500, 30000)
    types = Counter(e.event_type for e in ds.events)
    for etype in (
        "trial_started",
        "user_blocked_no_seat",
        "macro_runner_minutes",
        "mcp_connected",
        "benchmark_ignored",
        "soc2_report_requested",
        "plan_upgraded",
        "baseline_created",
    ):
        assert types[etype] > 0, etype
    assert any(t.converted_to_paid for t in ds.truth)
    assert any(t.churned for t in ds.truth)


def test_company_names_are_unique() -> None:
    companies, plans = generate_companies(random.Random(1), 500)
    assert len({c.name for c in companies}) == 500
    assert set(plans) == {c.company_id for c in companies}


def test_every_language_state_scans_as_intended() -> None:
    rng = random.Random(3)
    expected = {
        "none": {"no_benchmarks"},
        "bench": {"benchmarks_without_codspeed", "not_addressable"},
        "codspeed": {"codspeed", "not_addressable"},
    }
    for lang in ("python", "rust", "node", "go", "jvm", "scala", "cpp"):
        for state, ok in expected.items():
            for _ in range(15):
                files = manifest_files(rng, lang, state)
                result = scan_repo("r", files)
                if any("unparseable" in e for e in result.evidence):
                    continue
                assert result.status in ok, (lang, state, files)
