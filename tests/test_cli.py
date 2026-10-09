"""CLI commands and end-to-end determinism (same seed -> byte-identical outputs)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from signal_engine.cli import main

SMALL = ["--companies", "60", "--users", "700", "--events", "9000"]
EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"


def digest(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.suffix != ".db"
    }


def test_demo_is_deterministic(tmp_path, capsys) -> None:
    for run in ("a", "b"):
        assert (
            main(
                ["demo", *SMALL, "--db", str(tmp_path / run / "d.db"), "--out", str(tmp_path / run)]
            )
            == 0
        )
    out = capsys.readouterr().out
    assert "primary play" in out and "precision@10" in out
    a, b = digest(tmp_path / "a"), digest(tmp_path / "b")
    assert a == b
    assert {
        "founder_brief_2026-10-05.md",
        "founder_brief_2026-10-12.md",
        "evaluation.md",
        "alerts.jsonl",
    } <= set(a)


def test_generate_run_brief_evaluate(tmp_path, capsys) -> None:
    db, out = str(tmp_path / "x.db"), str(tmp_path / "out")
    assert main(["generate", *SMALL, "--seed", "42", "--db", db]) == 0
    assert main(["run", "--db", db, "--week", "2026-10-05", "--out", out]) == 0
    assert main(["run", "--db", db, "--out", out]) == 0
    capsys.readouterr()
    assert main(["brief", "--db", db]) == 0
    brief = capsys.readouterr().out
    assert brief.startswith("# Monday pipeline brief: week of 2026-10-12")
    assert (tmp_path / "out" / "founder_brief_2026-10-12.md").read_text() == brief
    assert main(["evaluate", "--db", db, "--out", out]) == 0
    assert "precision@25" in capsys.readouterr().out
    assert (tmp_path / "out" / "evaluation.md").exists()


def test_generate_overwrites_existing_db(tmp_path, capsys) -> None:
    db = str(tmp_path / "g.db")
    main(["generate", "--companies", "20", "--users", "100", "--events", "500", "--db", db])
    main(["generate", "--companies", "10", "--users", "60", "--events", "300", "--db", db])
    assert "10 companies" in capsys.readouterr().out.splitlines()[-1]


def test_ingest_command(tmp_path, capsys) -> None:
    db = str(tmp_path / "i.db")
    args = [
        "ingest",
        "--db",
        db,
        "--companies",
        str(EXAMPLES / "companies.csv"),
        "--users",
        str(EXAMPLES / "users.jsonl"),
        "--repos",
        str(EXAMPLES / "repos.csv"),
        "--manifests",
        str(EXAMPLES / "manifests.jsonl"),
        "--events",
        str(EXAMPLES / "events.jsonl"),
        "--events",
        str(EXAMPLES / "events.csv"),
    ]
    assert main(args) == 0
    assert "events: " in capsys.readouterr().out
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"user_id": "u"}\n')
    assert main(["ingest", "--db", db, "--events", str(bad)]) == 1
