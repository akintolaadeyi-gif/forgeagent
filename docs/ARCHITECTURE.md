# Architecture

```
frontend/ (React + TS + Vite + Tailwind + Monaco)         backend/app/
  pages: Dashboard, Workspace, Settings                     main.py          FastAPI app, lifespan: Alembic upgrade, crash recovery
  useProject.ts  REST + WebSocket live state                api/routes.py    REST + WS endpoints
                                                            agent/
                                                              orchestrator.py  plan -> task queue -> agent loop -> tool execution
                                                              runtime.py       run handles, approvals, pause/cancel, status helpers
                                                              permissions.py   allow / ask / deny policy
                                                              prompts.py       system prompt assembly, role -> tool sets
                                                              context.py       token budget, truncation, compaction
                                                              memory.py        persistent memory + local vector search
                                                            llm/gateway.py   OpenAI-compatible client (llama.cpp)
                                                            llm/toolparse.py tool-call recovery & JSON repair
                                                            tools/*          typed tools, process runner, browser, git
                                                            security.py      path validation, command classification, redaction
                                                            models.py        SQLAlchemy models (Alembic migrations in backend/alembic)
```

## Execution flow

1. `POST /projects/{id}/chat` -> `Orchestrator.start_goal`: creates a root **Task**, stores the user message, ensures the workspace is a Git repo,
   starts an asyncio task and returns immediately (UI stays responsive; progress arrives over `WS /api/ws/projects/{id}`).
2. A Git **checkpoint** ("before: ...") is committed so the whole run can be undone.
3. *Planning* (`Plan: auto|always|never`): the **Architect** role (read-only tools + `submit_plan`) inspects the workspace and submits a
   plan. A `simple` plan runs as one Developer task; a `complex` plan becomes child tasks with dependencies, plus a final **Reviewer** task.
   If the Architect fails to produce a plan, the run continues as a single task and says so (no silent degradation).
4. *Task queue* (`_run_children`): picks PENDING tasks whose dependencies are COMPLETED, ordered by (priority, position); runs each with its
   role; commits a checkpoint when a task completes. A BLOCKED/FAILED task stops the queue; remaining tasks stay PENDING and are resumable.
   A Reviewer verdict of "blocking problems" triggers exactly one Debugger fix round.
5. *Agent loop* (`_agent_loop`) per task: build prompt (system + memory + summary + history) -> compact if over budget -> stream completion ->
   persist assistant message -> execute tool calls sequentially -> persist tool results -> repeat until `complete_task`.
6. *Finish*: the final report appended to the model's summary is generated **from the tool audit log** (files changed, commands run, last
   test/build status), not from what the model claims.

### Guards against bad agent behaviour
* `complete_task(success)` is **rejected** while the most recent test/build run failed.
* Text-only replies that look like plans (or that follow edits) are nudged to continue or call `complete_task` (max `max_nudges`);
  plain Q&A answers are accepted immediately.
* Invalid / unknown tool calls and schema violations are returned to the model as errors with the expected schema.
* Duplicate identical actions without any intervening change are refused after two repeats; consecutive failures park the task as BLOCKED.
* Step limit per task; retry limit per task; command timeouts; process-tree kill on stop.

## Persistence and resume
Everything needed to continue lives in SQLite: tasks (status, steps, summary), the complete message history (including tool calls/results),
the audit log (`tool_runs`), memory, checkpoints. At startup `recover_interrupted()` parks tasks left in PLANNING/RUNNING/TESTING as BLOCKED
("Interrupted by application restart"). *Resume* resets unfinished tasks to PENDING, re-loads their history (inserting a synthetic
"interrupted, outcome unknown" result for any tool call that never completed), tells the model to re-check workspace state, and continues.
Completed milestones are not repeated.

