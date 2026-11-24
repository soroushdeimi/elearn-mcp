# elearn-mcp plan

MCP server for elearn.ut.ac.ir (Moodle, boost theme) to list courses, deadlines and
assignments, download assignment files, draft solutions with a worker pipeline and
upload them after the student approves.

## Verified facts (2026-11-24)
- ArvanCloud JS challenge in front of the site: sets `__arcsjs` / `__arcsjsc` cookies
  (domain `ut.ac.ir`, 900 s). Solved by running the page's own script in node `vm`.
- Login: `/login/index.php` redirects to `sso1.ut.ac.ir/ApiContainer.SSO.RCL1/Account/Login`
  (OpenID code flow). POST `Account/login` form-urlencoded: `ReturnUrl`, `captcha` (empty
  accepted), `Username`, `Password`, `button=login`, `RememberLogin=false`,
  `__RequestVerificationToken`, with the antiforgery cookie and Referer of the login page.
  Must be one fresh flow: a stale ReturnUrl gives HTTP 400 `invalid_request`.
- After SSO: `/auth/utoauth2/login.php?...&oauth2code=...` shows an account picker
  (radio `auth_utoauth2_username` = student id). POST it with `continue` to the same URL;
  lands on `/my/courses.php`. One SSO user can own several student ids.
- Moodle web services (token.php) are disabled. Internal AJAX works with the session:
  POST `/lib/ajax/service.php?sesskey=..&info=<method>` body
  `[{"index":0,"methodname":..,"args":{..}}]`. `sesskey` is in `M.cfg` of any page.
  Working: `core_course_get_enrolled_courses_by_timeline_classification`,
  `core_calendar_get_action_events_by_timesort`. Not available: `mod_assign_get_assignments`.
- Assignment details come from `/mod/assign/view.php?id=<cmid>` HTML: description,
  `pluginfile.php/.../mod_assign/introattachment/...` files, submission status table.
- Session timeout 8 h: re-login on redirect to login. AJAX errors `servicerequireslogin` /
  `invalidsesskey` mean the session is gone. No concurrent-login limit was seen.
- The Arvan cookie lasts 900 s, so any later request (AJAX POST too) can get the challenge again.
- sso1 rate-limits: HTTP 429 JSON with `retryAfterSeconds` (seen: 1). Retry after the delay.
- One password login serves every student id: a fresh client with only the sso1 + Arvan cookies
  goes straight to the picker. Verified live: two ids, one password POST.
- Installed `mcp` is 2.x: the server class is `mcp.server.mcpserver.MCPServer` (FastMCP is gone).
- Page language follows the user setting: parse by CSS class, never by label text.
- Upload (verified live 2026-11-24 on CA3 with a placeholder file, then removed):
  1. GET `view.php?id=<cmid>&action=editsubmission`: form `mform` hidden inputs (lastmodified, id,
     userid, action=savesubmission, sesskey, _qf__mod_assign_submission_form, files_filemanager =
     draft itemid); `client_id`, `ctx_id`, maxbytes/maxfiles and the `"type":"upload"` repo id (5)
     are in the page JS.
  2. POST multipart `/repository/repository_ajax.php?action=upload` with repo_upload_file, title,
     itemid, repo_id, env=filemanager, sesskey, client_id, ctx_id, savepath=/, license.
  3. POST the form fields + submitbutton to `/mod/assign/view.php`.
  No draft mode on these courses: saving is already "submitted for grading".
  Remove: GET `action=removesubmissionconfirm`, POST its form (action=removesubmission, sesskey, userid).

## Architecture
```
head agent --MCP--> elearn_mcp.server (MCPServer, stdio)
                         auth: arvan + sso + account pick, one session per student id
                         tools: accounts, courses, deadlines, assignment, download,
                                submission_status, upload_draft, submit(confirm)
head agent --> Celery + Redis --> worker tasks (draft parts) --> head review/merge --> approve --> upload
```
Both student ids are supported; every tool takes `account` (student id).
Credentials only from env (`ELEARN_USERNAME`, `ELEARN_PASSWORD`) or macOS Keychain, never in files.
Final submit always needs explicit confirmation from the student.

## Phases
| # | Scope | Done when |
|---|---|---|
| 0 | read-only client + MCP: auth, courses, deadlines, assignment, download | done 2026-11-24, live for both ids |
| 1 | upload draft + submit with confirm | done 2026-11-24: upload/remove verified live, two-step token + sha256 |
| 2 | Celery + Redis pipeline, worker tasks, review loop | done 2026-11-24: materials/watch/agent tasks, beat schedule, chord fan-out; verified live (29-file course job, two parallel agent parts) |
| 3 | output formats (LaTeX->PDF, zip), requirement checks | matches each assignment's rules |
| 4 | watcher for new assignments / deadlines | done 2026-11-24: macos and telegram verified live; sms not configured |

## Phase 0 tasks
- T1 scaffold (uv, src layout, pytest, ruff) + `arvan.py` challenge solver
- T2 `sso.py` login flow + account picker (httpx, respx-mocked tests on fixtures)
- T3 `moodle.py` AJAX client + `parse.py` assignment page parser
- T4 `server.py` FastMCP tools, multi-account session manager
- T5 live smoke script (manual, opt-in, not in CI)

## Also done (2026-11-24)
- Course contents (`core_courseformat_get_state`), resource/folder/url modules, bulk materials with manifest and text copies (extractor VERSION 2).
- Grades overview, popup notifications, submitted files and feedback parsing (checked on a real graded page).
