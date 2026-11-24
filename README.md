# elearn-mcp

MCP server for elearn.ut.ac.ir (University of Tehran Moodle). One SSO login serves every
student id of the user.

Unofficial and not affiliated with the University of Tehran. It acts only with your own login.
Follow each course's rules: many courses require individual work, and anything uploaded with
this tool is your own submission.

## Setup
```sh
uv sync
security add-generic-password -s elearn-mcp -a <you>@ut.ac.ir -w   # SSO password
```
Register it in your MCP client as a stdio server: command `uv run --project <repo> elearn-mcp`,
env `ELEARN_USERNAME=<you>@ut.ac.ir`.
The password is read from `ELEARN_PASSWORD` or, if unset, from the macOS Keychain
(service `elearn-mcp`, account = username). Never put it in a config file.

| Variable | Default | Use |
|---|---|---|
| `ELEARN_DOWNLOAD_DIR` | `~/Documents/elearn/downloads` | assignment attachments |
| `ELEARN_MATERIALS_DIR` | `~/Documents/elearn/materials` | course materials + text copies |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Keychain | Telegram notifications (set `HTTPS_PROXY` if Telegram is blocked) |
| `KAVENEGAR_API_KEY`, `SMS_RECEPTOR`, `KAVENEGAR_SENDER` | Keychain | SMS via Kavenegar |

Secrets can live in the Keychain under service `elearn-mcp` with the variable name as account:
`security add-generic-password -s elearn-mcp -a TELEGRAM_BOT_TOKEN -w`.

## Tools
| Area | Tools |
|---|---|
| accounts, courses | `list_accounts`, `list_courses`, `course_contents`, `external_link` |
| assignments | `list_deadlines`, `all_deadlines`, `get_assignment`, `download_attachments` |
| files | `download_module`, `download_course_materials`, `list_materials`, `read_material` |
| grades, messages | `grades`, `notifications` |
| submission | `prepare_submission` -> `confirm_submission(token)`, `remove_submission(confirm=true)` |
| notifications | `send_notification`, `watch_now` |
| background jobs | `start_materials_job`, `start_watch_round`, `start_agents`, `job_status` |

Submitting always takes two calls. `prepare_submission` checks files and limits and returns a
single-use token (10 min); `confirm_submission` uploads only if the files did not change.
On these courses saving counts as "submitted for grading" (no draft mode).

`read_material` pages through text extracted from PDF, PPTX, DOCX and notebooks; Persian PDF
text is normalised (letter forms, word breaks, reversed digits). Parentheses around Latin text on
its own line can still come out swapped.

## Watcher
```sh
uv run elearn-watch --channels macos,telegram --interval 900   # --once for a single round
```
Sends one Persian message per change: new assignment, deadline within 24 h (once), new grade,
new site notification, new course file. State: `~/.local/state/elearn-mcp/state.json`.
The first round only reports close deadlines, so it does not flood.

## Background jobs (Celery + Redis)
```sh
brew services start redis                 # local only (127.0.0.1)
ELEARN_USERNAME=<you>@ut.ac.ir uv run elearn-worker   # runs the jobs (thread pool, ELEARN_WORKERS=3)
ELEARN_USERNAME=<you>@ut.ac.ir ELEARN_WATCH_CHANNELS=macos,telegram uv run elearn-beat   # watcher every ELEARN_WATCH_INTERVAL s (900)
```
- `start_materials_job` downloads a course in the background; `job_status(job_id)` returns the summary.
- `start_agents(parts)` runs up to 6 parts in parallel. Each part is `{"prompt", "workdir"}`; the workdir
  must be inside `ELEARN_JOBS_ROOT` (default `~/Desktop/myprojects/elearn-runs`). Each part runs
  `ELEARN_AGENT_CMD` (default `elearn-agent`, a script you provide on PATH) as
  `<cmd> -p --output-format json --max-turns N` with the prompt on stdin and `ELEARN_AGENT_MODEL=<model> (when given)`;
  output goes to `<workdir>/.jobs/<task id>.json`. The caller reviews the results; nothing is submitted.
- Jobs wait in Redis while the worker is down and run when it starts again.

## Development
```sh
uv run pytest -q && uv run ruff check . && uv run ruff format --check .
```
Tests use sanitized real pages in `tests/fixtures` and never touch the network.
Site facts and the verified login/upload flow are in `PLAN.md`.
