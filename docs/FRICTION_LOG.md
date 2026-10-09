# Friction log: my own CodSpeed onboarding

A template for noting where onboarding (on this repo) was smooth and where it was
rough. Fill it in while doing each step, not afterwards. Not filled in yet.

| Field | How to fill it |
|---|---|
| Step | e.g. "Install GitHub App", "Start AI Setup", "First run on main" |
| Expected | What I thought would happen |
| Actual | What happened (copy error messages verbatim) |
| Time | Minutes spent |
| Severity | blocker / confusing / papercut / delight |
| Signal | Which event in this engine's taxonomy this step would emit |
| Idea | What would remove the friction |

## Log

| # | Step | Expected | Actual | Time | Severity | Signal | Idea |
|---|---|---|---|---|---|---|---|
| 1 | Log in at app.codspeed.io | | | | | `signed_up` | |
| 2 | Import: install the GitHub App (org or personal), select repos | | | | | `github_app_installed`, `repo_imported` | |
| 3 | Open the repo's Setup | | | | | `setup_started` | |
| 4 | Start AI Setup (Wizard) or manual setup | | | | | `wizard_pr_opened` / `workflow_committed` | |
| 5 | Merge the setup PR | | | | | `wizard_pr_merged` | |
| 6 | workflow_dispatch backtest | | | | | `backtest_run` | |
| 7 | First run on the default branch (baseline) | | | | | `baseline_created` | |
| 8 | First PR report comment + "CodSpeed Performance Analysis" check | | | | | `first_pr_report` | |
| 9 | Enable the memory instrument | | | | | `run_completed{instrument=memory}` | |
| 10 | Regression PR: read the report and flame graph | | | | | `regression_detected`, `flamegraph_viewed` | |
| 11 | `@codspeedbot explain` / `@codspeedbot fix` | | | | | `codspeedbot_mention`, `codspeedbot_fix_pr_opened` | |
| 12 | Connect the MCP server to Claude Code | | | | | `mcp_connected`, `mcp_tool_call` | |

## Takeaways

- 
