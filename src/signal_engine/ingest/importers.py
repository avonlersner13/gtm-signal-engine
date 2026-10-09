"""JSONL / CSV importers following docs/EVENT_SCHEMA.md.

Every importer validates rows, rejects bad ones with a line-numbered reason, and
upserts the good ones. Timestamps must carry a UTC offset and are stored in UTC.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from signal_engine.ingest import store
from signal_engine.models import EVENT_TYPES, Company, Event, Manifest, Repo, User

MAX_ERRORS = 20


class RowError(ValueError):
    """A row failed validation."""


@dataclass(slots=True)
class ImportReport:
    """Outcome of one import."""

    kind: str
    accepted: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)

    def reject(self, line: int, reason: str) -> None:
        """Record a rejected row."""
        self.rejected += 1
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(f"line {line}: {reason}")


def read_rows(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield (line number, row dict) from a .jsonl/.ndjson or .csv file."""
    suffix = path.suffix.lower()
    with path.open(encoding="utf-8", newline="") as fh:
        if suffix in (".jsonl", ".ndjson"):
            for n, line in enumerate(fh, start=1):
                if line.strip():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        row = {"__error__": f"invalid JSON ({exc.msg})"}
                    yield n, row if isinstance(row, dict) else {"__error__": "not an object"}
        elif suffix == ".csv":
            for n, row in enumerate(csv.DictReader(fh), start=2):
                yield n, dict(row)
        else:
            raise ValueError(f"unsupported file type '{suffix}' (use .jsonl, .ndjson or .csv)")


def _text(row: dict[str, Any], key: str, required: bool = True) -> str | None:
    value = row.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise RowError(f"missing '{key}'")
        return None
    return str(value).strip()


def _list(row: dict[str, Any], key: str) -> tuple[str, ...]:
    value = row.get(key)
    if value is None or value == "":
        return ()
    if isinstance(value, list):
        return tuple(str(v) for v in value)
    return tuple(v.strip() for v in str(value).split(";") if v.strip())


def _int(row: dict[str, Any], key: str) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise RowError(f"'{key}' must be an integer") from exc


def _bool(row: dict[str, Any], key: str) -> bool:
    value = row.get(key)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    raise RowError(f"'{key}' must be a boolean")


def parse_ts(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp with offset; return it in UTC."""
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RowError(f"bad timestamp '{raw}'") from exc
    if ts.tzinfo is None:
        raise RowError(f"timestamp '{raw}' has no UTC offset")
    return ts.astimezone(UTC)


def parse_event(row: dict[str, Any], event_id: int) -> Event:
    """Validate one event row."""
    etype = _text(row, "event_type")
    if etype not in EVENT_TYPES:
        raise RowError(f"unknown event_type '{etype}'")
    props = row.get("props") or {}
    if isinstance(props, str):
        try:
            props = json.loads(props)
        except json.JSONDecodeError as exc:
            raise RowError("props is not valid JSON") from exc
    if not isinstance(props, dict):
        raise RowError("props must be an object")
    return Event(
        event_id=event_id,
        user_id=_text(row, "user_id") or "",
        ts=parse_ts(_text(row, "ts") or ""),
        event_type=etype,
        repo_id=_text(row, "repo_id", required=False),
        props=props,
    )


def parse_user(row: dict[str, Any]) -> User:
    """Validate one user row."""
    return User(
        user_id=_text(row, "user_id") or "",
        email=_text(row, "email") or "",
        name=_text(row, "name") or "",
        title=_text(row, "title", required=False) or "",
        github_login=_text(row, "github_login") or "",
        github_orgs=_list(row, "github_orgs"),
    )


def parse_company(row: dict[str, Any]) -> Company:
    """Validate one company (account) row."""
    return Company(
        company_id=_text(row, "company_id") or "",
        name=_text(row, "name") or "",
        domain=_text(row, "domain") or "",
        alias_domains=_list(row, "alias_domains"),
        github_orgs=_list(row, "github_orgs"),
        industry=_text(row, "industry", required=False) or "unknown",
        region=_text(row, "region", required=False) or "unknown",
        employee_count=_int(row, "employee_count"),
        eng_headcount=_int(row, "eng_headcount"),
        parent_id=_text(row, "parent_id", required=False),
    )


def parse_repo(row: dict[str, Any]) -> Repo:
    """Validate one repo row."""
    return Repo(
        repo_id=_text(row, "repo_id") or "",
        owner=_text(row, "owner") or "",
        name=_text(row, "name") or "",
        is_private=_bool(row, "is_private"),
        language=_text(row, "language", required=False) or "unknown",
    )


def parse_manifest(row: dict[str, Any]) -> Manifest:
    """Validate one manifest row (content may be empty but must be present)."""
    if "content" not in row:
        raise RowError("missing 'content'")
    return Manifest(_text(row, "repo_id") or "", _text(row, "path") or "", str(row["content"]))


WRITERS: dict[str, Callable[[sqlite3.Connection, list[Any]], None]] = {
    "users": store.write_users,
    "companies": store.write_companies,
    "repos": store.write_repos,
    "manifests": store.write_manifests,
}
PARSERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "users": parse_user,
    "companies": parse_company,
    "repos": parse_repo,
    "manifests": parse_manifest,
}


def import_file(conn: sqlite3.Connection, kind: str, path: Path) -> ImportReport:
    """Import one file of ``kind`` (events, users, companies, repos, manifests)."""
    report = ImportReport(kind)
    good: list[Any] = []
    next_id = store.next_event_id(conn)
    for line, row in read_rows(path):
        try:
            if "__error__" in row:
                raise RowError(row["__error__"])
            if kind == "events":
                good.append(parse_event(row, next_id + len(good)))
            else:
                good.append(PARSERS[kind](row))
        except RowError as exc:
            report.reject(line, str(exc))
    with conn:
        if kind == "events":
            store.write_events(conn, sorted(good, key=lambda e: (e.ts, e.event_id)))
        else:
            WRITERS[kind](conn, good)
    report.accepted = len(good)
    return report
