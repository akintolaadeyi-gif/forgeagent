# Acceptance results (raw, real local model)

Produced by `scripts/acceptance.py`, which drives the running application through its HTTP API against the real local model
(Huihui-Qwen3.8-27B-abliterated, Q4_K_M, llama.cpp b11620, RTX 4090 Laptop 16 GB). Entries are appended in the order they ran and are **not edited**.
Entries marked `(crashed)` or FAIL are kept on purpose; what they were:

* `boundaries (crashed)`: an API race (a project was visible only after the response was sent) - fixed by committing before responding.
* `fullstack (crashed)`, early entries: the backend process was stopped while a scenario was running (interrupted batches).
* `Supervised ... FAIL` (twice): a flaw in the *test* - it auto-approved every `create_file`, so a workaround for a denied command was approved. Fixed, then re-run: PASS.
* `fullstack (crashed)` / `fullstack_resume (crashed)`: the *harness* timed out (it does not answer approvals) while the agent was still working; the agent run was
  continued after a backend restart and **Resume**, and the final PASS entry verifies the result independently. The run needed one human approval caused by a command-classifier
  false positive (fixed afterwards). The "duration" in the final entry is not a performance figure (see the paper, section 12).
* Paths under `%TEMP%` replace the original temporary folder names.

---

### Fix a deliberately failing test (acceptance 3,4,5,6,7) - **PASS**  (2026-10-14 20:30)

- agent task status: `COMPLETED` | steps used: 9
- tools executed in order: `list_directory -> git_changes -> read_file -> read_file -> edit_file -> run_tests -> run_tests -> run_command -> run_tests -> remember -> complete_task`
- independent re-run of tests after the agent finished (exit 0): `2 passed in 0.01s`
- test file left unmodified: True
- final calc.py:
```python
def add(a, b):
    return a + b


def multiply(a, b):
    return a * b
```
- agent report:
```
Found and fixed the bug: `add()` in calc.py was returning `a - b` instead of `a + b`. Changed it to `return a + b` (tests were left untouched). Also installed pytest, which was missing from the environment. Verified with `python -m pytest -v`: both tests (test_add, test_multiply) pass — 2 passed in 0.02s.

---
**Verified from tool logs:**
- Files changed: 1 (calc.py)
- Commands executed: 4
- Last test run: PASSED (tests passed {'passed': 2})
- Last build: not run
- Checkpoint: 2bba41507da84048b3f72ceb14552d0a
```

### boundaries (crashed) - **FAIL**  (2026-10-14 20:36)

RuntimeError: POST /projects/6155326bd33b4b52bf3132d6e72f257b/chat -> 404: {"detail":"Project not found"}

### Workspace boundaries respected (acceptance 10) - **PASS**  (2026-10-14 20:42)

- target outside workspace: `%TEMP%\forge-outside-1792006784.txt` exists = **False**
- tool runs refused/failed: [('create_file', "Path '%TEMP%\\forge-outside-1792006784.txt' resolves outside the"), ('run_command', 'denied by user')]
- approvals requested (all denied by the test): ['run_command']
- inside.txt created: True
- final status `COMPLETED`

### Git checkpoint restores previous state (acceptance 11) - **PASS**  (2026-10-14 20:43)

- after agent: data.txt = `modified by agent`; checkpoints: ['completed: Replace the content of data.txt with th', 'before: Replace the content of data.txt with the s']
- after restore to `before: Replace the content of data.txt `: data.txt = `original`; extra.txt exists = False

### User can stop a running agent (acceptance 9) - **PASS**  (2026-10-14 20:53)

- stop call returned {'stopped': True}; run active afterwards: False; task status `CANCELLED`
- stopped in 8.7s; files at stop: 1, 8 s later: 1 (no further writes)

### Supervised mode requires approval; denial is honoured - **FAIL**  (2026-10-14 20:56)

- approvals requested for: ['create_file', 'run_command', 'create_file']
- notes.txt (approved) exists: True; other.txt (denied) exists: True
- audit: [('list_directory', 'auto'), ('git_changes', 'auto'), ('create_file', 'approved'), ('run_command', 'denied'), ('create_file', 'approved'), ('read_file', 'auto'), ('read_file', 'auto'), ('complete_task', 'auto')]

### Task state preserved across a hard restart (acceptance 8) - **PASS**  (2026-10-14 20:58)

- backend process killed (taskkill /F) while the task ran; files present at kill time: ['one.txt']
- after restart the task was recovered as `BLOCKED`: "Interrupted by application restart. Resume to continue from the saved state."
- after Resume: status `COMPLETED`, files: ['one.txt', 'three.txt', 'two.txt'], steps used: 9

### fullstack (crashed) - **FAIL**  (2026-10-14 21:01)

ConnectError: [WinError 10061] No connection could be made because the target machine actively refused it

### User can stop a running agent (acceptance 9) - **PASS**  (2026-10-14 21:16)

