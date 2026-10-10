"""Manifest and CI scanner: which benchmarking framework, and is CodSpeed integrated?

Detection rules follow CodSpeed's documented integrations (Oct 2026):

* Python: pytest-benchmark / asv vs pytest-codspeed or ``--codspeed`` in CI.
* Rust: divan / criterion / bencher in [dev-dependencies]; CodSpeed's compat crates are
  usually *renamed* (``criterion = { package = "codspeed-criterion-compat" }``), so the
  ``package`` field is checked, not the key. ``cargo-codspeed`` in CI.
* Node/TS: vitest (only with *.bench.* files), tinybench, benchmark.js, playwright vs
  the @codspeed/*-plugin packages.
* Go: ``func Benchmark`` in *_test.go; CodSpeed is only visible in CI (CodSpeedHQ/action
  running ``go test -bench``).
* JVM: jmh-core / me.champeau.jmh vs io.codspeed.jmh, codspeed-jvm submodule, or the
  jmh-fork includeBuild.
* Scala sbt-jmh: not supported by CodSpeed -> "not addressable yet".
* C++: Google Benchmark vs codspeed-cpp FetchContent, -DCODSPEED_MODE=, Bazel compat.
* Any: CodSpeedHQ/action@v* in workflows, codspeed.yml at the repo root.

Go and JVM can only run in walltime mode (macro runners, paid minutes): tagged
``walltime_only`` as a revenue signal.
"""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from signal_engine.models import Manifest, RepoScan, ScanSummary

WALLTIME_ONLY_LANGUAGES = frozenset({"go", "jvm"})

PY_DEP_RE = r"""(?im)(?:^|["'\s\[,])(pytest-codspeed|pytest-benchmark|asv)(?=$|[\s"'<>=~!\[;,@])"""
PY_CODSPEED_CI_RE = r"--codspeed\b"
CODSPEED_ACTION_RE = r"CodSpeedHQ/action@v\d+"
CARGO_CODSPEED_RE = r"\bcargo[- ]codspeed\b"
GO_BENCH_CI_RE = r"\bgo\s+test\b[^\n]*-bench"
GO_BENCH_FUNC_RE = r"(?m)^func\s+Benchmark\w*\s*\(\s*\w+\s+\*testing\.B\s*\)"
NODE_BENCH_FILE_RE = r"\.bench\.(?:ts|js|mts|mjs|cts|cjs|tsx|jsx)$"
GRADLE_JMH_RE = r"org\.openjdk\.jmh:jmh-core|me\.champeau\.jmh"
GRADLE_CODSPEED_RE = r"io\.codspeed\.jmh"
JMH_FORK_RE = r"includeBuild\([^)]*codspeed-jvm/jmh-fork"
SBT_JMH_RE = r"sbt-jmh"
CPP_GBENCH_RE = r"benchmark::benchmark|BENCHMARK_MAIN"
CPP_CODSPEED_RE = r"CodSpeedHQ/codspeed-cpp|-DCODSPEED_MODE="
BAZEL_CODSPEED_RE = r"codspeed_google_benchmark_compat"
REQUIREMENTS_RE = r"(?:^|/)requirements[^/]*\.txt$"

RUST_BENCH_CRATES = ("divan", "criterion", "bencher")
NODE_BENCH_PACKAGES = {
    "vitest": "vitest",
    "tinybench": "tinybench",
    "benchmark": "benchmark.js",
    "playwright": "playwright",
    "@playwright/test": "playwright",
}
NODE_CODSPEED_PACKAGES = (
    "@codspeed/vitest-plugin",
    "@codspeed/tinybench-plugin",
    "@codspeed/benchmark.js-plugin",
    "@codspeed/playwright-plugin",
)
PYTHON_FILES = ("pyproject.toml", "uv.lock", "poetry.lock", "Pipfile")


class _Findings:
    """Mutable accumulator for one repo while its files are scanned."""

    def __init__(self) -> None:
        self.languages: set[str] = set()
        self.frameworks: set[str] = set()
        self.codspeed: set[str] = set()
        self.evidence: list[str] = []
        self.has_node_bench_files = False
        self.vitest_pending = False

    def framework(self, lang: str, name: str, path: str) -> None:
        self.languages.add(lang)
        self.frameworks.add(name)
        self.evidence.append(f"{path}: {name}")

    def integrated(self, lang: str | None, marker: str, path: str) -> None:
        if lang:
            self.languages.add(lang)
        self.codspeed.add(marker)
        self.evidence.append(f"{path}: CodSpeed ({marker})")


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _scan_python(path: str, content: str, f: _Findings) -> None:
    f.languages.add("python")
    for match in sorted({m.lower() for m in re.compile(PY_DEP_RE).findall(content)}):
        if match == "pytest-codspeed":
            f.integrated("python", "pytest-codspeed", path)
        else:
            f.framework("python", match, path)


