"""Scanner rules: one test per documented detection case, plus edge cases."""

from __future__ import annotations

import pytest

from signal_engine.models import Manifest
from signal_engine.scanner import classify, scan_manifests, scan_repo, summarize

WORKFLOW = (
    "jobs:\n  b:\n    steps:\n      - uses: CodSpeedHQ/action@v4\n"
    "        with:\n          run: {run}\n"
)


def scan(*files: tuple[str, str]):
    return scan_repo("r1", files)


@pytest.mark.parametrize(
    ("path", "content", "framework"),
    [
        (
            "pyproject.toml",
            '[dependency-groups]\ndev = ["pytest-benchmark>=4"]\n',
            "pytest-benchmark",
        ),
        ("pyproject.toml", '[project]\noptional-dependencies = { b = ["asv>=0.6"] }\n', "asv"),
        ("requirements-dev.txt", "pytest-benchmark==4.0.0\n", "pytest-benchmark"),
        ("requirements.txt", "asv\n", "asv"),
        ("uv.lock", '[[package]]\nname = "pytest-benchmark"\n', "pytest-benchmark"),
        ("poetry.lock", '[[package]]\nname = "pytest-benchmark"\n', "pytest-benchmark"),
        ("Pipfile", '[dev-packages]\npytest-benchmark = "*"\n', "pytest-benchmark"),
    ],
)
def test_python_benchmarks_without_codspeed(path: str, content: str, framework: str) -> None:
    result = scan((path, content))
    assert result.status == "benchmarks_without_codspeed"
    assert result.frameworks == (framework,)
    assert result.languages == ("python",)


def test_python_word_asv_inside_other_names_is_ignored() -> None:
    assert scan(("requirements.txt", "canvas==1.0\nasvx==2\n")).status == "no_benchmarks"


def test_python_pytest_codspeed_is_integrated() -> None:
    result = scan(
        (
            "pyproject.toml",
            '[dependency-groups]\ndev = ["pytest-codspeed>=3", "pytest-benchmark"]\n',
        )
    )
    assert result.status == "codspeed"
    assert "pytest-codspeed" in result.codspeed


def test_python_codspeed_flag_in_ci() -> None:
    result = scan((".github/workflows/bench.yml", "steps:\n  - run: pytest tests/ --codspeed\n"))
    assert result.status == "codspeed"
    assert "--codspeed" in result.codspeed


@pytest.mark.parametrize("crate", ["criterion", "divan", "bencher"])
def test_rust_plain_bench_crates(crate: str) -> None:
    result = scan(("Cargo.toml", f'[package]\nname = "x"\n\n[dev-dependencies]\n{crate} = "0.5"\n'))
    assert result.status == "benchmarks_without_codspeed"
    assert result.frameworks == (crate,)


@pytest.mark.parametrize("crate", ["criterion", "divan", "bencher"])
def test_rust_package_rename_trap_is_codspeed(crate: str) -> None:
    toml = (
        f'[dev-dependencies]\n{crate} = {{ package = "codspeed-{crate}-compat", version = "*" }}\n'
    )
    result = scan(("Cargo.toml", toml))
    assert result.status == "codspeed"
    assert result.frameworks == ()
    assert result.codspeed == (f"codspeed-{crate}-compat",)


def test_rust_renamed_key_to_other_crate_is_not_a_framework() -> None:
    toml = '[dev-dependencies]\ncriterion = { package = "my-fork", version = "1" }\n'
    assert scan(("Cargo.toml", toml)).status == "no_benchmarks"


def test_rust_workspace_and_target_dev_dependencies() -> None:
    toml = (
        '[workspace.dependencies]\ndivan = "0.1"\n\n'
        "[target.'cfg(unix)'.dev-dependencies]\ncriterion = \"0.5\"\n"
    )
    assert scan(("Cargo.toml", toml)).frameworks == ("criterion", "divan")


def test_rust_cargo_codspeed_in_ci() -> None:
    result = scan((".github/workflows/ci.yml", "run: cargo install cargo-codspeed\n"))
    assert "cargo-codspeed" in result.codspeed


def test_malformed_cargo_is_skipped_not_fatal() -> None:
    result = scan(("Cargo.toml", '[dev-dependencies\ncriterion = "0.5'))
    assert result.status == "no_benchmarks"
    assert any("unparseable" in e for e in result.evidence)


