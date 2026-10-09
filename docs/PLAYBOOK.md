# Playbook

Each account gets one primary play (highest configured priority among the triggers that
fire) and up to two secondary plays. Triggers live in `plays.py`; thresholds, owners,
SLAs and message templates live in `config/plays.toml`. Templates are filled with the
account's real numbers. Plays at priority 70 or above also produce a Slack-style alert in
`out/alerts.jsonl`. These are never sent.

Owners: **founder** (Arthur), **GTM engineer**, or **automated** (no human touch).

| # | Play | Trigger | Who acts (SLA) | What to say | Success metric |
|---|---|---|---|---|---|
| 1 | Trial ending | Pro trial active with <= 5 days left | Founder (24h) | Founder calls the champion: "N engineers are using it and the trial ends in X days. 15 minutes so nothing gets blocked?" | Plan upgraded before the trial ends |
| 2 | Blocked users | >= 2 users blocked without a seat | Founder (8h, same day) | To the admin / economic buyer: "your engineers are blocked from runs on private repos". Offer to assign seats or turn on auto-allocation | Seats assigned or auto-allocation on within 48h |
| 3 | Security review | Trust Center, SOC 2 report, SSO page or security docs within 14 days | GTM engineer (24h), then founder | Send the SOC 2 Type II packet (trust.codspeed.io) and the DPA, and offer SSO/SAML and on-prem options | Security review closed; Enterprise conversation opened |
| 4 | Runner overage | Projected Graviton minutes > 600 this month, or Ryzen requested, or a runner budget set (30 days) | GTM engineer (48h) | Pro/Enterprise runner conversation: budgets, Ryzen for steadier walltime, volume discounts | Runner budget agreed or Enterprise discount quoted |
| 5 | Multi-team spread | >= 2 GitHub orgs/teams active under one parent account | GTM engineer (72h): map the account, then founder | "CodSpeed is running in N orgs. Consolidate seats and settings under one plan?" | One parent-level agreement |
| 6 | Cap approaching | 4-5 of 5 free seats used and rising week over week | GTM engineer (72h) | Champion enablement kit: ROI one-pager, rollout guide, pricing ($15/user/month annual) | Kit shared internally; trial starts with a budget owner aware |
| 7 | AI-native power user | MCP connected and >= 2 @codspeedbot fix PRs merged (90 days) | Founder (5 days) | Ask what's working; case study; offer an AI-workflow review | Case study agreed or expansion to another team |
| 8 | Walltime-only stack | Go/JVM repo, account activated, walltime runs in 30 days | GTM engineer (5 days) | Macro-runner value story: walltime is their only mode, and stable runners make it trustworthy | Runner minutes grow; budget set |
| 9 | Churn risk | >= 3 benchmarks ignored, informational check enabled, Wizard disabled, seats removed (30 days), or runs down > 50% | GTM engineer (72h) | Health check: "is something noisy or in your way?" Help tune thresholds and fix flaky benchmarks | Run volume recovers; informational check reverted |
| 10 | Activation rescue | Installed >= 7 days ago, no baseline | Automated (24h) | Nudge to click **Start AI Setup**: the Wizard finds or writes benchmarks, adds the workflow and opens the PR | `baseline_created` within 7 days |
| 11 | Outbound | No product users; repos benchmark without CodSpeed; fit >= 45 | GTM engineer (1 week) | Personalized sequence that names their framework: "you already benchmark with criterion; CodSpeed runs those in CI on every PR" | Reply; signup from the target org |
| 12 | Not addressable yet | Only sbt-jmh (Scala) or another unsupported stack | Automated | No outreach. Log it as product feedback | Counted in the product-feedback insight |

## Founder capacity

The founder can do about 8 touches a week (`founder_weekly_capacity` in `plays.toml`).
After plays are matched, accounts with a founder-owned primary play are ranked by play
priority, then account priority. The top N keep the founder as owner. Every founder-owned
play on the remaining accounts moves to the GTM engineer with the note
**"over founder capacity"**. The note shows in the briefs, in the alerts, and in the
`play_owner_note` column of `crm_accounts.csv`. The brief headline shows both numbers,
e.g. "8 founder touches this week; 21 queued for GTM engineer".

Thresholds are tuned so founder-eligible accounts (founder-owned primary play before
capacity is applied) stay at about 5-10% of active (signed-up) accounts. Blocked users
needs >= 2 blocked users, and AI-native needs >= 2 merged fix PRs. Trial ending stays at
<= 5 days left.

## Revenue estimates in every play

`revenue.py` shows its assumptions next to every number:

* **Seats**: projected active users x $15 x 12 (annual billing), with the monthly-billing
  alternative ($20) shown alongside. Projection = active now + 30-day growth + blocked
  users. It's $0 while the projection stays within the 5-user Free plan.
* **Runners**: projected Graviton overage minutes x $0.032 x 12. When Ryzen was requested,
  25% of minutes are assumed to move to Ryzen at $0.06.
* **Enterprise**: flagged ("custom") on security-review or SSO signals, never valued.

## Buying committee

* **Champion**: the most engaged IC or staff engineer, weighted 2x toward value events
  (regressions, acknowledgements, required check, flame graphs, @codspeedbot fixes).
* **Technical evaluator**: the platform / DevEx persona with the most activity.
* **Economic buyer**: a VP/CTO if seen, else an engineering manager, else
  "unknown: ask the champion".

Personas come from job titles via the ordered keyword lists in `signals.toml`. Matching
uses word boundaries, so "Director" never matches "CTO".
