"""Synthetic repositories and manifest files covering every scanner detection case.

States per repo: ``none`` (no benchmarks), ``bench`` (benchmarks without CodSpeed) and
``codspeed`` (CodSpeed integrated). Edge cases include the Cargo ``package =`` rename,
Go detectable only through CI, JVM via io.codspeed.jmh, sbt-jmh (not addressable),
vitest without bench files, codspeed.yml, and deliberately malformed files.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from signal_engine.generate.companies import CompanyPlan
from signal_engine.generate.users import UserBundle
from signal_engine.models import Company, Manifest, Repo

_REPO_WORDS_WORDS = (
    "api core engine parser router cache gateway indexer compiler runtime sdk cli worker "
    "scheduler codec storage query search render sync stream vector graph ledger auth "
    "billing ingest planner"
)
REPO_WORDS = tuple(_REPO_WORDS_WORDS.split())
NOT_INTEGRATED = ("stalled",)
PRIVATE_SHARE = {"oss_free": 0.0, "market_only": 0.0, "not_addressable": 0.0}

Files = list[tuple[str, str]]


@dataclass(slots=True)
class RepoBundle:
    """Generated repos plus which ones each company imported into CodSpeed."""

    repos: list[Repo]
    manifests: list[Manifest]
    by_company: dict[str, list[str]]
    enabled: dict[str, list[str]]
    personal_enabled: dict[str, str]


def _workflow(run: str, mode: str = "simulation", runner: str = "ubuntu-latest") -> str:
    return (
        "name: CodSpeed\non:\n  push:\n    branches: [main]\n  pull_request:\n"
        "  workflow_dispatch:\njobs:\n  benchmarks:\n"
        f"    runs-on: {runner}\n    steps:\n      - uses: actions/checkout@v4\n"
        "      - uses: CodSpeedHQ/action@v4\n        with:\n"
        f"          mode: {mode}\n          run: {run}\n"
    )


def _plain_ci(run: str) -> str:
    return (
        "name: CI\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        f"    steps:\n      - uses: actions/checkout@v4\n      - run: {run}\n"
    )


def _python(rng: random.Random, state: str) -> Files:
    if state == "codspeed":
        return [
            (
                "pyproject.toml",
                '[project]\nname = "svc"\ndependencies = ["httpx>=0.27"]\n\n'
                '[dependency-groups]\ndev = ["pytest>=8", "pytest-codspeed>=3.0"]\n',
            ),
            (".github/workflows/codspeed.yml", _workflow("uv run pytest tests/ --codspeed")),
        ]
    if state == "bench":
        return [rng.choice(_PY_BENCH)]
    return [
        ("pyproject.toml", '[project]\nname = "svc"\ndependencies = ["httpx>=0.27"]\n'),
        ("requirements-dev.txt", "pytest==8.3.2\nruff==0.6.0\n"),
    ]


_PY_BENCH: Files = [
    (
        "pyproject.toml",
        '[project]\nname = "svc"\n\n[dependency-groups]\ndev = ["pytest-benchmark>=4.0"]\n',
    ),
    ("requirements-dev.txt", "pytest==8.3.2\npytest-benchmark==4.0.0\n"),
    (
        "pyproject.toml",
        '[project]\nname = "svc"\noptional-dependencies = { bench = ["asv>=0.6"] }\n',
    ),
    ("uv.lock", 'version = 1\n\n[[package]]\nname = "pytest-benchmark"\nversion = "4.0.0"\n'),
    ("poetry.lock", '[[package]]\nname = "pytest-benchmark"\nversion = "4.0.0"\n'),
    ("Pipfile", '[packages]\nrequests = "*"\n\n[dev-packages]\npytest-benchmark = "*"\n'),
]


def _rust(rng: random.Random, state: str) -> Files:
    crate = rng.choice(("criterion", "divan", "bencher"))
    head = '[package]\nname = "core"\nversion = "0.1.0"\n\n[dependencies]\nserde = "1"\n\n'
    if state == "codspeed":
        dep = f'{crate} = {{ package = "codspeed-{crate}-compat", version = "*" }}\n'
        run = "cargo codspeed build && cargo codspeed run"
        return [
            ("Cargo.toml", head + "[dev-dependencies]\n" + dep),
            (".github/workflows/codspeed.yml", _workflow(f"cargo install cargo-codspeed && {run}")),
        ]
    if state == "bench":
        return [("Cargo.toml", head + f'[dev-dependencies]\n{crate} = "0.5"\n')]
    return [("Cargo.toml", head + '[dev-dependencies]\nproptest = "1"\n')]


def _node(rng: random.Random, state: str) -> Files:
    bench_file = (
        "src/parse.bench.ts",
        "import { bench } from 'vitest'\nbench('parse', () => {})\n",
    )
    if state == "codspeed":
        plugin = rng.choice(("vitest", "tinybench"))
        deps = f'"@codspeed/{plugin}-plugin": "^4.0.0", "{plugin}": "^2.0.0"'
        files = [("package.json", '{"name": "web", "devDependencies": {' + deps + "}}"), bench_file]
        return [*files, (".github/workflows/codspeed.yml", _workflow("npx vitest bench"))]
    if state == "bench":
        lib = rng.choice(("vitest", "tinybench", "benchmark", "@playwright/test"))
        files = [("package.json", f'{{"name": "web", "devDependencies": {{"{lib}": "^2.0.0"}}}}')]
        return files + ([bench_file] if lib == "vitest" else [])
    if rng.random() < 0.5:  # vitest without *.bench.* files is NOT benchmarking
        return [("package.json", '{"name": "web", "devDependencies": {"vitest": "^2.0.0"}}')]
    return [("package.json", '{"name": "web", "devDependencies": {"jest": "^29.0.0"}}')]


def _go(rng: random.Random, state: str) -> Files:
    mod = ("go.mod", "module example.test/svc\n\ngo 1.23\n")
    test = 'package svc\n\nimport "testing"\n\nfunc TestParse(t *testing.T) {}\n'
    bench = test + "\nfunc BenchmarkParse(b *testing.B) {\n\tfor b.Loop() {}\n}\n"
    if state == "codspeed":
        wf = _workflow("go test -bench=. ./...", mode="walltime", runner="codspeed-macro")
        return [mod, ("parse_test.go", bench), (".github/workflows/codspeed.yml", wf)]
    if state == "bench":
        return [
            mod,
            ("parse_test.go", bench),
            (".github/workflows/ci.yml", _plain_ci("go test ./...")),
        ]
    return [mod, ("parse_test.go", test)]


def _jvm(rng: random.Random, state: str) -> Files:
    if state == "codspeed" and rng.random() < 0.5:
        return [
            ("settings.gradle.kts", 'includeBuild("../codspeed-jvm/jmh-fork")\n'),
            (".gitmodules", '[submodule "codspeed-jvm"]\n\tpath = codspeed-jvm\n'),
            (".github/workflows/codspeed.yml", _workflow("./gradlew jmh", mode="walltime")),
        ]
    if state == "codspeed":
        return [("pom.xml", _pom("io.codspeed.jmh", "jmh-core"))]
    if state == "bench" and rng.random() < 0.5:
        return [("build.gradle.kts", 'plugins {\n    id("me.champeau.jmh") version "0.7.2"\n}\n')]
    if state == "bench":
        return [("pom.xml", _pom("org.openjdk.jmh", "jmh-core"))]
    return [("pom.xml", _pom("org.junit.jupiter", "junit-jupiter"))]


def _pom(group: str, artifact: str) -> str:
    return (
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
        "<dependencies><dependency>"
        f"<groupId>{group}</groupId><artifactId>{artifact}</artifactId><version>1.37</version>"
        "</dependency></dependencies></project>"
    )


def _scala(rng: random.Random, state: str) -> Files:
    if state in ("bench", "codspeed"):
        return [
            ("project/plugins.sbt", 'addSbtPlugin("pl.project13.scala" % "sbt-jmh" % "0.4.7")\n')
        ]
    return [("build.sbt", 'scalaVersion := "3.5.0"\n')]


def _cpp(rng: random.Random, state: str) -> Files:
    if state == "codspeed" and rng.random() < 0.3:
        return [
            ("MODULE.bazel", 'bazel_dep(name = "codspeed_cpp", version = "1.0.0")\n'),
            (
                "BUILD.bazel",
                'load("@codspeed_cpp//:defs.bzl", "codspeed_google_benchmark_compat")\n',
            ),
        ]
    if state == "codspeed":
        cmake = (
            "include(FetchContent)\nFetchContent_Declare(codspeed\n"
            "  GIT_REPOSITORY https://github.com/CodSpeedHQ/codspeed-cpp\n  GIT_TAG main)\n"
            "target_link_libraries(bench benchmark::benchmark)\n"
        )
        wf = _workflow("cmake -B build -DCODSPEED_MODE=instrumentation && ./build/bench")
        return [("CMakeLists.txt", cmake), (".github/workflows/codspeed.yml", wf)]
    if state == "bench":
        cmake = "find_package(benchmark REQUIRED)\nadd_executable(bench bench.cpp)\n"
        return [("CMakeLists.txt", cmake + "target_link_libraries(bench benchmark::benchmark)\n")]
    return [("CMakeLists.txt", "add_executable(app main.cpp)\n")]


BUILDERS: dict[str, Callable[[random.Random, str], Files]] = {
    "python": _python,
    "rust": _rust,
    "node": _node,
    "go": _go,
    "jvm": _jvm,
    "scala": _scala,
    "cpp": _cpp,
}
MALFORMED = {
    "Cargo.toml": '[dev-dependencies\ncriterion = "0.5',
    "package.json": '{"devDependencies": {"tinybench": ',
    "pom.xml": "<project><dependencies><dependency><groupId>org.openjdk.jmh",
}


def manifest_files(rng: random.Random, language: str, state: str) -> Files:
    """Return (path, content) pairs for one repo in a given language and state."""
    files = list(BUILDERS[language](rng, state))
    if state == "codspeed" and rng.random() < 0.15:
        files.append(("codspeed.yml", "benchmarks:\n  - name: default\n"))
    if rng.random() < 0.02:
        files = [(p, MALFORMED.get(p, c)) for p, c in files]
    return files


def _repo_language(rng: random.Random, plan: CompanyPlan) -> str:
    if plan.archetype in ("walltime", "not_addressable") or rng.random() < 0.7:
        return plan.language
    return rng.choice(("python", "rust", "node", "go", "cpp"))


def _repo_state(rng: random.Random, plan: CompanyPlan, enabled: bool) -> str:
    if enabled:
        return "none" if plan.archetype in NOT_INTEGRATED else "codspeed"
    if plan.has_market_benchmarks and rng.random() < 0.7:
        return "bench"
    return "bench" if rng.random() < 0.08 else "none"


def generate_repos(
    rng: random.Random,
    companies: list[Company],
    plans: dict[str, CompanyPlan],
    users: UserBundle,
) -> RepoBundle:
    """Generate repos and manifests for every company org and some independents."""
    bundle = RepoBundle([], [], {}, {}, {})
    seq = 0
    for c in companies:
        plan = plans[c.company_id]
        has_users = bool(users.by_company.get(c.company_id))
        n_repos = rng.randint(1, 4) + (2 if plan.archetype in ("spreading", "security") else 0)
        n_enabled = min(n_repos, rng.randint(1, 3)) if has_users else 0
        private_share = PRIVATE_SHARE.get(plan.archetype, 0.7)
        ids, enabled = [], []
        for i in range(n_repos):
            seq += 1
            enabled_repo = i < n_enabled
            private = rng.random() < private_share
            if enabled_repo and plan.archetype in ("spreading", "blocked", "small_team"):
                private = True
            repo = Repo(
                repo_id=f"r{seq:06d}",
                owner=c.github_orgs[i % len(c.github_orgs)],
                name=f"{rng.choice(REPO_WORDS)}-{rng.choice(REPO_WORDS)}",
                is_private=private,
                language=_repo_language(rng, plan),
            )
            _add_repo(rng, bundle, repo, _repo_state(rng, plan, enabled_repo))
            ids.append(repo.repo_id)
            if enabled_repo:
                enabled.append(repo.repo_id)
        bundle.by_company[c.company_id] = ids
        bundle.enabled[c.company_id] = enabled
    logins = {u.user_id: u.github_login for u in users.users}
    for uid in users.independents:
        if rng.random() < 0.35:
            seq += 1
            repo = Repo(f"r{seq:06d}", logins[uid], rng.choice(REPO_WORDS), False, "python")
            activated = rng.random() < 0.5
            _add_repo(rng, bundle, repo, "codspeed" if activated else "bench")
            if activated:
                bundle.personal_enabled[uid] = repo.repo_id
    return bundle


def _add_repo(rng: random.Random, bundle: RepoBundle, repo: Repo, state: str) -> None:
    bundle.repos.append(repo)
    for path, content in manifest_files(rng, repo.language, state):
        bundle.manifests.append(Manifest(repo.repo_id, path, content))
