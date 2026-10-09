"""SQLite round-trip, weekly history, and importers against the documented examples."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import NOW
from signal_engine.ingest import store
from signal_engine.ingest.importers import RowError, import_file, parse_event, parse_ts, read_rows
from signal_engine.pipeline import compute_week

EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"


def test_dataset_round_trip(tmp_path, small_ds) -> None:
    conn = store.connect(tmp_path / "rt.db")
    store.write_dataset(conn, small_ds)
    store.write_dataset(conn, small_ds)  # upsert is idempotent
    loaded = store.load_dataset(conn, with_truth=True)
    assert loaded.companies == small_ds.companies
    assert loaded.users == small_ds.users
    assert loaded.repos == small_ds.repos
    assert loaded.manifests == tuple(sorted(small_ds.manifests, key=lambda m: (m.repo_id, m.path)))
    assert [(e.event_id, e.ts, e.event_type, dict(e.props)) for e in loaded.events] == [
        (e.event_id, e.ts, e.event_type, dict(e.props)) for e in small_ds.events
    ]
    assert loaded.truth == small_ds.truth
    assert loaded.opportunities == small_ds.opportunities
    assert store.load_dataset(conn).truth == ()
    assert store.next_event_id(conn) == len(small_ds.events) + 1


def test_week_history_scores_stages(tmp_path, small_ds, cfg) -> None:
    conn = store.connect(tmp_path / "h.db")
    store.write_dataset(conn, small_ds)
    assert store.latest_week(conn) is None
    w1 = compute_week(small_ds, cfg, cfg.as_of_for_week(NOW.date().replace(day=5)), "2026-10-05")
    store.save_week(conn, w1)
    assert store.previous_week(conn, "2026-10-12") == "2026-10-05"
    assert store.previous_week(conn, "2026-10-05") is None
    prev = store.load_scores(conn, "2026-10-05")
    stages = store.load_stages(conn, "2026-10-05")
    assert set(prev) == {a.account_id for a in w1.accounts}
    assert set(stages.values()) <= {
        "none",
        "signed_up",
        "installed",
        "activated",
        "engaged",
        "pqa",
        "opportunity",
        "pql",
    }
    w2 = compute_week(small_ds, cfg, cfg.now, "2026-10-12", prev, stages)
    store.save_week(conn, w2)
    assert store.latest_week(conn) == "2026-10-12"
    history = store.load_stage_history(conn)
    first_seen = {(r[0], r[1], r[2]): r[4] for r in history}
    assert all(v in ("2026-10-05", "2026-10-12") for v in first_seen.values())
    assert any(v == "2026-10-05" for v in first_seen.values())
    churn = [r for r in history if r[2] == "churn_risk"]
    assert all(r[0] == "account" for r in churn)


def test_import_documented_examples(tmp_path, cfg) -> None:
    conn = store.connect(tmp_path / "imp.db")
    reports = [
        import_file(conn, "companies", EXAMPLES / "companies.csv"),
        import_file(conn, "users", EXAMPLES / "users.jsonl"),
        import_file(conn, "repos", EXAMPLES / "repos.csv"),
        import_file(conn, "manifests", EXAMPLES / "manifests.jsonl"),
        import_file(conn, "events", EXAMPLES / "events.jsonl"),
        import_file(conn, "events", EXAMPLES / "events.csv"),
    ]
    assert [r.rejected for r in reports] == [0] * 6
    assert [r.accepted for r in reports] == [2, 3, 2, 2, 7, 3]
    ds = store.load_dataset(conn)
    assert ds.companies[1].parent_id == "x001"
    assert ds.companies[0].github_orgs == ("quartzlabs", "quartzlabs-oss")
    assert ds.repos[0].is_private and not ds.repos[1].is_private
    assert [e.event_id for e in ds.events] == sorted(e.event_id for e in ds.events)
    assert ds.events[-1].props == {"runner": "graviton", "minutes": 35.5}
    assert all(e.ts.utcoffset().total_seconds() == 0 for e in ds.events)
    result = compute_week(ds, cfg, cfg.now, "2026-10-12")
    [acct] = [a for a in result.accounts if a.account_id == "x001"]
    assert acct.stage == "activated"
    assert result.resolutions["ux2"].account_id == "x001"
    assert result.identity.bots_excluded == 1


def test_importer_rejects_bad_rows(tmp_path) -> None:
    conn = store.connect(tmp_path / "bad.db")
    bad = tmp_path / "events.jsonl"
    rows = [
        {"user_id": "u1", "ts": "2026-10-01T00:00:00Z", "event_type": "signed_up"},
        {"user_id": "u1", "ts": "2026-10-01T00:00:00", "event_type": "signed_up"},
        {"user_id": "u1", "ts": "yesterday", "event_type": "signed_up"},
        {"user_id": "u1", "ts": "2026-10-01T00:00:00Z", "event_type": "teleported"},
        {"ts": "2026-10-01T00:00:00Z", "event_type": "signed_up"},
        {
            "user_id": "u1",
            "ts": "2026-10-01T00:00:00Z",
            "event_type": "signed_up",
            "props": "{oops",
        },
        {"user_id": "u1", "ts": "2026-10-01T00:00:00Z", "event_type": "signed_up", "props": [1]},
    ]
    bad.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n[1, 2]\n\n")
    report = import_file(conn, "events", bad)
    assert report.accepted == 1
    assert report.rejected == 8
    assert report.errors[0].startswith("line 2: timestamp")
    repos = tmp_path / "repos.csv"
    repos.write_text("repo_id,owner,name,is_private,language\nr1,o,n,maybe,go\nr2,o,n,no,\n")
    rep = import_file(conn, "repos", repos)
    assert (rep.accepted, rep.rejected) == (1, 1)
    companies = tmp_path / "companies.jsonl"
    companies.write_text(
        '{"company_id": "c", "name": "n", "domain": "d.example", '
        '"employee_count": "many", "eng_headcount": 1}\n'
    )
    assert import_file(conn, "companies", companies).rejected == 1
    manifests = tmp_path / "manifests.jsonl"
    manifests.write_text('{"repo_id": "r", "path": "p"}\n')
    assert import_file(conn, "manifests", manifests).rejected == 1


def test_importer_error_list_is_capped(tmp_path) -> None:
    conn = store.connect(tmp_path / "cap.db")
    path = tmp_path / "e.jsonl"
    path.write_text("x\n" * 30)
    report = import_file(conn, "events", path)
    assert report.rejected == 30
    assert len(report.errors) == 20


def test_unsupported_extension(tmp_path) -> None:
    path = tmp_path / "events.xlsx"
    path.write_text("")
    with pytest.raises(ValueError, match="unsupported file type"):
        list(read_rows(path))


def test_parse_helpers() -> None:
    assert parse_ts("2026-10-12T09:00:00-07:00").isoformat() == "2026-10-12T16:00:00+00:00"
    event = parse_event(
        {
            "user_id": "u",
            "ts": "2026-10-12T00:00:00Z",
            "event_type": "docs_visit",
            "props": '{"a": 1}',
        },
        7,
    )
    assert event.event_id == 7 and event.props == {"a": 1} and event.repo_id is None
    with pytest.raises(RowError):
        parse_event({"user_id": " ", "ts": "2026-10-12T00:00:00Z", "event_type": "docs_visit"}, 1)
