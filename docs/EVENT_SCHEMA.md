# Event schema and import formats

`signal-engine ingest` loads JSONL (`.jsonl` / `.ndjson`) or CSV files. Rows are validated
one by one. Bad rows are rejected with a line number and a reason, and good rows are
upserted. Working examples for every format are in [`docs/examples/`](examples/), and the
integration tests import them.

```bash
uv run signal-engine ingest --db out/demo.db \
  --companies docs/examples/companies.csv --users docs/examples/users.jsonl \
  --repos docs/examples/repos.csv --manifests docs/examples/manifests.jsonl \
  --events docs/examples/events.jsonl --events docs/examples/events.csv
```

Import order is always companies -> users -> repos -> manifests -> events.

> Every event name is a hypothesis that mirrors CodSpeed's documented product surface.
> It is not CodSpeed's internal schema.

## Events

| Field | Type | Required | Notes |
|---|---|---|---|
| `user_id` | string | yes | Who did it (bots end their login with `[bot]`) |
| `ts` | ISO-8601 with offset | yes | `2026-10-01T12:00:00Z` or `...-07:00`; stored in UTC. Naive timestamps are rejected |
| `event_type` | string | yes | Must be in the taxonomy below |
| `repo_id` | string or null | no | The repo the event concerns |
| `props` | object | no | JSON object. In CSV, a JSON string in the `props` column |

`event_id` is assigned on import (max existing id + 1, in timestamp order).

JSONL:

```json
{"user_id": "ux1", "ts": "2026-10-01T12:00:00Z", "event_type": "run_completed", "repo_id": "rx1", "props": {"instrument": "simulation", "branch_type": "pr", "benchmarks": 42}}
```

CSV:

```csv
user_id,ts,event_type,repo_id,props
ux1,2026-10-03T12:00:00Z,regression_detected,rx1,"{""impact_pct"": 14.2, ""benchmark"": ""parse_large""}"
```

### Taxonomy

| Category | event_type | props |
|---|---|---|
| Acquisition | `signed_up`, `dashboard_login`, `docs_visit`, `demo_requested` | |
| Activation | `github_app_installed` | `target` = org \| personal, `repos_selected` |
| | `repo_imported`, `wizard_pr_opened`, `wizard_pr_merged`, `workflow_committed`, `backtest_run`, `baseline_created`, `first_pr_report` | |
| | `setup_started` | `path` = wizard \| manual |
| Depth | `run_completed` | `instrument` = simulation \| walltime \| memory, `branch_type` = default \| pr, `benchmarks` |
| | `cli_auth_login`, `local_upload`, `sharding_enabled`, `partial_runs_enabled`, `mongodb_instrument_enabled` | |
| Value | `regression_detected` | `impact_pct`, `benchmark` |
| | `regression_acknowledged`, `improvement_detected`, `required_check_enabled`, `informational_check_enabled` (negative), `threshold_changed`, `flamegraph_viewed` | |
| AI | `codspeedbot_mention` | `intent` = impact \| explain \| fix \| add_benchmark |
| | `codspeedbot_fix_pr_opened`, `codspeedbot_fix_pr_merged`, `wizard_disabled` (negative), `mcp_connected`, `skill_installed` | |
| | `mcp_tool_call` | `tool` = list_repositories \| list_runs \| get_run \| compare_runs \| get_benchmark_result \| query_flamegraph \| list_threads |
| Hygiene / risk | `benchmark_ignored` | `reason` |
| | `benchmark_archived`, `seat_removed`, `repo_deleted` | |
| Monetization | `pr_authored_private` (counts toward active users) | optional `commit_email_domain` (used by identity resolution) |
| | `report_viewed` (counts toward active users) | |
| | `free_limit_exceeded`, `trial_started`, `user_blocked_no_seat`, `ryzen_requested` | |
| | `trial_ending` | `days_left` |
| | `seat_added` | `target_user` (the user who got the seat) |
| | `auto_seat_allocation_toggled` | `enabled` (bool) |
| | `plan_upgraded` | `plan`, `billing` = monthly \| annual |
| | `macro_runner_minutes` | `runner` = graviton \| ryzen, `minutes` |
| | `runner_budget_set` | `budget_usd` |
| Enterprise intent | `pricing_page_view`, `enterprise_page_view`, `sso_page_view`, `trust_center_visit`, `soc2_report_requested`, `security_docs_visit` | |

## Users

