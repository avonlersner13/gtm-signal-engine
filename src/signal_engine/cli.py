"""Command-line interface: ``signal-engine <command>``."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from signal_engine.config import Config, load_config
from signal_engine.evaluate import evaluate, render_evaluation
from signal_engine.generate import generate_dataset
from signal_engine.ingest import store
from signal_engine.ingest.importers import import_file
from signal_engine.models import Dataset, WeekResult
from signal_engine.outputs import write_outputs, write_text
from signal_engine.outputs.fmt import stage_label
from signal_engine.outputs.founder_brief import render_founder_brief
from signal_engine.pipeline import compute_week, run_week

DEFAULT_DB = "out/demo.db"
DEFAULT_OUT = "out"
IMPORT_KINDS = ("companies", "users", "repos", "manifests", "events")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="signal-engine", description=__doc__)
    p.add_argument("--config", default=None, help="config directory (default: ./config)")
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="generate a synthetic dataset into SQLite")
    _sizes(g)
    g.add_argument("--db", default=DEFAULT_DB)

    i = sub.add_parser("ingest", help="import JSONL/CSV files (see docs/EVENT_SCHEMA.md)")
    i.add_argument("--db", default=DEFAULT_DB)
    for kind in IMPORT_KINDS:
        i.add_argument(f"--{kind}", type=Path, action="append", default=[], metavar="PATH")

    r = sub.add_parser("run", help="run the weekly pipeline and write outputs")
    r.add_argument("--db", default=DEFAULT_DB)
    r.add_argument("--week", type=date.fromisoformat, default=None)
    r.add_argument("--out", default=DEFAULT_OUT)

    b = sub.add_parser("brief", help="print the founder brief to stdout")
    b.add_argument("--db", default=DEFAULT_DB)
    b.add_argument("--week", type=date.fromisoformat, default=None)

    e = sub.add_parser("evaluate", help="evaluate the ranking against ground truth")
    e.add_argument("--db", default=DEFAULT_DB)
    e.add_argument("--week", type=date.fromisoformat, default=None)
    e.add_argument("--out", default=DEFAULT_OUT)

    d = sub.add_parser("demo", help="generate + run two weeks + evaluate")
    _sizes(d)
    d.add_argument("--db", default=DEFAULT_DB)
    d.add_argument("--out", default=DEFAULT_OUT)

    gh = sub.add_parser("github-scan", help="OPTIONAL live scan via the gh CLI (writes to local/)")
    gh.add_argument("--org-list", type=Path, required=True)
    return p


def _sizes(p: argparse.ArgumentParser) -> None:
    p.add_argument("--companies", type=int, default=400)
    p.add_argument("--users", type=int, default=5000)
    p.add_argument("--events", type=int, default=150_000)
    p.add_argument("--seed", type=int, default=None)


def _fresh_db(path: str) -> None:
    db = Path(path)
    if db.exists():
        db.unlink()


def cmd_generate(args: argparse.Namespace, cfg: Config) -> Dataset:
    """Generate a dataset and write it to a fresh database."""
    ds = generate_dataset(cfg, args.companies, args.users, args.events, args.seed)
    _fresh_db(args.db)
    conn = store.connect(args.db)
    try:
        store.write_dataset(conn, ds)
    finally:
        conn.close()
    print(
        f"generated {len(ds.companies)} companies, {len(ds.users)} users, {len(ds.repos)} repos, "
        f"{len(ds.manifests)} manifests, {len(ds.events)} events -> {args.db}"
    )
    return ds


def cmd_ingest(args: argparse.Namespace) -> int:
    """Import files in dependency order."""
    conn = store.connect(args.db)
    failed = 0
    try:
        for kind in IMPORT_KINDS:
            for path in getattr(args, kind):
                report = import_file(conn, kind, path)
                print(f"{kind}: {path}: {report.accepted} accepted, {report.rejected} rejected")
                for err in report.errors:
                    print(f"  {err}")
                failed += report.rejected
    finally:
        conn.close()
    return 1 if failed else 0


def _week(args: argparse.Namespace, cfg: Config) -> date:
    return args.week or cfg.now.date()


def _compute(db: str, cfg: Config, week: date) -> tuple[Dataset, WeekResult]:
    conn = store.connect(db)
    try:
        ds = store.load_dataset(conn, with_truth=True)
        prev = store.previous_week(conn, week.isoformat())
        previous = store.load_scores(conn, prev) if prev else {}
        stages = store.load_stages(conn, prev) if prev else {}
    finally:
        conn.close()
    return ds, compute_week(ds, cfg, cfg.as_of_for_week(week), week.isoformat(), previous, stages)


def write_evaluation(ds: Dataset, result: WeekResult, cfg: Config, out: Path) -> str:
    """Evaluate, write out/evaluation.md and return the markdown."""
    text = render_evaluation(evaluate(result, ds.companies, ds.truth, cfg.seed))
    write_text(out / "evaluation.md", text)
    return text


def console_table(result: WeekResult, n: int = 15) -> str:
    """Compact ranking table for the terminal."""
    header = (
        f"{'#':>2}  {'account':<26} {'stage':<12} {'fit':>5} {'intent':>6} {'prio':>5}  "
        f"{'primary play':<21} top reason"
    )
    rows = [header, "-" * len(header)]
    for i, a in enumerate(result.accounts[:n], start=1):
        play = a.plays[0].play_id if a.plays else "-"
        reason = a.score.reasons[0] if a.score.reasons else "-"
        reason = reason if len(reason) <= 62 else reason[:59] + "..."
        rows.append(
            f"{i:>2}  {a.name[:26]:<26} {stage_label(a.stage):<12} {a.score.fit:>5.1f} "
            f"{a.score.intent:>6.1f} {a.score.priority:>5.1f}  {play:<21} {reason}"
        )
    return "\n".join(rows)


def cmd_demo(args: argparse.Namespace, cfg: Config) -> None:
    """Generate, run two consecutive weeks (so deltas exist), evaluate, print a table."""
    started = time.perf_counter()
    ds = cmd_generate(args, cfg)
    out = Path(args.out)
    this_week = cfg.now.date()
    conn = store.connect(args.db)
    try:
        result = None
        for week in (this_week - timedelta(days=7), this_week):
            prev = store.previous_week(conn, week.isoformat())
            previous = store.load_scores(conn, prev) if prev else {}
            stages = store.load_stages(conn, prev) if prev else {}
            result = compute_week(
                ds, cfg, cfg.as_of_for_week(week), week.isoformat(), previous, stages
            )
            store.save_week(conn, result)
            write_outputs(result, cfg, out)
    finally:
        conn.close()
    assert result is not None
    ev_text = write_evaluation(ds, result, cfg, out)
    print()
    print(console_table(result))
    print()
    print(next(line for line in ev_text.splitlines() if line.startswith("| precision@10")))
    print(
        f"\noutputs in {out}/ (founder_brief_{result.week}.md, accounts/, crm_*.csv, alerts.jsonl,"
        f" funnel.md, evaluation.md)"
    )
    print(f"demo finished in {time.perf_counter() - started:.1f}s")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``signal-engine`` console script."""
    args = _parser().parse_args(argv)
    cfg = load_config(args.config)
    if args.command == "generate":
        cmd_generate(args, cfg)
    elif args.command == "ingest":
        return cmd_ingest(args)
    elif args.command == "run":
        result = run_week(args.db, cfg, _week(args, cfg), Path(args.out))
        print(console_table(result))
        print(f"\nwrote outputs for week {result.week} to {args.out}/")
    elif args.command == "brief":
        _, result = _compute(args.db, cfg, _week(args, cfg))
        sys.stdout.write(render_founder_brief(result, cfg))
    elif args.command == "evaluate":
        ds, result = _compute(args.db, cfg, _week(args, cfg))
        sys.stdout.write(write_evaluation(ds, result, cfg, Path(args.out)))
    elif args.command == "demo":
        cmd_demo(args, cfg)
    elif args.command == "github-scan":  # pragma: no cover - live network, never in tests
        from signal_engine.ingest import github_live

        print(f"wrote {github_live.run(args.org_list)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
