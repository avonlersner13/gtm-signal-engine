"""Hidden ground-truth labels: converted_to_paid, expanded, churned.

Labels describe what happens AFTER the fixed "now" (the next quarter). They come from
the company's hidden archetype and propensity, while observable signals come from a
separate noisy "strength" draw, so signals predict outcomes only partly. Scoring must
never read these labels; only evaluate.py does.
"""

from __future__ import annotations

import random

from signal_engine.generate.companies import CompanyPlan
from signal_engine.models import Company, GroundTruth

BASE_CONVERT = {
    "spreading": 0.55,
    "blocked": 0.65,
    "walltime": 0.45,
    "security": 0.5,
    "ai_native": 0.4,
    "small_team": 0.1,
    "oss_free": 0.02,
    "stalled": 0.03,
    "churner": 0.04,
    "market_only": 0.03,
    "not_addressable": 0.0,
}
BASE_EXPAND = {
    "spreading": 0.45,
    "blocked": 0.35,
    "security": 0.3,
    "ai_native": 0.3,
    "walltime": 0.25,
}
BASE_CHURN = {"churner": 0.7, "small_team": 0.08, "oss_free": 0.05, "stalled": 0.12}


def _clamp(x: float) -> float:
    return min(1.0, max(0.0, x))


def label_company(rng: random.Random, plan: CompanyPlan, already_paid: bool) -> GroundTruth:
    """Draw noisy outcome labels for one company."""
    lift = 0.4 + 1.2 * plan.propensity
    converted = rng.random() < _clamp(BASE_CONVERT[plan.archetype] * lift)
    expanded = (converted or already_paid) and rng.random() < _clamp(
        BASE_EXPAND.get(plan.archetype, 0.05) * lift
    )
    churned = rng.random() < _clamp(BASE_CHURN.get(plan.archetype, 0.03) * (1.6 - plan.propensity))
    if churned:
        converted = expanded = False
    if already_paid:  # already a customer: the only upside left is expansion
        converted = False
    return GroundTruth(plan.company_id, converted, expanded, churned)


def generate_truth(
    rng: random.Random,
    companies: list[Company],
    plans: dict[str, CompanyPlan],
    upgraded: set[str],
) -> list[GroundTruth]:
    """Label every company; companies that already upgraded can only expand or churn."""
    return [label_company(rng, plans[c.company_id], c.company_id in upgraded) for c in companies]
