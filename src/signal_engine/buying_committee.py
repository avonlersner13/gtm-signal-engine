"""Buying committee per account: champion, technical evaluator, likely economic buyer.

* Champion: the most engaged IC / staff engineer, weighted toward value events.
* Technical evaluator: the platform / DevEx persona with the most activity.
* Economic buyer: a VP/CTO if seen, else an engineering manager; otherwise unknown
  ("ask the champion").
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import lru_cache

from signal_engine.config import Config
from signal_engine.models import BuyingCommittee, Contact, User, UserFeatures

UNKNOWN_BUYER = "unknown: ask the champion"
CHAMPION_PERSONAS = ("ic_engineer", "staff_principal")


@lru_cache(maxsize=64)
def _keyword_pattern(keywords: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")\b")


def classify_persona(title: str, cfg: Config) -> str:
    """Map a job title to a persona using the ordered keyword lists in signals.toml.

    Keywords match on word boundaries ("cto" must not match "director").
    """
    lowered = title.lower()
    for persona, keywords in cfg.persona_order:
        if _keyword_pattern(keywords).search(lowered):
            return persona
    return cfg.persona_default


def make_contact(user: User, cfg: Config) -> Contact:
    """Build a contact record for a user."""
    return Contact(
        user.user_id, user.name, user.title, classify_persona(user.title, cfg), user.email
    )


def _best(
    contacts: Sequence[Contact],
    features: Mapping[str, UserFeatures],
    personas: tuple[str, ...],
    key: str,
) -> Contact | None:
    pool = [c for c in contacts if c.persona in personas and c.user_id in features]
    if not pool:
        return None

    def rank(c: Contact) -> tuple[float, str]:
        f = features[c.user_id]
        value = f.value_score if key == "value" else float(f.event_count)
        return (-value, c.user_id)

    return min(pool, key=rank)


def buying_committee(
    contacts: Sequence[Contact], features: Mapping[str, UserFeatures]
) -> BuyingCommittee:
    """Pick the committee from an account's (non-bot) contacts."""
    champion = _best(contacts, features, CHAMPION_PERSONAS, "value")
    if champion is None:
        everyone = tuple({c.persona for c in contacts})
        champion = _best(contacts, features, everyone, "value")
    evaluator = _best(contacts, features, ("platform_devex",), "count")
    buyer = _best(contacts, features, ("vp_cto",), "value") or _best(
        contacts, features, ("eng_manager",), "value"
    )
    return BuyingCommittee(champion, evaluator, buyer)


def first_name(contact: Contact | None, fallback: str = "there") -> str:
    """First name of a contact, or a fallback."""
    return contact.name.split(" ")[0] if contact else fallback
