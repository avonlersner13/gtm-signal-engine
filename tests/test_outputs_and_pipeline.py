"""Pipeline wiring, every output renderer, evaluation."""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta

import pytest

from signal_engine.evaluate import account_labels, evaluate, render_evaluation
from signal_engine.models import GroundTruth
from signal_engine.outputs import write_outputs
from signal_engine.outputs.account_brief import (
    MAX_EMAIL_WORDS,
    brief_targets,
    render_account_brief,
    suggested_email,
    timeline,
)
from signal_engine.outputs.alerts import render_alerts
from signal_engine.outputs.crm_export import (
    ACCOUNT_FIELDS,
    CONTACT_FIELDS,
    render_crm_accounts,
    render_crm_contacts,
)
from signal_engine.outputs.fmt import first_sentences, money, revenue_line, signed
from signal_engine.outputs.founder_brief import (
    biggest_movers,
    product_feedback,
    render_founder_brief,
)
from signal_engine.outputs.funnel import funnel_rows, render_funnel
from signal_engine.pipeline import compute_week, events_until


@pytest.fixture(scope="module")
def weeks(small_ds, cfg):
    w1 = compute_week(small_ds, cfg, cfg.now - timedelta(days=7), "2026-10-05")
    prev = {a.account_id: a.score for a in w1.accounts}
    stages = {a.account_id: a.stage for a in w1.accounts}
    w2 = compute_week(small_ds, cfg, cfg.now, "2026-10-12", prev, stages)
    return w1, w2


def test_events_until_is_inclusive(small_ds) -> None:
    cut = small_ds.events[100].ts
    kept = events_until(small_ds.events, cut)
    assert kept[-1].ts == cut
    assert all(e.ts <= cut for e in kept)


def test_week_result_is_sorted_and_complete(weeks, small_ds) -> None:
    _, w2 = weeks
    priorities = [a.score.priority for a in w2.accounts]
    assert priorities == sorted(priorities, reverse=True)
    roots = {c.company_id for c in small_ds.companies if c.parent_id is None}
    assert {a.account_id for a in w2.accounts} == roots
    assert any(a.plays for a in w2.accounts)
    assert any(a.score.priority_delta for a in w2.accounts)


def test_new_pqa_uses_previous_stage(weeks, small_ds, cfg) -> None:
    w1, w2 = weeks
    before = {a.account_id: a.stage for a in w1.accounts}
    for a in w2.accounts:
        if a.new_pqa:
            assert before[a.account_id] not in ("pqa", "opportunity")
    fresh = compute_week(small_ds, cfg, cfg.now, "2026-10-12")
    for a in fresh.accounts:
        assert a.new_pqa == (a.pqa and a.stage_entries["pqa"] > cfg.now - timedelta(days=7))


def test_founder_brief(weeks, cfg) -> None:
    w1, w2 = weeks
    text = render_founder_brief(w2, cfg)
    for heading in (
        "# Monday pipeline brief: week of 2026-10-12",
        "## Headline",
        "## Top 5 accounts",
        "## Biggest movers",
        "## Churn risks",
        "## Product feedback",
    ):
        assert heading in text
    assert "For Arthur." in text
    founders = [x for x in w2.accounts if x.plays and x.plays[0].owner == "founder"]
    queued = [x for x in w2.accounts if x.plays and x.plays[0].note == "over founder capacity"]
    assert len(founders) <= cfg.org["founder_weekly_capacity"]
    assert (
        f"{len(founders)} founder touches this week; {len(queued)} queued for GTM engineer" in text
    )
    assert text.count("- **Why now:**") == 5
    assert "No prior week" in render_founder_brief(w1, cfg)
    assert len(biggest_movers(w2.accounts)) == 3
    assert "never created a baseline" in product_feedback(w2)


def test_account_briefs(weeks, cfg) -> None:
    _, w2 = weeks
    targets = brief_targets(w2, cfg)
    assert targets[0] == w2.accounts[0]
    founder = [a for a in w2.accounts if a.plays and a.plays[0].owner == "founder"]
    assert {a.account_id for a in founder} <= {a.account_id for a in targets}
    for a in w2.accounts:
        _, body = suggested_email(a, cfg)
        assert len(body.split()) <= MAX_EMAIL_WORDS
        assert body.endswith("Arthur")
    text = render_account_brief(targets[0], w2, cfg)
    for heading in (
        "## Why now",
        "## Seats vs cap",
        "## Runner minutes",
        "## Stage history",
        "## Timeline of key events",
        "## Buying committee",
        "## Plays",
        "## Suggested first email",
        "## Revenue estimate",
        "Assumptions:",
    ):
        assert heading in text
    market = next(a for a in w2.accounts if a.features is None)
    assert timeline(market) == []
    assert "No product events yet." in render_account_brief(market, w2, cfg)


