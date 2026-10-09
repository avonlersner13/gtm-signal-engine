"""SQLite schema, upserts and loaders.

Tables: companies, users, repos, manifests, events, ground_truth, opportunities,
identity, signals, scores_history, stage_history. Weekly outputs are keyed by week
so history (deltas, biggest movers, stage transitions) survives across runs.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from signal_engine.models import (
    Company,
    Dataset,
    Event,
    GroundTruth,
    Manifest,
    Opportunity,
    Repo,
    Score,
    User,
    WeekResult,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    company_id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL,
    alias_domains TEXT NOT NULL, github_orgs TEXT NOT NULL, industry TEXT NOT NULL,
    region TEXT NOT NULL, employee_count INTEGER NOT NULL, eng_headcount INTEGER NOT NULL,
    parent_id TEXT
);
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY, email TEXT NOT NULL, name TEXT NOT NULL, title TEXT NOT NULL,
    github_login TEXT NOT NULL, github_orgs TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repos (
    repo_id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
    is_private INTEGER NOT NULL, language TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manifests (
    repo_id TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL,
    PRIMARY KEY (repo_id, path)
);
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, ts TEXT NOT NULL,
    event_type TEXT NOT NULL, repo_id TEXT, props TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);
CREATE TABLE IF NOT EXISTS ground_truth (
    company_id TEXT PRIMARY KEY, converted_to_paid INTEGER NOT NULL,
    expanded INTEGER NOT NULL, churned INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunities (
    account_id TEXT NOT NULL, play_id TEXT NOT NULL, accepted_at TEXT NOT NULL,
    PRIMARY KEY (account_id, play_id)
);
CREATE TABLE IF NOT EXISTS identity (
    user_id TEXT NOT NULL, week TEXT NOT NULL, company_id TEXT, account_id TEXT,
    method TEXT NOT NULL, confidence REAL NOT NULL, PRIMARY KEY (user_id, week)
);
CREATE TABLE IF NOT EXISTS signals (
    account_id TEXT NOT NULL, week TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL,
    PRIMARY KEY (account_id, week, kind)
);
CREATE TABLE IF NOT EXISTS scores_history (
    account_id TEXT NOT NULL, week TEXT NOT NULL, fit REAL NOT NULL, intent REAL NOT NULL,
    priority REAL NOT NULL, intent_delta REAL NOT NULL, priority_delta REAL NOT NULL,
    reasons TEXT NOT NULL, PRIMARY KEY (account_id, week)
);
CREATE TABLE IF NOT EXISTS stage_history (
    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, stage TEXT NOT NULL,
    entered_at TEXT NOT NULL, first_seen_week TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id, stage)
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (and create if needed) a database with the full schema."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    return conn


def _upsert(
    conn: sqlite3.Connection,
    table: str,
    keys: tuple[str, ...],
    cols: tuple[str, ...],
    rows: Iterable[tuple[Any, ...]],
    update: bool = True,
) -> None:
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in keys) if update else ""
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT ({', '.join(keys)}) DO " + (f"UPDATE SET {updates}" if updates else "NOTHING")
    )
    conn.executemany(sql, rows)


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def write_companies(conn: sqlite3.Connection, companies: Iterable[Company]) -> None:
    """Upsert companies."""
    cols = (
        "company_id",
        "name",
        "domain",
        "alias_domains",
        "github_orgs",
        "industry",
        "region",
        "employee_count",
        "eng_headcount",
        "parent_id",
    )
    rows = (
        (
            c.company_id,
            c.name,
            c.domain,
            _dumps(list(c.alias_domains)),
            _dumps(list(c.github_orgs)),
            c.industry,
            c.region,
            c.employee_count,
            c.eng_headcount,
            c.parent_id,
        )
        for c in companies
    )
    _upsert(conn, "companies", ("company_id",), cols, rows)


def write_users(conn: sqlite3.Connection, users: Iterable[User]) -> None:
    """Upsert users."""
    cols = ("user_id", "email", "name", "title", "github_login", "github_orgs")
    rows = (
        (u.user_id, u.email, u.name, u.title, u.github_login, _dumps(list(u.github_orgs)))
        for u in users
    )
    _upsert(conn, "users", ("user_id",), cols, rows)


def write_repos(conn: sqlite3.Connection, repos: Iterable[Repo]) -> None:
    """Upsert repos."""
    cols = ("repo_id", "owner", "name", "is_private", "language")
    rows = ((r.repo_id, r.owner, r.name, int(r.is_private), r.language) for r in repos)
    _upsert(conn, "repos", ("repo_id",), cols, rows)


def write_manifests(conn: sqlite3.Connection, manifests: Iterable[Manifest]) -> None:
    """Upsert manifest files."""
    rows = ((m.repo_id, m.path, m.content) for m in manifests)
    _upsert(conn, "manifests", ("repo_id", "path"), ("repo_id", "path", "content"), rows)


def write_events(conn: sqlite3.Connection, events: Iterable[Event]) -> None:
    """Upsert events (event_id is the key)."""
    cols = ("event_id", "user_id", "ts", "event_type", "repo_id", "props")
    rows = (
        (e.event_id, e.user_id, e.ts.isoformat(), e.event_type, e.repo_id, _dumps(dict(e.props)))
        for e in events
    )
    _upsert(conn, "events", ("event_id",), cols, rows)


def write_dataset(conn: sqlite3.Connection, ds: Dataset) -> None:
    """Write a whole dataset in one transaction."""
    with conn:
        write_companies(conn, ds.companies)
        write_users(conn, ds.users)
        write_repos(conn, ds.repos)
        write_manifests(conn, ds.manifests)
        write_events(conn, ds.events)
        truth_rows = (
            (t.company_id, int(t.converted_to_paid), int(t.expanded), int(t.churned))
            for t in ds.truth
        )
        cols = ("company_id", "converted_to_paid", "expanded", "churned")
        _upsert(conn, "ground_truth", ("company_id",), cols, truth_rows)
        opp_rows = ((o.account_id, o.play_id, o.accepted_at.isoformat()) for o in ds.opportunities)
        cols = ("account_id", "play_id", "accepted_at")
        _upsert(conn, "opportunities", ("account_id", "play_id"), cols, opp_rows)


def next_event_id(conn: sqlite3.Connection) -> int:
    """Return the next free event id."""
    (value,) = conn.execute("SELECT COALESCE(MAX(event_id), 0) + 1 FROM events").fetchone()
    return int(value)


def load_dataset(conn: sqlite3.Connection, with_truth: bool = False) -> Dataset:
    """Load everything the pipeline reads. Ground truth only when explicitly asked."""
    companies = tuple(
        Company(
            r[0],
            r[1],
            r[2],
            tuple(json.loads(r[3])),
            tuple(json.loads(r[4])),
            r[5],
            r[6],
            r[7],
            r[8],
            r[9],
        )
        for r in conn.execute("SELECT * FROM companies ORDER BY company_id")
    )
    users = tuple(
        User(r[0], r[1], r[2], r[3], r[4], tuple(json.loads(r[5])))
        for r in conn.execute("SELECT * FROM users ORDER BY user_id")
    )
    repos = tuple(
        Repo(r[0], r[1], r[2], bool(r[3]), r[4])
        for r in conn.execute("SELECT * FROM repos ORDER BY repo_id")
    )
    manifests = tuple(
        Manifest(*r) for r in conn.execute("SELECT * FROM manifests ORDER BY repo_id, path")
    )
    parse = datetime.fromisoformat
    events = tuple(
        Event(r[0], r[1], parse(r[2]), r[3], r[4], json.loads(r[5]))
        for r in conn.execute("SELECT * FROM events ORDER BY ts, event_id")
    )
    opportunities = tuple(
        Opportunity(r[0], r[1], parse(r[2]))
        for r in conn.execute("SELECT * FROM opportunities ORDER BY account_id, play_id")
    )
    truth = load_truth(conn) if with_truth else ()
    return Dataset(companies, users, repos, manifests, events, truth, opportunities)


def load_truth(conn: sqlite3.Connection) -> tuple[GroundTruth, ...]:
    """Load hidden ground-truth labels (evaluation only)."""
    return tuple(
        GroundTruth(r[0], bool(r[1]), bool(r[2]), bool(r[3]))
        for r in conn.execute("SELECT * FROM ground_truth ORDER BY company_id")
    )


def load_scores(conn: sqlite3.Connection, week: str) -> dict[str, Score]:
    """Load the scores saved for one week."""
    out = {}
    for r in conn.execute("SELECT * FROM scores_history WHERE week = ?", (week,)):
        out[r[0]] = Score(r[0], r[1], r[2], r[3], r[4], r[5], r[6], tuple(json.loads(r[7])), ())
    return out


def previous_week(conn: sqlite3.Connection, week: str) -> str | None:
    """Return the latest scored week strictly before ``week``."""
    row = conn.execute("SELECT MAX(week) FROM scores_history WHERE week < ?", (week,)).fetchone()
    return row[0] if row and row[0] else None


def load_stages(conn: sqlite3.Connection, week: str) -> dict[str, str]:
    """Account stages stored for one week (from the signals table)."""
    rows = conn.execute(
        "SELECT account_id, value FROM signals WHERE week = ? AND kind = 'stage'", (week,)
    )
    return {acct: json.loads(value) for acct, value in rows}


def latest_week(conn: sqlite3.Connection) -> str | None:
    """Return the most recent scored week."""
    row = conn.execute("SELECT MAX(week) FROM scores_history").fetchone()
    return row[0] if row and row[0] else None


def save_week(conn: sqlite3.Connection, result: WeekResult) -> None:
    """Persist identity, signals, scores and stage transitions for one week."""
    week = result.week
    with conn:
        _upsert(
            conn,
            "identity",
            ("user_id", "week"),
            ("user_id", "week", "company_id", "account_id", "method", "confidence"),
            (
                (r.user_id, week, r.company_id, r.account_id, r.method, r.confidence)
                for r in result.resolutions.values()
            ),
        )
        _upsert(
            conn,
            "scores_history",
            ("account_id", "week"),
            (
                "account_id",
                "week",
                "fit",
                "intent",
                "priority",
                "intent_delta",
                "priority_delta",
                "reasons",
            ),
            (
                (
                    a.account_id,
                    week,
                    a.score.fit,
                    a.score.intent,
                    a.score.priority,
                    a.score.intent_delta,
                    a.score.priority_delta,
                    _dumps(list(a.score.reasons)),
                )
                for a in result.accounts
            ),
        )
        _upsert(
            conn,
            "signals",
            ("account_id", "week", "kind"),
            ("account_id", "week", "kind", "value"),
            (row for a in result.accounts for row in _signal_rows(a, week)),
        )
        _upsert(
            conn,
            "stage_history",
            ("entity_type", "entity_id", "stage"),
            ("entity_type", "entity_id", "stage", "entered_at", "first_seen_week"),
            (
                (e.entity_type, e.entity_id, e.stage, e.entered_at.isoformat(), week)
                for e in result.lifecycle.entries
            ),
            update=False,  # keep the first time an entity entered a stage
        )


def _signal_rows(account: Any, week: str) -> list[tuple[str, str, str, str]]:
    seats = account.seats
    values: Mapping[str, Any] = {
        "stage": account.stage,
        "seats": {
            "used": seats.seats_used,
            "over_cap": seats.over_cap,
            "trial_days_left": seats.trial_days_left,
            "blocked": len(seats.blocked_users),
        },
        "scan": {
            "codspeed": account.scan.repos_codspeed,
            "without": account.scan.repos_benchmarks_without_codspeed,
            "frameworks": list(account.scan.frameworks),
        },
        "plays": [p.play_id for p in account.plays],
        "revenue_arr": account.revenue.total_arr,
    }
    return [(account.account_id, week, k, _dumps(v)) for k, v in values.items()]


def load_stage_history(conn: sqlite3.Connection) -> list[tuple[str, str, str, str, str]]:
    """Return every stored stage transition."""
    return list(conn.execute("SELECT * FROM stage_history ORDER BY entity_type, entity_id, stage"))
