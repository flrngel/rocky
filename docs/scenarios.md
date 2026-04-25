# Rocky capability catalog

Version: 1.3.0

## Route scenarios

| Task signature | Lane | Class | Risk | Tool families | Preferred tools |
|---|---|---|---|---|---|
| `automation/general` | `standard` | `automation` | `medium` | filesystem, shell | write_file, read_file, run_shell_command |
| `conversation/general` | `standard` | `conversation` | `low` | — | — |
| `data/spreadsheet/analysis` | `standard` | `data` | `medium` | filesystem, shell | run_shell_command, read_file |
| `extract/general` | `standard` | `extraction` | `low` | filesystem, shell | read_file, run_shell_command |
| `local/runtime_inspection` | `standard` | `repo` | `medium` | shell | run_shell_command, read_file |
| `meta/runtime` | `meta` | `meta` | `low` | — | — |
| `repo/general` | `standard` | `repo` | `medium` | filesystem, shell | run_shell_command, read_file |
| `repo/shell_execution` | `standard` | `repo` | `medium` | filesystem, shell | run_shell_command, read_file, write_file |
| `repo/shell_inspection` | `standard` | `repo` | `medium` | shell, filesystem | run_shell_command, read_file |
| `research/live_compare/general` | `standard` | `research` | `medium` | web, browser | search_web, fetch_url, agent_browser |
| `site/understanding/general` | `standard` | `site` | `medium` | web, browser, filesystem | fetch_url, search_web, agent_browser |

## Operator commands

`/help`, `/tools`, `/skills`, `/harness`, `/memory`, `/student`, `/threads`, `/teach`, `/learned`, `/permissions`, `/context`, `/status`, `/sessions`, `/resume`, `/new`, `/config`, `/doctor`, `/why`, `/compact`, `/freeze`, `/plan`, `/learn`, `/undo`, `/init`, `/trace`, `/configure`, `/setup`, `/set-up`, `/meta`

## Built-in tools

`agent_browser`, `fetch_url`, `read_file`, `run_shell_command`, `search_web`, `write_file`

## Learning scenarios

| Scenario | What it covers |
|---|---|
| `SL-MEMORY` | Autonomous capture and reuse of project memory from normal prompts. |
| `SL-RETROSPECT` | Autonomous self-reflection persisted across processes. |
| `SL-PROMOTE` | Candidate learned policy promotion after verified reuse. |
| `SL-BRIEF` | Automatic project-brief rebuilding from promoted memories. |
| `UNDO` | Atomic rollback of learning lineages via the ledger. |
| `META-VARIANT` | Safe runtime overlay experimentation with canarying and rollback. |

## Harness scenarios

| Scenario | Phases |
|---|---|
| `agentic_repo_lookup_11` | single_phase |
| `agentic_exact_output_131` | single_phase |
| `learning_catalog_review_181` | prepare_workspace, install_and_baseline, teach, retry_with_learning, grade_results |