def test_email_is_truncated_to_limit(weeks, cfg) -> None:
    from dataclasses import replace

    _, w2 = weeks
    a = next(a for a in w2.accounts if a.plays)
    long_play = replace(a.plays[0], message="Hi Sam, " + "word " * 300)
    _, body = suggested_email(replace(a, plays=(long_play,)), cfg)
    assert len(body.split()) <= MAX_EMAIL_WORDS
    _, no_play = suggested_email(replace(a, plays=()), cfg)
    assert "one of the people building CodSpeed" in no_play


def test_crm_exports(weeks) -> None:
    _, w2 = weeks
    accounts = list(csv.DictReader(io.StringIO(render_crm_accounts(w2))))
    assert list(accounts[0]) == list(ACCOUNT_FIELDS)
    assert {r["pqa_flag"] for r in accounts} <= {"0", "1"}
    assert all(r["lifecyclestage"] for r in accounts)
    contacts = list(csv.DictReader(io.StringIO(render_crm_contacts(w2))))
    assert list(contacts[0]) == list(CONTACT_FIELDS)
    assert "champion" in {r["committee_role"] for r in contacts}
    assert all(not r["email"].startswith("ci@") for r in contacts)


def test_alerts_are_block_kit_and_above_threshold(weeks, cfg) -> None:
    _, w2 = weeks
    lines = render_alerts(w2, cfg).splitlines()
    assert lines
    for line in lines:
        payload = json.loads(line)
        assert payload["channel"] == "#pipeline-alerts"
        assert payload["blocks"][0]["type"] == "header"
        play = payload["metadata"]["event_payload"]["play_id"]
        assert (
            next(p.priority for p in cfg.plays if p.play_id == play)
            >= cfg.org["alert_min_priority"]
        )


def test_funnel(weeks) -> None:
    _, w2 = weeks
    rows = {r.stage: r for r in funnel_rows(w2)}
    assert rows["signed_up"].conversion is None
    assert rows["installed"].reached <= rows["signed_up"].reached
    assert rows["pqa"].from_stage == "engaged"
    text = render_funnel(w2)
    assert "| PQA |" in text and "## Identity resolution" in text


def test_write_outputs(tmp_path, weeks, cfg) -> None:
    _, w2 = weeks
    stale = tmp_path / "accounts" / "old.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("x")
    paths = write_outputs(w2, cfg, tmp_path)
    names = {p.name for p in paths}
    assert {
        "founder_brief_2026-10-12.md",
        "crm_accounts.csv",
        "crm_contacts.csv",
        "alerts.jsonl",
        "funnel.md",
    } <= names
    assert not stale.exists()


def test_evaluation(weeks, small_ds, cfg) -> None:
    _, w2 = weeks
    ev = evaluate(w2, small_ds.companies, small_ds.truth, cfg.seed)
    assert 0 <= ev.precision[10] <= 1 and ev.accounts == len(w2.accounts)
    assert ev.random_precision[10] == pytest.approx(ev.base_rate, abs=0.05)
    assert all(p.accounts >= p.hits for p in ev.plays)
    text = render_evaluation(ev)
    assert "precision@10" in text and "Honest note" in text


def test_evaluation_math_on_known_labels(weeks, small_ds, cfg) -> None:
    _, w2 = weeks
    top = [a.account_id for a in w2.accounts[:5]]
    truth = [
        GroundTruth(c.company_id, c.company_id in top, False, False) for c in small_ds.companies
    ]
    ev = evaluate(w2, small_ds.companies, truth, cfg.seed)
    assert ev.precision[10] == 0.5
    assert ev.recall_25 == 1.0
    assert ev.lift[10] == pytest.approx(0.5 / (5 / len(w2.accounts)))
    none = evaluate(w2, small_ds.companies, [], cfg.seed)
    assert none.base_rate == 0 and none.lift[10] == 0


def test_account_labels_roll_up_children(small_ds) -> None:
    child = next(c for c in small_ds.companies if c.parent_id)
    labels = account_labels(small_ds.companies, [GroundTruth(child.company_id, False, True, True)])
    assert labels[child.parent_id] == (True, True)


def test_fmt_helpers(weeks) -> None:
    _, w2 = weeks
    assert money(1234.5) == "$1,234" or money(1234.5) == "$1,235"
    assert signed(0) == "0.0" and signed(2) == "+2.0"
    assert first_sentences("One. Two two. Three.", 8) == "One."
    market = next(a for a in w2.accounts if a.features is None and not a.revenue.total_arr)
    assert revenue_line(market) == "no paid upside projected yet"
