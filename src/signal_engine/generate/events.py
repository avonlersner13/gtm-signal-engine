"""Synthetic product events: believable journeys, not random noise.

Each company archetype gets a scripted journey of "structural" events (onboarding,
cap crossing, trials, blocked users, security review, churn signs...). The remaining
event budget is spread over users' active periods as background usage, with a skewed
(Pareto) activity multiplier per company, weekday seasonality and a summer dip.

All event names are hypotheses that mirror CodSpeed's documented product surface.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from signal_engine.generate.companies import CompanyPlan
from signal_engine.generate.repos import RepoBundle
from signal_engine.generate.users import UserBundle
from signal_engine.models import Company, Event, Repo

TIMELINE_DAYS = 300
MCP_TOOLS = (
    "list_repositories",
    "list_runs",
    "get_run",
    "compare_runs",
    "get_benchmark_result",
    "query_flamegraph",
    "list_threads",
)
BOT_INTENTS = ("impact", "explain", "fix", "add_benchmark")
IGNORE_REASONS = (
    "flaky on shared runners",
    "too noisy",
    "not relevant anymore",
    "measures setup cost",
)
BENCH_NAMES = ("parse_large", "encode_batch", "query_plan", "render_page", "hash_keys", "sort_rows")
ENTERPRISE_EVENTS = (
    "trust_center_visit",
    "soc2_report_requested",
    "sso_page_view",
    "security_docs_visit",
    "enterprise_page_view",
    "pricing_page_view",
)
CHURN_EVENTS = (
    "benchmark_ignored",
    "informational_check_enabled",
    "wizard_disabled",
    "seat_removed",
    "repo_deleted",
    "benchmark_archived",
)

PROFILES: dict[str, tuple[tuple[str, float], ...]] = {
    "standard": (
        ("run_completed", 34),
        ("pr_authored_private", 12),
        ("report_viewed", 10),
        ("dashboard_login", 9),
        ("regression_detected", 5),
        ("improvement_detected", 4),
        ("flamegraph_viewed", 5),
        ("regression_acknowledged", 2),
        ("docs_visit", 4),
        ("codspeedbot_mention", 2),
        ("threshold_changed", 0.4),
        ("cli_auth_login", 1),
        ("local_upload", 2),
        ("pricing_page_view", 0.4),
        ("benchmark_ignored", 0.05),
        ("benchmark_archived", 0.2),
    ),
    "oss": (
        ("run_completed", 40),
        ("report_viewed", 10),
        ("dashboard_login", 10),
        ("regression_detected", 5),
        ("improvement_detected", 4),
        ("flamegraph_viewed", 4),
        ("docs_visit", 5),
        ("codspeedbot_mention", 1),
        ("benchmark_archived", 0.3),
    ),
}
PROFILES["walltime"] = (*PROFILES["standard"], ("macro_runner_minutes", 7.0))
PROFILES["ai"] = (
    *PROFILES["standard"],
    ("mcp_tool_call", 9.0),
    ("codspeedbot_mention", 4.0),
    ("codspeedbot_fix_pr_opened", 0.8),
    ("codspeedbot_fix_pr_merged", 0.5),
)
ACTIVATION_REACH = {"signup": 0, "install": 1, "setup": 2, "activated": 3}


@dataclass(slots=True)
class Period:
    """A stretch of time during which a user generates background usage."""

    user_id: str
    start: float
    end: float
    rate: float
    repos: tuple[str, ...]
    profile: str


@dataclass(slots=True)
class Ctx:
    """Shared state while building journeys."""

    rng: random.Random
    horizon: float
    month_start: float
    users: UserBundle
    repos: RepoBundle
    repo_by_id: dict[str, Repo]
    rows: list[tuple[float, str, str, str | None, dict[str, Any]]] = field(default_factory=list)
    periods: list[Period] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    upgraded: list[str] = field(default_factory=list)

    def emit(
        self, day: float, user: str, etype: str, repo: str | None = None, **props: Any
    ) -> None:
        """Record an event if it falls inside the timeline."""
        if 0.0 <= day <= self.horizon:
            self.rows.append((day, user, etype, repo, props))

    def pr(self, day: float, user: str, repo: str) -> None:
        """Emit a private-repo PR with the commit-email domain when the user has one."""
        domain = self.users.plans[user].commit_domain
        props = {"commit_email_domain": domain} if domain else {}
        self.emit(day, user, "pr_authored_private", repo, **props)


def _onboard(ctx: Ctx, champ: str, repos: list[str], t0: float, reach: str, target: str) -> float:
    """Emit onboarding events up to ``reach``; return the activation day (or -1)."""
    rng = ctx.rng
    level = ACTIVATION_REACH[reach]
    ctx.emit(t0, champ, "signed_up")
    ctx.emit(t0 + 0.01, champ, "dashboard_login")
    if rng.random() < 0.6:
        ctx.emit(t0 + 0.02, champ, "docs_visit")
    if level < 1 or not repos:
        return -1.0
    t1 = t0 + rng.expovariate(1 / 2.0)
    ctx.emit(t1, champ, "github_app_installed", target=target, repos_selected=len(repos))
    for i, repo in enumerate(repos):
        ctx.emit(t1 + 0.01 * (i + 1), champ, "repo_imported", repo)
    if level < 2:
        return -1.0
    t2 = t1 + rng.expovariate(1 / 1.5)
    path = "wizard" if rng.random() < 0.6 else "manual"
    ctx.emit(t2, champ, "setup_started", repos[0], path=path)
    if path == "wizard":
        ctx.emit(t2 + 0.05, champ, "wizard_pr_opened", repos[0])
    if level < 3:
        return -1.0
    t3 = t2 + rng.expovariate(1 / 1.5)
    merge = "wizard_pr_merged" if path == "wizard" else "workflow_committed"
    ctx.emit(t3, champ, merge, repos[0])
    if rng.random() < 0.5:
        ctx.emit(t3 + 0.02, champ, "backtest_run", repos[0])
    ctx.emit(t3 + 0.05, champ, "baseline_created", repos[0])
    ctx.emit(t3 + 0.06, champ, "run_completed", repos[0], **_run_props(rng, "standard", "default"))
    t4 = t3 + rng.expovariate(1 / 2.0) + 0.1
    ctx.emit(t4, champ, "first_pr_report", repos[0])
    return t4


def _run_props(rng: random.Random, profile: str, branch: str | None = None) -> dict[str, Any]:
    if profile == "walltime":
        instrument = "walltime" if rng.random() < 0.9 else "simulation"
    else:
        instrument = "memory" if rng.random() < 0.15 else "simulation"
    branch = branch or ("default" if rng.random() < 0.3 else "pr")
    return {"instrument": instrument, "branch_type": branch, "benchmarks": rng.randint(5, 400)}


def _join_users(ctx: Ctx, users: list[str], start: float, end: float) -> list[float]:
    """Sign up users at sorted random days in [start, end]; champion first at start."""
    days = [start] + sorted(ctx.rng.uniform(start, max(start, end)) for _ in users[1:])
    for uid, day in zip(users[1:], days[1:], strict=True):
        ctx.emit(day, uid, "signed_up")
        ctx.emit(day + 0.02, uid, "dashboard_login")
    return days


def _activate_users(
    ctx: Ctx, users: list[str], joins: list[float], repos: list[str], rate: float, profile: str
) -> None:
    """Give each user an active period and a steady trickle of private PRs."""
    private = [r for r in repos if ctx.repo_by_id[r].is_private]
    for uid, join in zip(users, joins, strict=True):
        ctx.periods.append(Period(uid, join, ctx.horizon, rate, tuple(repos), profile))
        if not private:
            continue
        day = join + 0.1
        while day <= ctx.horizon:
            ctx.pr(day, uid, ctx.rng.choice(private))
            day += ctx.rng.expovariate(1 / 5.0) + 0.5


def _extras(ctx: Ctx, plan: CompanyPlan, users: list[str], repo: str, t_act: float) -> None:
    """Optional depth/value milestones whose odds scale with signal strength."""
    rng, s = ctx.rng, plan.strength
    for etype, p in (
        ("required_check_enabled", 0.55 * s),
        ("sharding_enabled", 0.15 * s),
        ("partial_runs_enabled", 0.1 * s),
        ("mongodb_instrument_enabled", 0.05),
        ("demo_requested", 0.08 * s),
    ):
        if rng.random() < p:
            ctx.emit(rng.uniform(t_act, ctx.horizon), rng.choice(users), etype, repo)


def _team(ctx: Ctx, company: Company, cap: int | None = None) -> tuple[list[str], list[str]]:
    users = ctx.users.by_company.get(company.company_id, [])
    repos = ctx.repos.enabled.get(company.company_id, [])
    return (users[:cap] if cap else users), repos


def _stalled(ctx: Ctx, company: Company, plan: CompanyPlan) -> None:
    users, repos = _team(ctx, company)
    rng = ctx.rng
    t0 = rng.uniform(5, ctx.horizon - 1)
    reach = rng.choices(("signup", "install", "setup"), weights=(15, 60, 25))[0]
    _onboard(ctx, users[0], repos, t0, reach, "org")
    _join_users(ctx, users, t0, min(ctx.horizon, t0 + 20))


def _steady_team(
    ctx: Ctx, company: Company, plan: CompanyPlan, profile: str, max_active: int, rate: float
) -> tuple[list[str], float, list[str]]:
    """Onboard, activate and run a small team (<= max_active active users)."""
    users, repos = _team(ctx, company)
    rng = ctx.rng
    t0 = rng.uniform(0, ctx.horizon - 45)
    t_act = _onboard(ctx, users[0], repos, t0, "activated", "org")
    joins = _join_users(ctx, users, t0, min(ctx.horizon - 5, t_act + 60))
    active = users[:max_active]
    _activate_users(ctx, active, [max(j, t_act) for j in joins[:max_active]], repos, rate, profile)
    _extras(ctx, plan, active, repos[0], t_act)
    return active, t_act, repos


def _oss_free(ctx: Ctx, company: Company, plan: CompanyPlan) -> None:
    _steady_team(ctx, company, plan, "oss", ctx.rng.choice((1, 1, 2, 3)), 0.35)


def _small_team(ctx: Ctx, company: Company, plan: CompanyPlan) -> None:
    _steady_team(ctx, company, plan, "standard", ctx.rng.choice((1, 2, 2, 3, 4)), 0.6)


def _cross_schedule(rng: random.Random, horizon: float, strength: float) -> tuple[str, float]:
    phase = rng.choices(
        ("ending", "active", "upgraded", "approaching"),
        weights=(30 + 20 * strength, 25, 25, 20),
    )[0]
    cross = {
        "ending": horizon - rng.uniform(9.2, 13.8),
        "active": horizon - rng.uniform(1.0, 8.0),
        "upgraded": horizon - rng.uniform(20, 80),
        "approaching": horizon,
    }[phase]
    return phase, cross


def _spreading(ctx: Ctx, company: Company, plan: CompanyPlan) -> None:
    rng = ctx.rng
    users, repos = _team(ctx, company)
    phase, cross = _cross_schedule(rng, ctx.horizon, plan.strength)
    t_act = cross - rng.uniform(40, 110)
    t0 = t_act - rng.uniform(2, 15)
    _onboard(ctx, users[0], repos, t0, "activated", "org")
    if phase == "approaching":
        n_active = rng.choice((4, 4, 5))
        last = ctx.horizon - rng.uniform(0.5, 6.0)
        joins = [t_act] + sorted(rng.uniform(t_act, last - 8) for _ in range(n_active - 2)) + [last]
    else:
        n_active = len(users) if phase == "upgraded" else min(len(users), 6 + rng.randint(1, 8))
        early = sorted(rng.uniform(t_act, cross - 1) for _ in range(4))
        late = sorted(rng.uniform(cross + 0.5, ctx.horizon) for _ in range(n_active - 6))
        joins = [t_act, *early, cross, *late]
    for uid, day in zip(users[1:n_active], joins[1:], strict=True):
        ctx.emit(day - 0.05, uid, "signed_up")
    _join_users(ctx, [users[0], *users[n_active:]], t0, ctx.horizon)
    _activate_users(ctx, users[:n_active], joins, repos, 1.2, "standard")
    _extras(ctx, plan, users[:n_active], repos[0], t_act)
    if phase != "approaching":
        _trial(ctx, users[0], cross, phase == "upgraded")
    if phase == "upgraded":
        ctx.upgraded.append(company.company_id)


def _trial(ctx: Ctx, admin: str, cross: float, upgrade: bool) -> None:
    ctx.emit(cross + 0.02, admin, "free_limit_exceeded")
    ctx.emit(cross + 0.03, admin, "trial_started")
    if ctx.rng.random() < 0.6:
        ctx.emit(cross + ctx.rng.uniform(-3, 1), admin, "pricing_page_view")
    for offset, left in ((7, 7), (11, 3), (13, 1)):
        ctx.emit(cross + offset, admin, "trial_ending", days_left=left)
    if upgrade:
        billing = "annual" if ctx.rng.random() < 0.6 else "monthly"
        ctx.emit(
            cross + ctx.rng.uniform(5, 13), admin, "plan_upgraded", plan="pro", billing=billing
        )


def _admin(ctx: Ctx, users: list[str]) -> str:
    for uid in users:
        if ctx.users.plans[uid].persona in ("eng_manager", "vp_cto", "platform_devex"):
            return uid
    return users[0]


def _blocked(ctx: Ctx, company: Company, plan: CompanyPlan) -> None:
    rng = ctx.rng
    users, repos = _team(ctx, company)
    cross = ctx.horizon - rng.uniform(5, 40)
    t_act = cross - rng.uniform(30, 90)
    t0 = t_act - rng.uniform(2, 10)
    _onboard(ctx, users[0], repos, t0, "activated", "org")
    admin = _admin(ctx, users[:6])
    ctx.emit(t_act + rng.uniform(1, 10), admin, "auto_seat_allocation_toggled", enabled=False)
    joins = [t_act, *sorted(rng.uniform(t_act, cross - 1) for _ in range(4)), cross]
    for uid, day in zip(users[1:6], joins[1:], strict=True):
        ctx.emit(day - 0.05, uid, "signed_up")
    _activate_users(ctx, users[:6], joins, repos, 1.1, "standard")
    _extras(ctx, plan, users[:6], repos[0], t_act)
    _trial(ctx, admin, cross, cross < ctx.horizon - 14 and rng.random() < 0.5)
    n_blocked = rng.randint(1, 4)
    for uid in users[6 : 6 + n_blocked]:
        _blocked_user(ctx, uid, admin, repos)
    _join_users(ctx, [users[0], *users[6 + n_blocked :]], t0, ctx.horizon)


def _blocked_user(ctx: Ctx, uid: str, admin: str, repos: list[str]) -> None:
    rng = ctx.rng
    join = ctx.horizon - rng.uniform(0.5, 12)
    ctx.emit(join, uid, "signed_up")
    ctx.emit(join + 0.05, uid, "user_blocked_no_seat", repos[0])
    if rng.random() < 0.4:
        ctx.emit(join + rng.uniform(1, 4), uid, "user_blocked_no_seat", repos[0])
    if rng.random() < 0.3:
        seated = join + rng.uniform(0.5, 5)
        ctx.emit(seated, admin, "seat_added", target_user=uid)
        _activate_users(ctx, [uid], [seated + 0.1], repos, 1.0, "standard")


def _walltime(ctx: Ctx, company: Company, plan: CompanyPlan) -> None:
    rng = ctx.rng
    active, _, repos = _steady_team(ctx, company, plan, "walltime", rng.choice((2, 3, 4, 5)), 1.0)
    mtd_target = rng.uniform(80, 520) * (0.5 + plan.strength)
    jobs = rng.randint(6, 14)
    for _ in range(jobs):
        day = rng.uniform(ctx.month_start, ctx.horizon)
        minutes = round(mtd_target / jobs * rng.uniform(0.6, 1.4), 1)
        ctx.emit(
            day,
            rng.choice(active),
            "macro_runner_minutes",
            repos[0],
            runner="graviton",
            minutes=minutes,
        )
    if rng.random() < 0.35 * (0.5 + plan.strength):
        ctx.emit(ctx.horizon - rng.uniform(1, 25), active[0], "ryzen_requested")
    if rng.random() < 0.3:
        ctx.emit(
            ctx.horizon - rng.uniform(1, 40),
            _admin(ctx, active),
            "runner_budget_set",
            budget_usd=200,
        )


def _security(ctx: Ctx, company: Company, plan: CompanyPlan) -> None:
    rng = ctx.rng
    active, _, _ = _steady_team(ctx, company, plan, "standard", rng.choice((3, 4, 5)), 1.0)
    people = ctx.users.by_company[company.company_id]
    recent = rng.random() < 0.4 + 0.6 * plan.strength
    lo, hi = (0.5, 13.5) if recent else (30, 80)
    for etype in rng.sample(ENTERPRISE_EVENTS, rng.randint(2, 5)):
        ctx.emit(
            ctx.horizon - rng.uniform(lo, hi),
            _admin(ctx, people) if rng.random() < 0.5 else rng.choice(active),
            etype,
        )


def _ai_native(ctx: Ctx, company: Company, plan: CompanyPlan) -> None:
    rng = ctx.rng
    active, t_act, repos = _steady_team(ctx, company, plan, "ai", rng.choice((2, 3, 4)), 1.1)
    ctx.emit(t_act + rng.uniform(3, 40), active[0], "mcp_connected")
    if rng.random() < 0.5:
        ctx.emit(
            t_act + rng.uniform(3, 40), active[-1], "skill_installed", skill="codspeed-optimize"
        )
    for _ in range(rng.randint(0, 4) if rng.random() < plan.strength + 0.2 else 0):
        day = ctx.horizon - rng.uniform(1, 60)
        ctx.emit(day, active[0], "codspeedbot_mention", repos[0], intent="fix")
        ctx.emit(day + 0.1, active[0], "codspeedbot_fix_pr_opened", repos[0])
        ctx.emit(day + rng.uniform(0.3, 3), active[0], "codspeedbot_fix_pr_merged", repos[0])


def _churner(ctx: Ctx, company: Company, plan: CompanyPlan) -> None:
    rng = ctx.rng
    users, repos = _team(ctx, company)
    t0 = rng.uniform(0, ctx.horizon - 160)
    t_act = _onboard(ctx, users[0], repos, t0, "activated", "org")
    active = users[: rng.choice((2, 3, 4))]
    joins = _join_users(ctx, active, t0, t_act + 30)
    end = ctx.horizon - rng.uniform(35, 80)
    for uid, join in zip(active, joins, strict=True):
        ctx.periods.append(Period(uid, max(join, t_act), end, 1.0, tuple(repos), "standard"))
        ctx.periods.append(Period(uid, end, ctx.horizon, 0.08, tuple(repos), "standard"))
    if rng.random() < 0.5:
        ctx.emit(t_act + rng.uniform(5, 30), active[0], "required_check_enabled", repos[0])
    for etype in rng.sample(CHURN_EVENTS, rng.randint(2, 4)):
        for _ in range(rng.randint(2, 4) if etype == "benchmark_ignored" else 1):
            props = {"reason": rng.choice(IGNORE_REASONS)} if etype == "benchmark_ignored" else {}
            ctx.emit(
                ctx.horizon - rng.uniform(1, 30), _admin(ctx, active), etype, repos[0], **props
            )


JOURNEYS = {
    "stalled": _stalled,
    "oss_free": _oss_free,
    "small_team": _small_team,
    "spreading": _spreading,
    "blocked": _blocked,
    "walltime": _walltime,
    "security": _security,
    "ai_native": _ai_native,
    "churner": _churner,
}


def _independents(ctx: Ctx) -> None:
    rng = ctx.rng
    for uid in ctx.users.independents:
        t0 = rng.uniform(0, ctx.horizon)
        repo = ctx.repos.personal_enabled.get(uid)
        if repo:
            t_act = _onboard(ctx, uid, [repo], min(t0, ctx.horizon - 20), "activated", "personal")
            ctx.periods.append(Period(uid, t_act, ctx.horizon, 0.25, (repo,), "oss"))
            continue
        ctx.emit(t0, uid, "signed_up")
        if rng.random() < 0.4:
            ctx.emit(t0 + 0.1, uid, "docs_visit")


def _bots(ctx: Ctx) -> None:
    for cid, bot in ctx.users.bots.items():
        repos = [r for r in ctx.repos.enabled.get(cid, []) if ctx.repo_by_id[r].is_private]
        if not repos:
            continue
        day = ctx.rng.uniform(ctx.horizon - 120, ctx.horizon - 30)
        while day <= ctx.horizon:
            ctx.emit(day, bot, "pr_authored_private", repos[0])
            ctx.emit(
                day + 0.01, bot, "run_completed", repos[0], **_run_props(ctx.rng, "standard", "pr")
            )
            day += ctx.rng.uniform(3, 9)


def _background_event(
    ctx: Ctx, period: Period, day: float
) -> tuple[str, str | None, dict[str, Any]]:
    rng = ctx.rng
    options = PROFILES[period.profile]
    etype = rng.choices([o for o, _ in options], weights=[w for _, w in options])[0]
    repo = rng.choice(period.repos) if period.repos else None
    props: dict[str, Any] = {}
    if etype == "pr_authored_private" and (repo is None or not ctx.repo_by_id[repo].is_private):
        etype = "run_completed"
    if etype == "run_completed":
        props = _run_props(rng, period.profile)
    elif etype == "regression_detected":
        props = {"impact_pct": round(rng.uniform(10, 60), 1), "benchmark": rng.choice(BENCH_NAMES)}
    elif etype == "macro_runner_minutes":
        runner = "ryzen" if rng.random() < 0.05 else "graviton"
        props = {"runner": runner, "minutes": round(rng.uniform(4, 30), 1)}
    elif etype == "mcp_tool_call":
        props = {"tool": rng.choice(MCP_TOOLS)}
    elif etype == "codspeedbot_mention":
        props = {"intent": rng.choice(BOT_INTENTS)}
    elif etype == "benchmark_ignored":
        props = {"reason": rng.choice(IGNORE_REASONS)}
    elif etype in ("dashboard_login", "docs_visit", "pricing_page_view"):
        repo = None
    return etype, repo, props


def _seasonal_day(rng: random.Random, start: float, end: float, origin_weekday: int) -> float:
    day = rng.uniform(start, end)
    for _ in range(3):
        weekday = (origin_weekday + int(day)) % 7
        if weekday < 5 or rng.random() > 0.75:
            break
        day = rng.uniform(start, end)
    return day


def _allocate(periods: list[Period], weights: dict[str, float], budget: int) -> list[int]:
    raw = [p.rate * max(0.0, p.end - p.start) * weights.get(p.user_id, 1.0) for p in periods]
    total = sum(raw) or 1.0
    ideal = [budget * r / total for r in raw]
    counts = [int(x) for x in ideal]
    left = budget - sum(counts)
    order = sorted(range(len(ideal)), key=lambda i: (-(ideal[i] - counts[i]), i))
    for i in order[:left]:
        counts[i] += 1
    return counts


def _background(ctx: Ctx, budget: int, origin_weekday: int, summer: tuple[float, float]) -> None:
    counts = _allocate(ctx.periods, ctx.weights, budget)
    for period, n in zip(ctx.periods, counts, strict=True):
        for _ in range(n):
            day = _seasonal_day(ctx.rng, period.start, period.end, origin_weekday)
            if summer[0] <= day < summer[1] and ctx.rng.random() < 0.25:
                day = _seasonal_day(ctx.rng, period.start, period.end, origin_weekday)
            etype, repo, props = _background_event(ctx, period, day)
            if etype == "pr_authored_private" and repo:
                ctx.pr(day, period.user_id, repo)
            else:
                ctx.emit(day, period.user_id, etype, repo, **props)


def generate_events(
    rng: random.Random,
    companies: list[Company],
    plans: dict[str, CompanyPlan],
    users: UserBundle,
    repos: RepoBundle,
    n_events: int,
    now: datetime,
) -> tuple[list[Event], list[str]]:
    """Generate at least ``n_events`` events; return them plus upgraded company ids."""
    now_utc = now.astimezone(UTC).replace(microsecond=0)
    origin = now_utc - timedelta(days=TIMELINE_DAYS)
    month_start = datetime(now_utc.year, now_utc.month, 1, tzinfo=UTC)
    ctx = Ctx(
        rng=rng,
        horizon=float(TIMELINE_DAYS),
        month_start=(month_start - origin) / timedelta(days=1),
        users=users,
        repos=repos,
        repo_by_id={r.repo_id: r for r in repos.repos},
    )
    for c in companies:
        plan = plans[c.company_id]
        journey = JOURNEYS.get(plan.archetype)
        if journey and users.by_company.get(c.company_id):
            journey(ctx, c, plan)
            multiplier = min(6.0, rng.paretovariate(2.0))
            for uid in users.by_company[c.company_id]:
                ctx.weights[uid] = multiplier
    _independents(ctx)
    _bots(ctx)
    summer = (
        (datetime(2026, 8, 1, tzinfo=UTC) - origin) / timedelta(days=1),
        (datetime(2026, 9, 1, tzinfo=UTC) - origin) / timedelta(days=1),
    )
    _background(ctx, max(0, n_events - len(ctx.rows)), origin.weekday(), summer)
    return _to_events(ctx.rows, origin), ctx.upgraded


def _to_events(
    rows: list[tuple[float, str, str, str | None, dict[str, Any]]], origin: datetime
) -> list[Event]:
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3] or ""))
    out = []
    for i, (day, user, etype, repo, props) in enumerate(rows, start=1):
        ts = origin + timedelta(seconds=round(day * 86400))
        out.append(Event(i, user, ts, etype, repo, props))
    return out