@pytest.mark.parametrize(
    ("package", "framework"),
    [
        ("tinybench", "tinybench"),
        ("benchmark", "benchmark.js"),
        ("@playwright/test", "playwright"),
        ("playwright", "playwright"),
    ],
)
def test_node_bench_packages(package: str, framework: str) -> None:
    result = scan(("package.json", f'{{"devDependencies": {{"{package}": "^1"}}}}'))
    assert result.frameworks == (framework,)


def test_node_vitest_needs_bench_files() -> None:
    pkg = ("package.json", '{"devDependencies": {"vitest": "^2"}}')
    assert scan(pkg).status == "no_benchmarks"
    with_bench = scan(pkg, ("src/a.bench.ts", "bench()"))
    assert with_bench.frameworks == ("vitest",)
    assert scan(pkg, ("lib/b.bench.mjs", "")).frameworks == ("vitest",)


@pytest.mark.parametrize(
    "plugin",
    [
        "@codspeed/vitest-plugin",
        "@codspeed/tinybench-plugin",
        "@codspeed/benchmark.js-plugin",
        "@codspeed/playwright-plugin",
    ],
)
def test_node_codspeed_plugins(plugin: str) -> None:
    result = scan(("package.json", f'{{"dependencies": {{"{plugin}": "^4"}}}}'))
    assert result.status == "codspeed"
    assert result.codspeed == (plugin,)


def test_malformed_package_json() -> None:
    assert scan(("package.json", '{"devDependencies": {')).status == "no_benchmarks"


def test_go_benchmark_without_codspeed_is_walltime_only() -> None:
    test_go = 'package x\nimport "testing"\nfunc BenchmarkParse(b *testing.B) {}\n'
    result = scan(("parse_test.go", test_go))
    assert result.status == "benchmarks_without_codspeed"
    assert result.frameworks == ("go-testing-bench",)
    assert result.walltime_only


def test_go_tests_without_benchmarks() -> None:
    result = scan(("x_test.go", "func TestX(t *testing.T) {}\n"))
    assert result.status == "no_benchmarks"
    assert not result.walltime_only


def test_go_codspeed_detectable_only_in_ci() -> None:
    test_go = "func BenchmarkX(b *testing.B) {}\n"
    without_ci = scan(("x_test.go", test_go))
    assert without_ci.status == "benchmarks_without_codspeed"
    with_ci = scan(
        ("x_test.go", test_go),
        (".github/workflows/cs.yml", WORKFLOW.format(run="go test -bench=. ./...")),
    )
    assert with_ci.status == "codspeed"
    assert "go test -bench via CodSpeedHQ/action" in with_ci.codspeed
    assert with_ci.walltime_only


def test_go_bench_command_without_codspeed_action_is_not_integration() -> None:
    result = scan((".github/workflows/ci.yml", "run: go test -bench=. ./...\n"))
    assert result.status == "no_benchmarks"


def test_jvm_maven_jmh_and_codspeed_group() -> None:
    pom = (
        "<project><dependencies><dependency><groupId>{g}</groupId>"
        "<artifactId>jmh-core</artifactId></dependency></dependencies></project>"
    )
    plain = scan(("pom.xml", pom.format(g="org.openjdk.jmh")))
    assert plain.frameworks == ("jmh",)
    assert plain.walltime_only
    codspeed = scan(("pom.xml", pom.format(g="io.codspeed.jmh")))
    assert codspeed.status == "codspeed"
    assert codspeed.codspeed == ("io.codspeed.jmh",)


def test_jvm_maven_namespaced_pom() -> None:
    pom = (
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><dependencies><dependency>'
        "<groupId>org.openjdk.jmh</groupId><artifactId>jmh-core</artifactId>"
        "</dependency></dependencies></project>"
    )
    assert scan(("pom.xml", pom)).frameworks == ("jmh",)


def test_jvm_malformed_pom() -> None:
    assert scan(("pom.xml", "<project><dependencies>")).status == "no_benchmarks"


def test_jvm_gradle_champeau_plugin_and_codspeed() -> None:
    assert scan(("build.gradle.kts", 'id("me.champeau.jmh")')).frameworks == ("jmh",)
    assert scan(("build.gradle", "implementation 'org.openjdk.jmh:jmh-core:1.37'")).frameworks == (
        "jmh",
    )
    assert (
        scan(("build.gradle.kts", 'implementation("io.codspeed.jmh:jmh-core:1")')).status
        == "codspeed"
    )


