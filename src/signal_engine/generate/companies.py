"""Synthetic firmographics: fake companies from word lists with .example domains.

Each company also gets a hidden :class:`CompanyPlan` (archetype, propensity, signal
strength) that drives its journey. Plans never leave the generator except through
truth.py, which turns them into noisy ground-truth labels.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from signal_engine.models import Company

_ADJECTIVES_WORDS = (
    "Amber Arctic Atlas Basalt Beacon Binary Blue Bright Cedar Cinder Cobalt Copper "
    "Crimson Crystal Delta Ember Falcon Fern Flint Granite Harbor Helix Indigo Iron "
    "Juniper Kestrel Lumen Maple Meridian Nimbus Northwind Onyx Orbit Pacific Pine Pixel "
    "Quartz Quiet Rapid Redwood Saffron Sierra Silver Slate Solar Spruce Stellar Summit "
    "Tidal Topaz Umber Vector Velvet Violet Willow Zenith Zephyr Lucky Hollow Bold"
)
ADJECTIVES = tuple(_ADJECTIVES_WORDS.split())
_NOUNS_WORDS = (
    "Anchor Arrow Badger Bay Bridge Canyon Circuit Cloud Comet Compass Crane Dune Engine "
    "Field Forge Fox Frame Garden Gate Grid Grove Hawk Hive Island Lake Lantern Ledger "
    "Lens Loop Mesa Mill Moth Node Oak Otter Path Peak Pier Prism Quill Raven Reef Ridge "
    "River Rocket Sail Shore Signal Spark Stack Stone Stream Thread Tower Trail Vale "
    "Wave Wharf Yard Kiln"
)
NOUNS = tuple(_NOUNS_WORDS.split())
_SUFFIXES_WORDS = (
    "Labs Systems Cloud Data Works Technologies AI Software Networks Robotics Analytics "
    "Games Health Pay DB"
)
SUFFIXES = tuple(_SUFFIXES_WORDS.split())
INDUSTRIES = (
    ("devtools", 14),
    ("fintech", 13),
    ("infrastructure", 12),
    ("databases", 6),
    ("ai_ml", 12),
    ("gaming", 7),
    ("ecommerce", 10),
    ("healthtech", 7),
    ("media", 6),
    ("open_source", 7),
    ("education", 6),
)
REGIONS = (("NA", 45), ("EMEA", 35), ("APAC", 15), ("LATAM", 5))

# archetype -> (weight, employee median, eng ratio)
ARCHETYPES: dict[str, tuple[float, int, float]] = {
    "market_only": (0.2, 300, 0.35),
    "not_addressable": (0.03, 400, 0.4),
    "stalled": (0.36, 120, 0.4),
    "oss_free": (0.07, 15, 0.8),
    "small_team": (0.09, 60, 0.45),
    "spreading": (0.07, 700, 0.4),
    "blocked": (0.035, 500, 0.4),
    "walltime": (0.035, 350, 0.45),
    "security": (0.035, 1500, 0.3),
    "ai_native": (0.025, 120, 0.55),
    "churner": (0.05, 150, 0.4),
}

LANGUAGES_BY_ARCHETYPE: dict[str, tuple[tuple[str, int], ...]] = {
    "walltime": (("go", 6), ("jvm", 4)),
    "not_addressable": (("scala", 1),),
    "default": (
        ("python", 30),
        ("rust", 22),
        ("node", 20),
        ("cpp", 10),
        ("go", 11),
        ("jvm", 7),
    ),
}


@dataclass(frozen=True, slots=True)
class CompanyPlan:
    """Hidden generator state for one company (never stored in the product tables)."""

    company_id: str
    archetype: str
    propensity: float
    strength: float
    language: str
    has_market_benchmarks: bool


def slugify(name: str) -> str:
    """Lower-case, hyphen-separated slug of a name."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _weighted(rng: random.Random, options: tuple[tuple[str, int], ...]) -> str:
    return rng.choices([o for o, _ in options], weights=[w for _, w in options])[0]


