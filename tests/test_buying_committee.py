"""Persona classification and the buying committee picks."""

from __future__ import annotations

import pytest

from conftest import contact, user, user_features
from signal_engine.buying_committee import (
    buying_committee,
    classify_persona,
    first_name,
    make_contact,
)


@pytest.mark.parametrize(
    ("title", "persona"),
    [
        ("CTO", "vp_cto"),
        ("VP of Engineering", "vp_cto"),
        ("Head of Engineering", "vp_cto"),
        ("Director of Engineering", "eng_manager"),
        ("Senior Engineering Manager", "eng_manager"),
        ("Platform Engineer", "platform_devex"),
        ("DevEx Lead", "platform_devex"),
        ("Build & Release Engineer", "platform_devex"),
        ("Staff Engineer", "staff_principal"),
        ("Software Architect", "staff_principal"),
        ("Senior Software Engineer", "ic_engineer"),
        ("Open Source Maintainer", "ic_engineer"),
        ("Bot", "ic_engineer"),
    ],
)
def test_classify_persona(cfg, title: str, persona: str) -> None:
    assert classify_persona(title, cfg) == persona


def test_make_contact(cfg) -> None:
    c = make_contact(user("u1", "a@b.example", title="CTO"), cfg)
    assert c.persona == "vp_cto"
    assert c.email == "a@b.example"


def test_committee_picks() -> None:
    people = [
        contact("ic1"),
        contact("ic2", "staff_principal"),
        contact("plat", "platform_devex"),
        contact("mgr", "eng_manager"),
    ]
    feats = {
        "ic1": user_features("ic1", value_score=3.0, event_count=50),
        "ic2": user_features("ic2", value_score=9.0, event_count=5),
        "plat": user_features("plat", value_score=20.0, event_count=10),
        "mgr": user_features("mgr", value_score=1.0, event_count=2),
    }
    c = buying_committee(people, feats)
    assert c.champion.user_id == "ic2"
    assert c.technical_evaluator.user_id == "plat"
    assert c.economic_buyer.user_id == "mgr"


def test_vp_preferred_over_manager_and_unknown_buyer() -> None:
    feats = {u: user_features(u, value_score=1.0) for u in ("ic", "vp", "mgr")}
    people = [contact("ic"), contact("vp", "vp_cto"), contact("mgr", "eng_manager")]
    assert buying_committee(people, feats).economic_buyer.user_id == "vp"
    only_ic = buying_committee([contact("ic")], feats)
    assert only_ic.economic_buyer is None
    assert only_ic.technical_evaluator is None


def test_champion_falls_back_to_any_active_persona() -> None:
    feats = {"mgr": user_features("mgr", value_score=2.0)}
    c = buying_committee([contact("mgr", "eng_manager")], feats)
    assert c.champion.user_id == "mgr"
    assert buying_committee([contact("x")], {}).champion is None


def test_first_name() -> None:
    assert first_name(contact("u", name="Robin Vale")) == "Robin"
    assert first_name(None) == "there"
    assert first_name(None, "team") == "team"