def test_jvm_codspeed_jvm_submodule_and_fork() -> None:
    sub = scan((".gitmodules", '[submodule "codspeed-jvm"]\n\tpath = codspeed-jvm\n'))
    assert sub.codspeed == ("codspeed-jvm submodule",)
    fork = scan(("settings.gradle.kts", 'includeBuild("../codspeed-jvm/jmh-fork")\n'))
    assert fork.codspeed == ("codspeed-jvm jmh-fork",)
    assert scan((".gitmodules", '[submodule "other"]\n')).status == "no_benchmarks"


def test_scala_sbt_jmh_is_not_addressable() -> None:
    result = scan(
        ("project/plugins.sbt", 'addSbtPlugin("pl.project13.scala" % "sbt-jmh" % "0.4.7")\n')
    )
    assert result.status == "not_addressable"
    assert result.frameworks == ("sbt-jmh",)
    assert not result.walltime_only


def test_sbt_plus_supported_framework_is_addressable() -> None:
    result = scan(
        ("project/plugins.sbt", 'addSbtPlugin("pl.project13.scala" % "sbt-jmh" % "0.4.7")\n'),
        ("pyproject.toml", '["pytest-benchmark"]'),
    )
    assert result.status == "benchmarks_without_codspeed"


def test_cpp_google_benchmark_and_codspeed() -> None:
    assert scan(("CMakeLists.txt", "target_link_libraries(b benchmark::benchmark)")).frameworks == (
        "google-benchmark",
    )
    assert scan(("CMakeLists.txt", "BENCHMARK_MAIN()")).frameworks == ("google-benchmark",)
    fetch = (
        "FetchContent_Declare(codspeed GIT_REPOSITORY https://github.com/CodSpeedHQ/codspeed-cpp)"
    )
    assert scan(("CMakeLists.txt", fetch)).status == "codspeed"
    mode = scan((".github/workflows/x.yml", "run: cmake -B build -DCODSPEED_MODE=instrumentation"))
    assert "-DCODSPEED_MODE" in mode.codspeed


def test_cpp_bazel_compat() -> None:
    result = scan(
        ("BUILD.bazel", 'load("@codspeed_cpp//:defs.bzl", "codspeed_google_benchmark_compat")')
    )
    assert result.codspeed == ("codspeed-cpp (bazel)",)
    assert scan(("WORKSPACE", "nothing here")).status == "no_benchmarks"


def test_any_language_codspeed_action_and_yml() -> None:
    action = scan((".github/workflows/codspeed.yaml", WORKFLOW.format(run="make bench")))
    assert action.codspeed == ("CodSpeedHQ/action",)
    yml = scan(("codspeed.yml", "benchmarks: []\n"))
    assert yml.codspeed == ("codspeed.yml",)


def test_codspeed_yml_must_be_at_repo_root() -> None:
    assert classify("sub/codspeed.yml") is None
    assert classify("codspeed.yaml") == "codspeed_yml"


def test_codspeed_action_needs_version_tag() -> None:
    assert (
        scan((".github/workflows/x.yml", "uses: CodSpeedHQ/action@main")).status == "no_benchmarks"
    )


def test_irrelevant_files_are_ignored() -> None:
    assert classify("README.md") is None
    assert classify("src/main.rs") is None
    assert scan(("README.md", "pytest-benchmark criterion")).status == "no_benchmarks"


def test_scan_manifests_groups_by_repo() -> None:
    manifests = [
        Manifest("r2", "Cargo.toml", '[dev-dependencies]\ncriterion = "0.5"\n'),
        Manifest("r1", "codspeed.yml", ""),
        Manifest("r2", "codspeed.yml", ""),
    ]
    result = scan_manifests(manifests)
    assert list(result) == ["r1", "r2"]
    assert result["r2"].status == "codspeed"


def test_summarize_rolls_up_status_and_flags() -> None:
    scans = scan_manifests(
        [
            Manifest("a", "x_test.go", "func BenchmarkA(b *testing.B) {}"),
            Manifest("b", "codspeed.yml", ""),
            Manifest("c", "project/plugins.sbt", "sbt-jmh"),
        ]
    )
    summary = summarize(scans.values())
    assert summary.repos_scanned == 3
    assert summary.repos_codspeed == 1
    assert summary.repos_benchmarks_without_codspeed == 1
    assert summary.walltime_only
    assert not summary.not_addressable_only
    assert summary.frameworks == ("go-testing-bench", "sbt-jmh")
    only_sbt = summarize([scans["c"]])
    assert only_sbt.not_addressable_only
