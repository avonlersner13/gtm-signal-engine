"""Synthetic people: personas, invented names, a corporate/alias/freemail mix, and bots.

Names are built from syllables so they don't belong to anyone real. Email domains are
either the fake company's .example domains or .example freemail domains.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from signal_engine.generate.companies import CompanyPlan
from signal_engine.models import Company, User

_SYLLABLES_WORDS = (
    "ka lo mi ra ve ta no shi ri an el or is ul ze ba dor fen gal hir jas kel mor nev "
    "pal quin rho sab tor ul vin wes yar zan bri cal dra eth fio gre ila jor lia mae nor "
    "ost pri rue sel tam ula vio wyn"
)
SYLLABLES = tuple(_SYLLABLES_WORDS.split())
FREEMAIL = ("mail.example", "inbox.example", "webmail.example", "post.example")

TITLES: dict[str, tuple[str, ...]] = {
    "ic_engineer": (
        "Software Engineer",
        "Senior Software Engineer",
        "Backend Engineer",
        "Performance Engineer",
        "Systems Developer",
        "Open Source Maintainer",
    ),
    "staff_principal": ("Staff Engineer", "Principal Engineer", "Software Architect"),
    "eng_manager": ("Engineering Manager", "Director of Engineering", "Senior Engineering Manager"),
    "platform_devex": (
        "Platform Engineer",
        "DevEx Lead",
        "Developer Experience Engineer",
        "Infrastructure Engineer",
        "Build & Release Engineer",
    ),
    "vp_cto": ("CTO", "VP of Engineering", "Head of Engineering"),
}
PERSONA_MIX = (
    ("ic_engineer", 50),
    ("staff_principal", 15),
    ("eng_manager", 14),
    ("platform_devex", 13),
    ("vp_cto", 8),
)
CHAMPION_EMAIL_MIX = (("corporate", 70), ("alias", 8), ("vanity_org", 7), ("freemail_org", 15))
EMAIL_MIX = (
    ("corporate", 62),
    ("alias", 8),
    ("vanity_org", 5),
    ("freemail_org", 17),
    ("freemail_commit", 4),
    ("unresolvable", 4),
)
BASE_USERS = {
    "stalled": 1.5,
    "small_team": 3.5,
    "oss_free": 2.5,
    "spreading": 13.0,
    "blocked": 11.0,
    "walltime": 5.0,
    "security": 7.0,
    "ai_native": 5.0,
    "churner": 5.0,
    "market_only": 0.0,
    "not_addressable": 0.0,
}
MIN_USERS = {"spreading": 7, "blocked": 8, "small_team": 2, "security": 3, "ai_native": 2}


@dataclass(frozen=True, slots=True)
class UserPlan:
    """Hidden generator state for one user."""

    user_id: str
    company_id: str | None
    persona: str
    commit_domain: str | None
    is_bot: bool


@dataclass(slots=True)
class UserBundle:
    """Generated users plus the plans the journey builder needs."""

    users: list[User]
    plans: dict[str, UserPlan]
    by_company: dict[str, list[str]]
    independents: list[str]
    bots: dict[str, str]


def _person_name(rng: random.Random) -> tuple[str, str]:
    first = (rng.choice(SYLLABLES) + rng.choice(SYLLABLES)).capitalize()
    last = "".join(rng.choice(SYLLABLES) for _ in range(rng.choice((2, 3)))).capitalize()
    return first, last


def _weighted(rng: random.Random, options: tuple[tuple[str, int], ...]) -> str:
    return rng.choices([o for o, _ in options], weights=[w for _, w in options])[0]


def _company_user_counts(
    rng: random.Random, companies: list[Company], plans: dict[str, CompanyPlan], budget: int
) -> dict[str, int]:
    raw = {}
    for c in companies:
        plan = plans[c.company_id]
        base = BASE_USERS[plan.archetype] * (0.5 + plan.strength) * rng.uniform(0.6, 1.4)
        raw[c.company_id] = base
    total = sum(raw.values()) or 1.0
    scale = budget / total
    counts = {}
    for cid, value in raw.items():
        archetype = plans[cid].archetype
        n = round(value * scale)
        if BASE_USERS[archetype] > 0:
            n = max(n, MIN_USERS.get(archetype, 1))
        counts[cid] = n
    return counts


def _email(
    rng: random.Random, method: str, first: str, last: str, company: Company | None
) -> tuple[str, str | None]:
    """Return (email, commit_domain) for the chosen email method."""
    local = f"{first}.{last}".lower()
    freemail = f"{local}{rng.randint(1, 999)}@{rng.choice(FREEMAIL)}"
    if company is None:
        return freemail, None
    if method == "corporate":
        return f"{local}@{company.domain}", company.domain
    if method == "alias" and company.alias_domains:
        return f"{local}@{company.alias_domains[0]}", company.alias_domains[0]
    if method == "alias":
        return f"{local}@{company.domain}", company.domain
    if method == "vanity_org":
        return f"{local}@{last.lower()}{first.lower()}.example", None
    if method == "freemail_org":
        return freemail, company.domain if rng.random() < 0.5 else None
    if method == "freemail_commit":
        return freemail, company.domain
    return freemail, None


def _make_user(
    rng: random.Random, uid: str, company: Company | None, persona: str, champion: bool = False
) -> tuple[User, UserPlan]:
    first, last = _person_name(rng)
    mix = CHAMPION_EMAIL_MIX if champion else EMAIL_MIX
    method = _weighted(rng, mix) if company else "independent"
    email, commit_domain = _email(rng, method, first, last, company)
    orgs: tuple[str, ...] = ()
    if company and method not in ("freemail_commit", "unresolvable"):
        orgs = (rng.choice(company.github_orgs),)
    login = f"{first.lower()}{last.lower()[:4]}{rng.randint(1, 99)}"
    user = User(uid, email, f"{first} {last}", rng.choice(TITLES[persona]), login, orgs)
    plan = UserPlan(uid, company.company_id if company else None, persona, commit_domain, False)
    return user, plan


def _make_bot(uid: str, company: Company) -> tuple[User, UserPlan]:
    org = company.github_orgs[0]
    login = f"{org}-ci[bot]"
    user = User(uid, f"{org}-ci@noreply.github.example", login, "Bot", login, (org,))
    return user, UserPlan(uid, company.company_id, "ic_engineer", None, True)


def generate_users(
    rng: random.Random, companies: list[Company], plans: dict[str, CompanyPlan], n_users: int
) -> UserBundle:
    """Generate roughly ``n_users`` users across companies, independents and bots."""
    bundle = UserBundle([], {}, {}, [], {})
    active = [c for c in companies if BASE_USERS[plans[c.company_id].archetype] > 0]
    n_bots = sum(1 for _ in active) // 2
    counts = _company_user_counts(rng, companies, plans, int(n_users * 0.86) - n_bots)
    seq = 0

    def add(user: User, plan: UserPlan) -> None:
        bundle.users.append(user)
        bundle.plans[user.user_id] = plan

    for c in companies:
        ids = []
        for i in range(counts[c.company_id]):
            seq += 1
            persona = _champion_persona(rng) if i == 0 else _weighted(rng, PERSONA_MIX)
            user, plan = _make_user(rng, f"u{seq:06d}", c, persona, champion=i == 0)
            add(user, plan)
            ids.append(user.user_id)
        bundle.by_company[c.company_id] = ids
    for c in active[::2]:
        seq += 1
        bot, plan = _make_bot(f"u{seq:06d}", c)
        add(bot, plan)
        bundle.bots[c.company_id] = bot.user_id
    while seq < n_users:
        seq += 1
        user, plan = _make_user(rng, f"u{seq:06d}", None, "ic_engineer")
        add(user, plan)
        bundle.independents.append(user.user_id)
    return bundle


def _champion_persona(rng: random.Random) -> str:
    return "ic_engineer" if rng.random() < 0.6 else "staff_principal"