## Local LLM gateway
* `GET /models` discovery; `GET /props` (llama.cpp) for real `n_ctx`, chat-template tool support, build.
* Streaming SSE parsing: content, `reasoning_content` (thinking), tool-call deltas, `usage`, llama.cpp `timings` (tokens/s).
* Tool calling: native first. If the server rejects `tools` (HTTP 400) the gateway switches to prompt-based calls (`<tool_call>` JSON) and
  remembers that. Regardless of mode, tool calls found in text are recovered: JSON, Qwen XML (`<function=..><parameter=..>`), fenced JSON,
  truncated/trailing-comma/single-quote JSON. Unrepairable calls are surfaced as errors, never executed.
* Retries with backoff on transport / 5xx errors; 4xx are not retried; cancellation closes the HTTP stream (llama.cpp aborts the generation).
* One shared `asyncio.Semaphore(1)` serialises inference: all roles share a single model instance.

## Security model

| Mechanism | What it does | What it does **not** do |
|---|---|---|
| Path validation | every file tool resolves paths with `realpath` and refuses anything outside the workspace (symlinks/junctions included), `.git`/`.forge` are write-protected | |
| Autonomy modes | *Supervised*: approval for every edit/command/browser action. *Project autonomous* (default): edits/tests/builds inside the workspace run automatically; approval for deletes, restore, destructive/network/deploy/secrets commands, unknown commands, paths outside the workspace, external URLs. *Custom*: allow/ask/deny per tool | |
| Command classification | regex/allow-list heuristics over the command string (`security.classify_command`) | It is **not a sandbox**. Obfuscated or indirect commands (e.g. a script that the agent wrote and then runs) can do anything your Windows account can. |
| Untrusted content | tool output from files/terminal/web is labelled untrusted; the system prompt says it cannot override instructions; permissions are enforced in code, not by the model | A model may still be *misled*; the approval policy is the real protection |
| Secrets | `.env`, keys etc. require approval to read/modify; known credential formats are redacted in logs; the stored API key is masked in the API | generic secrets inside source files you ask it to read are not hidden (needed for coding) |
| Network | llama-server and the API bind to 127.0.0.1; optional `FORGE_API_TOKEN` | no TLS |
| Audit | every tool run (arguments, output, approval state, duration, files) is stored in `tool_runs`; user actions go in `events` | |

For genuine isolation run ForgeAgent inside a VM/WSL2 distro/Windows Sandbox or a throw-away user account.

## Context management
`make_budget` = context size - reserved completion tokens - tool-schema tokens. Before each step history is estimated (chars/3.6, exact counts
via `/tokenize` are available in the gateway); above `compaction_threshold` the oldest messages (never the original goal, never an
assistant tool call separated from its results) are summarised by the model into `Task.summary` and flagged `compacted`. If summarisation fails a
deterministic log digest is used. Tool outputs longer than `tool_output_max_chars` are cut head+tail; the complete text stays in `tool_runs` and
is reachable with `read_full_output`. As a last resort old tool outputs are shortened / old turns dropped to fit.

## Memory
`memory_items` (kind, title, content, pinned, vector). Retrieval = cosine similarity of feature-hashed uni+bi-gram vectors blended with token
overlap; pinned items are always included. Written by the agent (`remember`), automatically (requirements, architecture/stack from the plan,
completed tasks, "test failure fixed" records with the files changed), and by you in the Memory tab.

## Data model
`projects`, `tasks` (self-referencing parent/children, `depends_on`), `messages`, `tool_runs`, `memory_items`, `checkpoints`, `events`,
`settings`, `llm_usage`. Migration `0001_initial_schema`.

## Limitations
* No Docker/WSL2 isolation (spec item "when available" - not implemented). No sandboxing of shell commands (see above).
* Memory "vector search" is lexical feature hashing, not neural embeddings.
* Research of online documentation is only possible through commands the user approves (`network_access` is a policy flag, there is no
  dedicated web-search tool).
* GraphQL / REST generation is a capability of the model, not a dedicated feature of the platform.
* Browser: headless Chromium via Playwright; the text model cannot *see* screenshots (they are saved for you; the agent reads DOM text and
  console/network errors instead).
* Token counts for budgeting are estimates unless the server reports usage; compaction quality depends on the model.
* Single-user, single-machine design; one active agent run per project; inference is serialised.
