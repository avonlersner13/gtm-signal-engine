"""CodSpeed benchmarks for each pipeline stage and the end-to-end weekly run.

Parametrize ids are stable on purpose: CodSpeed treats a renamed case as a new
benchmark and loses its history.
"""

from __future__ import annotations

import random

import pytest

from conftest import config, dataset, identity
from signal_engine.features import compute_features
from signal_engine.generate.repos import BUILDERS, manifest_files
from signal_engine.identity import resolve_identities
from signal_engine.lifecycle import run_lifecycle
from signal_engine.models import Manifest
from signal_engine.outputs.founder_brief import render_founder_brief
from signal_engine.pipeline import (
    account_inputs,
    compute_week,
    draft_account,
    events_until,
    prepare_week,
)
from signal_engine.plays import match_plays
from signal_engine.scanner import scan_manifests
from signal_engine.scoring import score_account
from signal_engine.seats import compute_seats, rollup_seats

SIZE_IDS = ["small", "medium"]


@pytest.fixture(scope="module")
def manifests_500() -> list[Manifest]:
    """Exactly 500 manifest files across every language and integration state."""
    rng = random.Random(42)
    out: list[Manifest] = []
    repo = 0
    while len(out) < 500:
        repo += 1
        lang = sorted(BUILDERS)[repo % len(BUILDERS)]
        state = ("none", "bench", "codspeed")[repo % 3]
        for path, content in manifest_files(rng, lang, state):
            out.append(Manifest(f"r{repo:04d}", path, content))
    return out[:500]


@pytest.fixture(scope="module")
def small_week():
    """Pre-computed upstream stages for the small dataset."""
    cfg = config()
    wk, _ = prepare_week(dataset("small"), cfg, cfg.now, "2026-10-12")
    return wk


@pytest.mark.benchmark
def test_scanner_500_manifests(manifests_500: list[Manifest]) -> None:
    result = scan_manifests(manifests_500)
    assert result


@pytest.mark.parametrize("size", SIZE_IDS, ids=SIZE_IDS)
def test_identity(benchmark, size: str) -> None:
    cfg, ds = config(), dataset(size)
    events = events_until(ds.events, cfg.now)
    result = benchmark(resolve_identities, ds.users, ds.companies, events, cfg)
    assert result.report.people > 0


@pytest.mark.parametrize("size", SIZE_IDS, ids=SIZE_IDS)
def test_seats(benchmark, size: str) -> None:
    cfg, ds, ident = config(), dataset(size), identity(size)
    events = events_until(ds.events, cfg.now)

    def run():
        per_company = compute_seats(events, ident.resolutions, ds.repos, ds.companies, cfg, cfg.now)
        return rollup_seats(per_company, ident.company_to_account, cfg.pricing.free_cap)

    assert any(s.over_cap for s in benchmark(run).values())


def test_lifecycle(benchmark, small_week) -> None:
    cfg, wk = config(), small_week
    result = benchmark(
        run_lifecycle,
        wk.feats.accounts,
        wk.feats.users,
        wk.seats,
        dataset("small").opportunities,
        cfg,
        cfg.now,
    )
    assert result.account_stage


@pytest.mark.parametrize("size", SIZE_IDS, ids=SIZE_IDS)
def test_features(benchmark, size: str) -> None:
    cfg, ds, ident = config(), dataset(size), identity(size)
    events = events_until(ds.events, cfg.now)
    result = benchmark(compute_features, events, ident.resolutions, cfg, cfg.now)
    assert result.accounts


def test_scoring(benchmark, small_week) -> None:
    cfg, wk = config(), small_week
    inputs = [account_inputs(a, fam, wk) for a, fam in sorted(wk.groups.family.items())]
    scores = benchmark(lambda: [score_account(i, cfg, wk.week, None) for i in inputs])
    assert len(scores) == len(inputs)


def test_plays(benchmark, small_week) -> None:
    cfg, wk = config(), small_week
    contexts = [draft_account(a, fam, wk).context for a, fam in sorted(wk.groups.family.items())]
    plays = benchmark(lambda: [match_plays(c, cfg) for c in contexts])
    assert any(plays)


def test_founder_brief_rendering(benchmark) -> None:
    cfg, ds = config(), dataset("small")
    result = compute_week(ds, cfg, cfg.now, "2026-10-12")
    text = benchmark(render_founder_brief, result, cfg)
    assert text.startswith("# Monday pipeline brief")


@pytest.mark.parametrize("size", SIZE_IDS, ids=SIZE_IDS)
def test_end_to_end(benchmark, size: str) -> None:
    cfg, ds = config(), dataset(size)
    result = benchmark(compute_week, ds, cfg, cfg.now, "2026-10-12")
    assert result.accounts
