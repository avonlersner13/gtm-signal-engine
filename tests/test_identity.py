"""Identity waterfall: every step, bot exclusion, parent rollup and report rates."""

from __future__ import annotations

from conftest import EventLog, company, user
from signal_engine.identity import (
    account_roots,
    build_index,
    commit_domains,
    identity_report,
    normalize_domain,
    resolve_identities,
    resolve_user,
)
from signal_engine.models import Resolution

ACME = company(
    "c1",
    domain="acme.example",
    alias_domains=("getacme.example",),
    github_orgs=("acme", "acme-oss"),
)
SUB = company("c2", domain="acmesub.example", github_orgs=("acmesub",), parent_id="c1")
OTHER = company("c3", domain="other.example", github_orgs=("other",))
INDEX = build_index([ACME, SUB, OTHER])


def resolve(cfg, u, commit=None) -> Resolution:
    return resolve_user(u, INDEX, commit, cfg)


def test_corporate_domain(cfg) -> None:
    r = resolve(cfg, user("u1", "pat@acme.example"))
    assert (r.company_id, r.account_id, r.method, r.confidence) == (
        "c1",
        "c1",
        "corporate_domain",
        1.0,
    )


def test_corporate_subdomain_and_case(cfg) -> None:
    r = resolve(cfg, user("u1", "Pat@EU.Acme.Example "))
    assert r.method == "corporate_domain"
    assert r.company_id == "c1"


def test_alias_domain(cfg) -> None:
    r = resolve(cfg, user("u1", "pat@getacme.example"))
    assert (r.company_id, r.method, r.confidence) == ("c1", "alias_domain", 0.95)


def test_github_org_with_unknown_domain(cfg) -> None:
    r = resolve(cfg, user("u1", "pat@patsite.example", orgs=["acme-oss"]))
    assert (r.company_id, r.method, r.confidence) == ("c1", "github_org", 0.9)


def test_freemail_plus_github_org(cfg) -> None:
    r = resolve(cfg, user("u1", "pat@mail.example", orgs=["ACME"]))
    assert (r.company_id, r.method, r.confidence) == ("c1", "freemail_github_org", 0.8)


def test_commit_email_domain(cfg) -> None:
    r = resolve(cfg, user("u1", "pat@mail.example"), commit="acme.example")
    assert (r.company_id, r.method, r.confidence) == ("c1", "commit_email_domain", 0.7)
    alias = resolve(cfg, user("u1", "pat@mail.example"), commit="getacme.example")
    assert alias.method == "commit_email_domain"


def test_unresolved(cfg) -> None:
    r = resolve(cfg, user("u1", "pat@mail.example", orgs=["unknown-org"]), commit="nowhere.example")
    assert (r.company_id, r.account_id, r.method, r.confidence) == (None, None, "unresolved", 0.0)


def test_waterfall_order_prefers_email_over_org(cfg) -> None:
    r = resolve(cfg, user("u1", "pat@other.example", orgs=["acme"]))
    assert r.company_id == "c3"
    assert r.method == "corporate_domain"


def test_freemail_domain_never_matches_a_company(cfg) -> None:
    freemail_co = company("c9", domain="webmail.example", github_orgs=())
    idx = build_index([freemail_co])
    assert resolve_user(user("u1", "pat@webmail.example"), idx, None, cfg).method == "unresolved"


def test_subsidiary_rolls_up_to_parent_but_keeps_child(cfg) -> None:
    r = resolve(cfg, user("u1", "pat@acmesub.example"))
    assert r.company_id == "c2"
    assert r.account_id == "c1"


def test_bots_are_flagged(cfg) -> None:
    r = resolve(cfg, user("b1", "ci@noreply.github.example", orgs=["acme"], login="acme-ci[bot]"))
    assert r.is_bot
    assert r.company_id == "c1"


def test_account_roots_handle_chains_cycles_and_missing_parents() -> None:
    chain = [company("a"), company("b", parent_id="a"), company("c", parent_id="b")]
    assert account_roots(chain) == {"a": "a", "b": "a", "c": "a"}
    cycle = [company("x", parent_id="y"), company("y", parent_id="x")]
    roots = account_roots(cycle)
    assert set(roots.values()) <= {"x", "y"}
    orphan = [company("o", parent_id="missing")]
    assert account_roots(orphan) == {"o": "o"}


def test_normalize_domain() -> None:
    assert normalize_domain(" Pat@WWW.Acme.Example. ") == "acme.example"
    assert normalize_domain("acme.example") == "acme.example"


def test_commit_domains_pick_most_frequent_then_alphabetical() -> None:
    log = EventLog()
    for _ in range(2):
        log.add("u1", "pr_authored_private", 3, "r1", commit_email_domain="b.example")
    log.add("u1", "pr_authored_private", 2, "r1", commit_email_domain="A.example")
    log.add("u2", "pr_authored_private", 2, "r1", commit_email_domain="z.example")
    log.add("u2", "pr_authored_private", 2, "r1", commit_email_domain="y.example")
    log.add("u3", "pr_authored_private", 2, "r1")
    log.add("u3", "run_completed", 2, "r1", commit_email_domain="ignored.example")
    assert commit_domains(log.events()) == {"u1": "b.example", "u2": "y.example"}


def test_report_excludes_bots_and_computes_rates() -> None:
    res = [
        Resolution("u1", "c1", "c1", "corporate_domain", 1.0, False),
        Resolution("u2", "c1", "c1", "github_org", 0.9, False),
        Resolution("u3", None, None, "unresolved", 0.0, False),
        Resolution("u4", None, None, "unresolved", 0.0, False),
        Resolution("b1", "c1", "c1", "github_org", 0.9, True),
    ]
    report = identity_report(res)
    assert report.people == 4
    assert report.bots_excluded == 1
    assert report.unresolved_rate == 0.5
    assert report.fallback_rate == 0.25
    assert report.by_method == {"corporate_domain": 1, "github_org": 1, "unresolved": 2}
    empty = identity_report([])
    assert (empty.unresolved_rate, empty.fallback_rate) == (0.0, 0.0)


def test_resolve_identities_end_to_end(cfg) -> None:
    users = [
        user("u1", "pat@acme.example"),
        user("u2", "sam@mail.example"),
        user("b1", "x@noreply.github.example", orgs=["acme"], login="acme-ci[bot]"),
    ]
    events = (
        EventLog()
        .add("u2", "pr_authored_private", 1, "r1", commit_email_domain="acmesub.example")
        .events()
    )
    result = resolve_identities(users, [ACME, SUB, OTHER], events, cfg)
    assert result.resolutions["u2"].account_id == "c1"
    assert result.resolutions["u2"].company_id == "c2"
    assert result.report.people == 2
    assert result.report.bots_excluded == 1
    assert result.org_to_company["acme-oss"] == "c1"
    assert result.company_to_account["c2"] == "c1"
