# Signal framework

Every weight and threshold below lives in `config/signals.toml` and is a **hypothesis**:
a reasoned guess about what predicts revenue for a product-led performance-testing tool,
not a CodSpeed fact. Internal event names are invented; they mirror the documented
product surface (onboarding, checks, instruments, the Wizard and @codspeedbot, MCP, seats,
runners, Enterprise).

## How scores work

* **Fit (0-100)**: who they are. Engineering headcount band, language mix, private-repo
  ratio, industry, parent-account size, and repos that already benchmark without CodSpeed.
* **Intent (0-100)**: what they do. For each event type,
  `contribution = clamp(weight x decayed_count, cap)` where every occurrence decays with
  a 14-day half-life. Negative weights subtract. Derived signals (seats, runners,
  spread, market, run drop) add fixed points. The raw total is divided by
  `intent.scale` (125) and clamped to 0-100.
* **Priority** = 0.4 x fit + 0.6 x intent + momentum. Momentum is
  0.3 x week-over-week intent delta, capped at +/-10.
* Each rule that fires leaves a **reason** with real numbers, e.g.
  "6 active users, 5-seat cap exceeded, trial ends in 4 days". The top six are kept.
* Every week's score is saved to `scores_history`. That's where deltas and
  "biggest movers" come from.

## Fit signals

| Signal | What it means | Why it predicts revenue | How measured | How to tune |
|---|---|---|---|---|
| Eng headcount | Size of the potential seat base | Seats are the main revenue line ($15/user/month annual) | Sum of `eng_headcount` across the parent account, banded | `[fit.eng_headcount].bands` |
| Language mix | Whether the stack runs CodSpeed's instruments | Simulation covers Python, Rust, C++ and Node; Go/JVM are walltime-only, so they need macro runners (paid minutes) | Languages the scanner found in their repos; best language scores | `[fit.language.points]`, `walltime_only` |
| Private-repo ratio | Code that needs paid seats | Public repos are free and unlimited; private repos drive seats | Share of the account's repos that are private | `[fit.private_repos].max_points` |
| Industry | Sensitivity to performance | Latency and cost matter most in fintech, infra, databases and devtools | Firmographic `industry` | `[fit.industry.points]` |
| Parent size | Room to expand across subsidiaries | Multi-org parents expand beyond the first team | Employees across the parent account (only with subsidiaries) | `[fit.parent_size].bands` |
| Benchmarks without CodSpeed | They already care about performance | Existing benchmarks make setup one PR away | Scanner `benchmarks_without_codspeed` repos | `[fit.market]` |

## Intent signals (events)

| Category | Signals (weight) | What it means / why it predicts revenue |
|---|---|---|
| Acquisition | signed_up (1), dashboard_login (0.3), docs_visit (0.3), demo_requested (10) | Top-of-funnel interest. A demo request is a strong hand-raise. |
| Activation | github_app_installed (4), repo_imported (1.5), setup_started (2), wizard_pr_opened (2), wizard_pr_merged (3), workflow_committed (3), backtest_run (2), baseline_created (6), first_pr_report (6) | Without a baseline on the default branch, PRs get no comparison. The first PR report is the aha moment. |
| Depth | run_completed (0.15, cap 10), cli_auth_login (1), local_upload (0.5), sharding_enabled (4), partial_runs_enabled (3), mongodb_instrument_enabled (4) | Sustained runs, plus features that only large suites need. |
| Value | regression_detected (1), regression_acknowledged (2), improvement_detected (0.5), required_check_enabled (8), threshold_changed (1), flamegraph_viewed (0.5), **informational_check_enabled (-8)** | A required check means CodSpeed now gates merges, which makes it hard to rip out. Switching to informational is the opposite. |
| AI | codspeedbot_mention (1), codspeedbot_fix_pr_opened (2), codspeedbot_fix_pr_merged (4), mcp_connected (5), mcp_tool_call (0.3), skill_installed (2), **wizard_disabled (-6)** | AI-native teams lean on @codspeedbot and the MCP server. They make good case studies and expansion bets. |
| Hygiene / risk | **benchmark_ignored (-1.5), benchmark_archived (-0.5), seat_removed (-4), repo_deleted (-3)** | Noise, flaky benchmarks and shrinking teams come before churn. |
| Monetization | pr_authored_private (0.2), report_viewed (0.2), free_limit_exceeded (6), trial_started (6), trial_ending (2), user_blocked_no_seat (3), seat_added (2), plan_upgraded (10), macro_runner_minutes (0.1), ryzen_requested (6), runner_budget_set (4) | These map straight onto the billing rules: active users, the 5-user cap, the 14-day trial, blocked users and runner minutes. |
| Enterprise | pricing_page_view (2), enterprise_page_view (4), sso_page_view (5), trust_center_visit (5), soc2_report_requested (8), security_docs_visit (3) | A security review is under way, so an Enterprise deal is likely (SSO/SAML, SOC 2 Type II, on-prem). |

## Derived signals

| Signal | Rule | Points | Reason example |
|---|---|---|---|
| over_cap | active users > 5 (rolling 30 days) | 14 | "6 active users, 5-seat cap exceeded, trial ends in 4 days" |
| trial_active | trial running, not paid | 6 | (folded into over_cap) |
| near_cap | 4-5 active users and rising week over week | 6 | "4/5 free seats used, up from 3 a week ago" |
| blocked_users | users blocked without a seat (auto-allocation off) | 5 each, cap 15 | "2 engineers blocked without a seat (auto-allocation off)" |
| runner_overage | projected Graviton minutes this month > 600 free | 10 | "projected 912 Graviton minutes this month vs 600 free" |
| multi_org | 2+ GitHub orgs/teams active under one parent | 4 each, cap 8 | "2 GitHub orgs active under one parent account" |
| engaged_users | users meeting the engagement criteria | 2 each, cap 8 | "5 engaged engineers" |
| market_benchmarks | repos benchmarking without CodSpeed | 3 each, cap 9 | "2 repos benchmark with criterion but not CodSpeed" |
| run_drop | runs down > 50% vs prior 30 days (prior >= 10) | -8 | "runs down 75% vs the prior 30 days (10 vs 40)" |

## Seats (billing rules)

An active user is a non-bot user who, in a rolling 30-day window, authored a PR on a
**private, CodSpeed-enabled** repo or opened the detailed performance report. Public-repo
activity doesn't count. More than 5 active users starts a 14-day Pro trial. When no
explicit `trial_started` event exists, the trial start is inferred from the day the
rolling count first went above 5. With automatic seat allocation off, new users get
`user_blocked_no_seat` until an admin assigns a seat (`seat_added`).

## Market signals (scanner)

The scanner reads manifests and CI files and classifies each repo as `codspeed`,
`benchmarks_without_codspeed`, `not_addressable` (sbt-jmh only) or `no_benchmarks`. It
also tags Go/JVM repos as `walltime_only`. See `scanner.py` for every rule, including
the Cargo `package =` rename trap and Go being detectable only in CI.

## How to tune

1. Start from closed-won and closed-lost data, not from these numbers.
2. Change one weight at a time in `signals.toml` and re-run
   `uv run signal-engine evaluate` to watch precision@k and the per-play hit rate.
3. Keep caps. They stop one noisy signal (thousands of runs) from swamping the rest.
4. Keep negative signals negative. The config loader rejects a weight and cap with
   different signs.
5. When a weight is stable, write down why next to it in the TOML.