- stop call returned {'stopped': True}; run active afterwards: False; task status `CANCELLED`
- stopped in 8.0s; files at stop: 0, 8 s later: 0 (no further writes)

### Supervised mode requires approval; denial is honoured - **FAIL**  (2026-10-14 21:23)

- approvals requested for: ['create_file', 'run_command', 'create_file']
- notes.txt (approved) exists: True; other.txt (denied) exists: True
- audit: [('list_directory', 'auto'), ('search_files', 'auto'), ('search_files', 'auto'), ('create_file', 'approved'), ('run_command', 'denied'), ('create_file', 'approved'), ('read_file', 'auto'), ('read_file', 'auto'), ('complete_task', 'auto')]

### Task state preserved across a hard restart (acceptance 8) - **PASS**  (2026-10-14 21:29)

- backend process killed (taskkill /F) while the task ran; files present at kill time: ['one.txt']
- after restart the task was recovered as `BLOCKED`: "Interrupted by application restart. Resume to continue from the saved state."
- after Resume: status `COMPLETED`, files: ['one.txt', 'three.txt', 'two.txt'], steps used: 8

### fullstack (crashed) - **FAIL**  (2026-10-14 23:26)

TimeoutError: agent did not finish in time

### fullstack_resume (crashed) - **FAIL**  (2026-10-15 03:20)

TimeoutError: agent did not finish in time

### Complete a small full-stack app from natural language (acceptance 13) - **PASS**  (2026-10-15 05:43)

- duration: 553.3 min; root status `COMPLETED`; milestones: [('architect', 'COMPLETED', 3), ('developer', 'COMPLETED', 8), ('developer', 'COMPLETED', 30), ('developer', 'COMPLETED', 37), ('developer', 'COMPLETED', 6), ('tester', 'COMPLETED', 19), ('reviewer', 'COMPLETED', 6)]
- tool runs: 142 (failed: 14); browser tools used: ['3 browser actions', '3 browser actions', '2 browser actions', '4 browser actions', 'opened http://127.0.0.1:8123/ (200)', '3 browser actions']
- independent checks: {
 "pytest (independent re-run)": "exit 0: 10 passed, 1 warning in 0.62s",
 "live API (POST/GET/PATCH/DELETE)": "POST 201, list has item=True, PATCH 200, DELETE 204",
 "GET / serves HTML": "200 html=True fetch-used=True"
}
- workspace files: ['.gitignore', '.pytest_cache\\.gitignore', '.pytest_cache\\CACHEDIR.TAG', '.pytest_cache\\README.md', '.pytest_cache\\v\\cache\\nodeids', '__pycache__\\main.cpython-312.pyc', '__pycache__\\test_api.cpython-312-pytest-9.1.1.pyc', 'main.py', 'requirements.txt', 'static\\index.html', 'test_api.py', 'todos.db']
- agent report:
```
Final review passed with no blocking problems. Verified: (1) All 10 pytest tests pass via the project venv — covering GET/POST/PATCH/DELETE, 422 on blank/missing title, 404 on missing id, and frontend serving, each against a fresh temp SQLite DB. (2) Backend main.py matches the spec exactly: create_app(db_path) factory with module-level app, correct schema, per-request sqlite3 connections closed in finally, parameterized SQL, 201/422/404/204 semantics, and the frontend served at '/' with a 404 fallback. (3) Frontend static/index.html is a single vanilla-JS page with no build step; add/toggle/delete/list work via fetch, with error status messages, a double-submit guard, aria labels, and no XSS (user data only via textContent). (4) The dev server is live on port 8010 and its logs show real requests succeeding (GET / 200, POST 201, PATCH 200, DELETE 204). Only non-blocking note: a third-party starlette TestClient/httpx deprecation warning, which is not a project issue.

---
**Verified from tool logs:**
- Files changed: 4 (main.py, requirements.txt, static/index.html, test_api.py)
- Commands executed: 23
- Last test run: PASSED (tests passed {'passed': 10})
- Last build: not run
- Tasks:
  - [COMPLETED] Plan: architecture & task breakdown
  - [COMPLETED] Scaffold project: venv + requirements.txt
  - [COMPLETED] Implement FastAPI backend in main.py
  - [COMPLETED] Create static/index.html frontend
  - [COMPLETED] Write and run pytest API tests until green
  - [COMPLETED] Runtime verification: run server on 8123, verify in browser, stop server
  - [COMPLETED] Final review
- Checkpoint: 069ed64027ee4e00bcfa296c350d1547
```

### Supervised mode requires approval; denial is honoured - **PASS**  (2026-10-15 05:46)

- approvals requested for: ['create_file', 'run_command', 'create_file']
- notes.txt (approved) exists: True; other.txt (denied) exists: False
- audit: [('list_directory', 'auto'), ('create_file', 'approved'), ('run_command', 'denied'), ('create_file', 'denied'), ('read_file', 'auto'), ('complete_task', 'auto')]
