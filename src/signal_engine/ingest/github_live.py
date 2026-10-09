"""OPTIONAL live GitHub adapter. Off by default; never imported by tests or benchmarks.

Runs only through ``signal-engine github-scan``. It shells out to the authenticated
``gh`` CLI, reads public manifest/CI files for each org in a target list, runs the same
scanner as the pipeline, and writes results to the gitignored ``local/`` folder only.
"""

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from signal_engine.scanner import classify, scan_repo, summarize

MAX_REPOS_PER_ORG = 30
MAX_FILES_PER_REPO = 40


def _gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _repos(org: str) -> list[dict[str, Any]]:
    jq = ".[] | select(.archived | not) | {name, private, default_branch}"
    out = _gh("api", f"orgs/{org}/repos?per_page=100&type=public", "--paginate", "--jq", jq)
    repos = [json.loads(line) for line in out.splitlines() if line.strip()]
    return sorted(repos, key=lambda r: r["name"])[:MAX_REPOS_PER_ORG]


def _files(org: str, repo: str, branch: str) -> list[tuple[str, str]]:
    tree = _gh("api", f"repos/{org}/{repo}/git/trees/{branch}?recursive=1", "--jq", ".tree[].path")
    paths = [p for p in tree.splitlines() if classify(p)][:MAX_FILES_PER_REPO]
    files = []
    for path in paths:
        raw = _gh("api", f"repos/{org}/{repo}/contents/{path}", "--jq", ".content")
        files.append((path, base64.b64decode(raw).decode("utf-8", errors="replace")))
    return files


def scan_org(org: str) -> dict[str, Any]:
    """Scan one org's public repos; return a JSON-serializable summary."""
    scans = []
    for repo in _repos(org):
        try:
            files = _files(org, repo["name"], repo["default_branch"])
        except RuntimeError as exc:
            scans.append({"repo": repo["name"], "error": str(exc)})
            continue
        scans.append({"repo": repo["name"], "scan": scan_repo(f"{org}/{repo['name']}", files)})
    summary = summarize(s["scan"] for s in scans if "scan" in s)
    return {
        "org": org,
        "summary": asdict(summary),
        "repos": [
            {"repo": s["repo"], "error": s["error"]}
            if "error" in s
            else {
                "repo": s["repo"],
                "status": s["scan"].status,
                "frameworks": s["scan"].frameworks,
                "codspeed": s["scan"].codspeed,
                "walltime_only": s["scan"].walltime_only,
            }
            for s in scans
        ],
    }


LOCAL_DIR = Path("local")


def run(org_list: Path) -> Path:
    """Scan every org in ``org_list`` (one per line); write local/github_scan.json only."""
    lines = org_list.read_text().splitlines()
    orgs = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    results = [scan_org(org) for org in orgs]
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCAL_DIR / "github_scan.json"
    path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    return path