def _cargo_dev_deps(data: Mapping[str, Any]) -> dict[str, Any]:
    deps: dict[str, Any] = {}
    deps.update(data.get("dev-dependencies", {}))
    deps.update(data.get("workspace", {}).get("dependencies", {}))
    for target in data.get("target", {}).values():
        deps.update(target.get("dev-dependencies", {}))
    return deps


def _scan_cargo(path: str, content: str, f: _Findings) -> None:
    f.languages.add("rust")
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        f.evidence.append(f"{path}: unparseable TOML, skipped")
        return
    for key, spec in sorted(_cargo_dev_deps(data).items()):
        package = spec.get("package", key) if isinstance(spec, dict) else key
        if package.startswith("codspeed-") and package.endswith("-compat"):
            f.integrated("rust", package, path)
        elif package in RUST_BENCH_CRATES:
            f.framework("rust", package, path)


def _scan_package_json(path: str, content: str, f: _Findings) -> None:
    f.languages.add("node")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        f.evidence.append(f"{path}: unparseable JSON, skipped")
        return
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    for name in sorted(deps):
        if name in NODE_CODSPEED_PACKAGES:
            f.integrated("node", name, path)
        elif name == "vitest":
            f.vitest_pending = True
        elif name in NODE_BENCH_PACKAGES:
            f.framework("node", NODE_BENCH_PACKAGES[name], path)


def _scan_go_test(path: str, content: str, f: _Findings) -> None:
    f.languages.add("go")
    if re.compile(GO_BENCH_FUNC_RE).search(content):
        f.framework("go", "go-testing-bench", path)


def _scan_pom(path: str, content: str, f: _Findings) -> None:
    f.languages.add("jvm")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        f.evidence.append(f"{path}: unparseable XML, skipped")
        return
    for dep in root.iter():
        if not dep.tag.endswith("dependency"):
            continue
        fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in dep}
        group, artifact = fields.get("groupId", ""), fields.get("artifactId", "")
        if group == "io.codspeed.jmh":
            f.integrated("jvm", "io.codspeed.jmh", path)
        elif group == "org.openjdk.jmh" and artifact == "jmh-core":
            f.framework("jvm", "jmh", path)


def _scan_gradle(path: str, content: str, f: _Findings) -> None:
    f.languages.add("jvm")
    if re.compile(GRADLE_CODSPEED_RE).search(content):
        f.integrated("jvm", "io.codspeed.jmh", path)
    elif re.compile(GRADLE_JMH_RE).search(content):
        f.framework("jvm", "jmh", path)
    if re.compile(JMH_FORK_RE).search(content):
        f.integrated("jvm", "codspeed-jvm jmh-fork", path)


def _scan_gitmodules(path: str, content: str, f: _Findings) -> None:
    if "codspeed-jvm" in content:
        f.integrated("jvm", "codspeed-jvm submodule", path)


def _scan_sbt(path: str, content: str, f: _Findings) -> None:
    f.languages.add("scala")
    if re.compile(SBT_JMH_RE).search(content):
        f.framework("scala", "sbt-jmh", path)


def _scan_cmake(path: str, content: str, f: _Findings) -> None:
    f.languages.add("cpp")
    if re.compile(CPP_CODSPEED_RE).search(content):
        f.integrated("cpp", "codspeed-cpp", path)
    elif re.compile(CPP_GBENCH_RE).search(content):
        f.framework("cpp", "google-benchmark", path)


def _scan_bazel(path: str, content: str, f: _Findings) -> None:
    if re.compile(BAZEL_CODSPEED_RE).search(content):
        f.integrated("cpp", "codspeed-cpp (bazel)", path)


def _scan_workflow(path: str, content: str, f: _Findings) -> None:
    has_action = bool(re.compile(CODSPEED_ACTION_RE).search(content))
    if has_action:
        f.integrated(None, "CodSpeedHQ/action", path)
        if re.compile(GO_BENCH_CI_RE).search(content):
            f.integrated("go", "go test -bench via CodSpeedHQ/action", path)
    if re.compile(PY_CODSPEED_CI_RE).search(content):
        f.integrated("python", "--codspeed", path)
    if re.compile(CARGO_CODSPEED_RE).search(content):
        f.integrated("rust", "cargo-codspeed", path)
    if re.compile(CPP_CODSPEED_RE).search(content):
        f.integrated("cpp", "-DCODSPEED_MODE", path)