| Field | Type | Required | Notes |
|---|---|---|---|
| `user_id` | string | yes | |
| `email` | string | yes | Drives the identity waterfall |
| `name` | string | yes | |
| `title` | string | no | Mapped to a persona via `signals.toml` keywords |
| `github_login` | string | yes | `[bot]` suffix = bot |
| `github_orgs` | list (JSON) or `;`-separated (CSV) | no | Org memberships |

## Companies (accounts)

| Field | Type | Required | Notes |
|---|---|---|---|
| `company_id` | string | yes | |
| `name`, `domain` | string | yes | Primary corporate domain |
| `alias_domains` | list or `;`-separated | no | Old or regional domains |
| `github_orgs` | list or `;`-separated | no | |
| `industry`, `region` | string | no | Default `unknown` |
| `employee_count`, `eng_headcount` | integer | yes | |
| `parent_id` | string | no | Subsidiaries roll up to the top-level parent |

## Repos

`repo_id`, `owner` (GitHub org or user login), `name`, `is_private` (true/false/1/0/yes/no),
`language`.

## Manifests

`repo_id`, `path` (repo-relative, e.g. `Cargo.toml`, `.github/workflows/ci.yml`),
`content` (the file's text). The scanner decides from the path which detector applies,
and ignores irrelevant files.

## Export formats

### `out/crm_accounts.csv`

| Column | HubSpot (company) | Salesforce (Account) |
|---|---|---|
| `signal_engine_account_id` | `signal_engine_account_id` (custom, unique) | `Signal_Engine_Id__c` (External ID) |
| `name` | `name` | `Name` |
| `domain` | `domain` | `Website` |
| `industry` | `industry` | `Industry` |
| `numberofemployees` | `numberofemployees` | `NumberOfEmployees` |
| `region` | `region__c` (custom) | `Region__c` |
| `subsidiaries` | custom | child Accounts via `ParentId` |
| `lifecyclestage` | `lifecyclestage` (subscriber / lead / marketingqualifiedlead / salesqualifiedlead / opportunity / customer) | `Lifecycle_Stage__c` |
| `product_stage` | custom | `Product_Stage__c` |
| `pqa_flag` | custom | `PQA_Flag__c` |
| `primary_play`, `secondary_plays` | custom | `Primary_Play__c`, `Secondary_Plays__c` |
| `play_owner` | map to `hubspot_owner_id` | `OwnerId` |
| `play_owner_note` | custom (`over founder capacity` when reassigned) | `Play_Owner_Note__c` |
| `fit_score`, `intent_score`, `priority_score`, `intent_delta_wow` | custom | `Fit_Score__c`, `Intent_Score__c`, `Priority_Score__c`, `Intent_Delta_WoW__c` |
| `seats_used`, `trial_days_left`, `blocked_users` | custom | `Seats_Used__c`, `Trial_Days_Left__c`, `Blocked_Users__c` |
| `est_arr_usd`, `enterprise_uplift`, `churn_risk`, `top_reason` | custom | `Estimated_ARR__c`, `Enterprise_Uplift__c`, `Churn_Risk__c`, `Top_Reason__c` |

Product stage maps to HubSpot lifecycle like this: market only -> subscriber;
signed up / installed -> lead; activated / engaged -> marketingqualifiedlead;
PQL / PQA -> salesqualifiedlead; opportunity -> opportunity. A paid plan -> customer.

### `out/crm_contacts.csv`

| Column | HubSpot (contact) | Salesforce (Contact) |
|---|---|---|
| `signal_engine_user_id` | custom (unique) | `Signal_Engine_Id__c` (External ID) |
| `email`, `firstname`, `lastname`, `jobtitle` | same names | `Email`, `FirstName`, `LastName`, `Title` |
| `company`, `signal_engine_account_id` | `company` + association | `AccountId` (lookup by external id) |
| `persona`, `committee_role` | custom | `Persona__c`, `Buying_Role__c` |
| `pql_flag`, `engaged_flag` | custom | `PQL_Flag__c`, `Engaged_Flag__c` |
| `identity_method`, `identity_confidence` | custom | `Identity_Method__c`, `Identity_Confidence__c` |

### `out/alerts.jsonl`

One Slack Block Kit-style payload per (account, play) at or above
`alert_min_priority`, with `channel`, `text`, `blocks` (header, fields, why-now,
suggested opener, context) and `metadata` (`account_id`, `play_id`, `week`). They're
ready for an incoming webhook but **never sent**.