def _unique_name(rng: random.Random, used: set[str]) -> str:
    for attempt in range(1000):
        name = f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)} {rng.choice(SUFFIXES)}"
        if attempt > 50:
            name = f"{name} {attempt}"
        if slugify(name) not in used:
            used.add(slugify(name))
            return name
    raise RuntimeError("could not generate a unique company name")  # pragma: no cover


def _archetype(rng: random.Random) -> str:
    names = list(ARCHETYPES)
    return rng.choices(names, weights=[ARCHETYPES[n][0] for n in names])[0]


def _make_company(rng: random.Random, idx: int, archetype: str, used: set[str]) -> Company:
    name = _unique_name(rng, used)
    slug = slugify(name)
    base = slug.replace("-", "")
    _, median, eng_ratio = ARCHETYPES[archetype]
    employees = max(3, int(rng.lognormvariate(0, 0.8) * median))
    eng = max(1, int(employees * eng_ratio * rng.uniform(0.7, 1.3)))
    aliases: tuple[str, ...] = ()
    if rng.random() < 0.25:
        aliases = (rng.choice((f"get{base}.example", f"{base}-eu.example", f"{base}hq.example")),)
    orgs = (base,)
    if rng.random() < 0.18:
        orgs = (base, f"{base}-{rng.choice(('labs', 'oss', 'platform', 'research'))}")
    industry = "open_source" if archetype == "oss_free" else _weighted(rng, INDUSTRIES)
    return Company(
        company_id=f"c{idx:05d}",
        name=name,
        domain=f"{base}.example",
        alias_domains=aliases,
        github_orgs=orgs,
        industry=industry,
        region=_weighted(rng, REGIONS),
        employee_count=employees,
        eng_headcount=eng,
    )


def _plan(rng: random.Random, company: Company, archetype: str) -> CompanyPlan:
    propensity = rng.betavariate(2.0, 2.0)
    strength = min(1.0, max(0.0, 0.55 * propensity + 0.45 * rng.random()))
    langs = LANGUAGES_BY_ARCHETYPE.get(archetype, LANGUAGES_BY_ARCHETYPE["default"])
    market_p = {"market_only": 0.6, "not_addressable": 1.0}.get(archetype, 0.2)
    return CompanyPlan(
        company_id=company.company_id,
        archetype=archetype,
        propensity=propensity,
        strength=strength,
        language=_weighted(rng, langs),
        has_market_benchmarks=rng.random() < market_p,
    )


def _link_subsidiaries(rng: random.Random, companies: list[Company]) -> list[Company]:
    """Make ~7% of companies subsidiaries of a larger company (one level deep)."""
    by_size = sorted(companies, key=lambda c: (-c.employee_count, c.company_id))
    parents = {c.company_id for c in by_size[: max(1, len(by_size) // 8)]}
    out = []
    parent_pool = sorted(parents)
    for c in companies:
        if c.company_id not in parents and rng.random() < 0.07:
            parent = rng.choice(parent_pool)
            c = Company(**{**_as_dict(c), "parent_id": parent})
        out.append(c)
    return out


def _as_dict(c: Company) -> dict[str, object]:
    return {f: getattr(c, f) for f in Company.__slots__}


def generate_companies(
    rng: random.Random, n_companies: int
) -> tuple[list[Company], dict[str, CompanyPlan]]:
    """Generate ``n_companies`` companies and their hidden plans."""
    used: set[str] = set()
    companies: list[Company] = []
    plans: dict[str, CompanyPlan] = {}
    for idx in range(1, n_companies + 1):
        archetype = _archetype(rng)
        company = _make_company(rng, idx, archetype, used)
        companies.append(company)
        plans[company.company_id] = _plan(rng, company, archetype)
    return _link_subsidiaries(rng, companies), plans
