# Architecture

gtm-signal-engine is a batch pipeline. Every Monday it reads product events and market
signals, resolves who they belong to, scores each account, picks a play, and writes the
briefs a founder and a GTM engineer act on. Everything runs on the standard library and
SQLite. A fixed seed and a fixed "now" make every run reproducible.

> Independent demo. Event names, weights and thresholds are hypotheses, not CodSpeed's.

## Data flow

```mermaid
flowchart LR
    subgraph Sources
        GEN[generate/<br/>synthetic companies, users,<br/>repos, manifests, events]
        IMP[ingest/importers<br/>JSONL / CSV]
        GH[ingest/github_live<br/>optional, gh CLI, local/ only]
    end
    GEN --> DB[(SQLite<br/>store.py)]
    IMP --> DB
    GH -. JSON in local/ .-> LOCAL[(local/)]
    DB --> ID["identity.py<br/>user → company → account"]
    DB --> SC[scanner.py<br/>manifests + CI]
    ID --> SE[seats.py<br/>active users, trials, blocks]
    ID --> FE[features.py<br/>7/14/30/90d windows, decay]
    SE --> LC[lifecycle.py<br/>stage machine]
    FE --> LC
    SC --> SCO[scoring.py<br/>fit + intent + momentum]
    SE --> SCO
    FE --> SCO
    LC --> SCO
    SCO --> PL["plays.py<br/>trigger → play"]
    FE --> BC[buying_committee.py]
    SE --> RV[revenue.py]
    BC --> PL
    RV --> PL
    PL --> OUT[outputs/<br/>founder brief, account briefs,<br/>CRM CSVs, alerts, funnel]
    PL --> HIST[(scores_history<br/>stage_history<br/>signals)]
    HIST --> SCO
    OUT --> EV[evaluate.py<br/>vs hidden ground truth]
```

`pipeline.compute_week()` is pure: an in-memory `Dataset` goes in and a `WeekResult`
comes out, so benchmarks and tests never touch disk. `pipeline.run_week()` adds
persistence (scores, stages and signals keyed by week) and writes the output files.
Last week's stored scores drive week-over-week deltas, momentum and "biggest movers".
Last week's stored stages decide which PQAs are new.

## Lifecycle state machine

Each stage has entry criteria (thresholds in `config/signals.toml`) and a prerequisite.
An account enters a stage at the later of its criteria time and its prerequisite's
entry time. Every transition is stored in `stage_history` with a timestamp. A row is
written once, so the first entry is kept.

```mermaid
stateDiagram-v2
    [*] --> signed_up: any user signs up
    signed_up --> installed: GitHub App installed
    installed --> activated: baseline_created AND first_pr_report
    activated --> engaged: runs on 3+ days in 14d, a regression acked, or the check made required
    engaged --> pql: an engaged user has intent above threshold
    engaged --> pqa: 2+ users engaged in 30d, or over the 5-user cap, or enterprise intent in 30d
    pqa --> opportunity: play accepted (CRM flag)
    note right of engaged
        churn_risk is an overlay, not a stage:
        ignored benchmarks, informational check,
        Wizard disabled, seats removed, runs -50%
    end note
```

`pql` describes a person. An account "reaches" pql when it has at least one PQL user.
`pqa` and `pql` both follow `engaged`, so the funnel reports each one's conversion
from `engaged`.

## Identity resolution

```mermaid
flowchart TD
    U[user] --> B{"login ends with [bot]?"}
    B -- yes --> BOT[resolved, but excluded<br/>from people counts and seats]
    B -- no --> C{"email domain = company domain?<br/>incl. subdomains"}
    BOT --> C
    C -- yes --> R1[corporate_domain 1.0]
    C -- no --> A{known alias domain?}
    A -- yes --> R2[alias_domain 0.95]
    A -- no --> O{member of a known GitHub org?}
    O -- yes, corporate-looking email --> R3[github_org 0.9]
    O -- yes, freemail --> R4[freemail_github_org 0.8]
    O -- no --> P{"commit-email domain on PRs<br/>matches a company?"}
    P -- yes --> R5[commit_email_domain 0.7]
    P -- no --> R6[unresolved 0]
    R1 & R2 & R3 & R4 & R5 --> ROLL["roll up subsidiary → parent account<br/>child kept for reporting"]
```

The funnel report includes the unresolved rate and the "resolved by fallback" rate
(anything but corporate domain).

## Storage

| Table | Key | What |
|---|---|---|
| companies, users, repos, manifests, events | natural ids | inputs (upserted) |
| ground_truth | company_id | hidden labels; only `evaluate.py` reads them |
| opportunities | account_id, play_id | simulated "play accepted" CRM flag |
| identity | user_id, week | resolution per week |
| signals | account_id, week, kind | stage, seats, scan, plays, ARR per week |
| scores_history | account_id, week | fit, intent, priority, deltas, reasons |
| stage_history | entity_type, entity_id, stage | first time an entity entered a stage |

## Performance notes (baseline)

The baseline is written to be efficient so regressions show up clearly in CodSpeed:

* lookup dicts (domain, alias, org, account root, repo privacy) built once per run
* single time-ordered pass over events in `seats.py` and `features.py`, with repo
  enablement tracked as the stream goes
* window cutoffs and the decay rate computed once per run, never per event
* scanner regexes compiled once at module level
* `bisect` to cut events at the as-of time (no full copy or rescan)
* the rolling 30-day active-user series is built from a difference array over merged
  per-user intervals: O(activity + days), not O(users x days)