def _scan_codspeed_yml(path: str, content: str, f: _Findings) -> None:
    f.integrated(None, "codspeed.yml", path)


def classify(path: str) -> str | None:
    """Return the detector name for a file path, or None if the file is irrelevant."""
    name = _basename(path)
    if path.startswith(".github/workflows/") and name.endswith((".yml", ".yaml")):
        return "workflow"
    if path in ("codspeed.yml", "codspeed.yaml"):
        return "codspeed_yml"
    if name in PYTHON_FILES or re.compile(REQUIREMENTS_RE).search(path):
        return "python"
    if re.compile(NODE_BENCH_FILE_RE).search(name):
        return "node_bench_file"
    return {
        "Cargo.toml": "cargo",
        "package.json": "package_json",
        "pom.xml": "pom",
        "build.gradle": "gradle",
        "build.gradle.kts": "gradle",
        "settings.gradle": "gradle",
        "settings.gradle.kts": "gradle",
        ".gitmodules": "gitmodules",
        "plugins.sbt": "sbt",
        "CMakeLists.txt": "cmake",
        "BUILD": "bazel",
        "BUILD.bazel": "bazel",
        "MODULE.bazel": "bazel",
        "WORKSPACE": "bazel",
    }.get(name, "go_test" if name.endswith("_test.go") else None)


def _mark_node_bench_file(path: str, content: str, f: _Findings) -> None:
    f.has_node_bench_files = True


DETECTORS = {
    "workflow": _scan_workflow,
    "codspeed_yml": _scan_codspeed_yml,
    "python": _scan_python,
    "node_bench_file": _mark_node_bench_file,
    "cargo": _scan_cargo,
    "package_json": _scan_package_json,
    "pom": _scan_pom,
    "gradle": _scan_gradle,
    "gitmodules": _scan_gitmodules,
    "sbt": _scan_sbt,
    "cmake": _scan_cmake,
    "bazel": _scan_bazel,
    "go_test": _scan_go_test,
}


def _finish(repo_id: str, f: _Findings) -> RepoScan:
    if f.vitest_pending and f.has_node_bench_files:
        f.framework("node", "vitest", "*.bench.* files")
    frameworks = tuple(sorted(f.frameworks))
    codspeed = tuple(sorted(f.codspeed))
    if codspeed:
        status = "codspeed"
    elif frameworks and set(frameworks) <= {"sbt-jmh"}:
        status = "not_addressable"
    elif frameworks:
        status = "benchmarks_without_codspeed"
    else:
        status = "no_benchmarks"
    walltime = bool(f.languages & WALLTIME_ONLY_LANGUAGES) and status in (
        "codspeed",
        "benchmarks_without_codspeed",
    )
    return RepoScan(
        repo_id=repo_id,
        languages=tuple(sorted(f.languages)),
        frameworks=frameworks,
        codspeed=codspeed,
        status=status,
        walltime_only=walltime,
        evidence=tuple(f.evidence),
    )


def scan_repo(repo_id: str, files: Iterable[tuple[str, str]]) -> RepoScan:
    """Scan one repository given (path, content) pairs."""
    findings = _Findings()
    for path, content in files:
        kind = classify(path)
        if kind:
            DETECTORS[kind](path, content, findings)
    return _finish(repo_id, findings)


def scan_manifests(manifests: Sequence[Manifest]) -> dict[str, RepoScan]:
    """Group manifests by repo (one pass) and scan each repo."""
    by_repo: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for m in manifests:
        by_repo[m.repo_id].append((m.path, m.content))
    return {repo_id: scan_repo(repo_id, files) for repo_id, files in sorted(by_repo.items())}


def summarize(scans: Iterable[RepoScan]) -> ScanSummary:
    """Roll repo verdicts up to an account."""
    scans = list(scans)
    frameworks = sorted({fw for s in scans if s.status != "codspeed" for fw in s.frameworks})
    languages = sorted({lang for s in scans for lang in s.languages})
    statuses = [s.status for s in scans]
    addressable = [s for s in statuses if s in ("codspeed", "benchmarks_without_codspeed")]
    return ScanSummary(
        repos_scanned=len(scans),
        repos_codspeed=statuses.count("codspeed"),
        repos_benchmarks_without_codspeed=statuses.count("benchmarks_without_codspeed"),
        frameworks=tuple(frameworks),
        languages=tuple(languages),
        walltime_only=any(s.walltime_only for s in scans),
        not_addressable_only="not_addressable" in statuses and not addressable,
        evidence=tuple(e for s in scans for e in s.evidence)[:6],
    )
