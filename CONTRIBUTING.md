# Contributing

1. Fork, create a feature branch.
2. Backend: `cd backend && .\.venv\Scripts\python -m pytest` (all tests must pass). Frontend: `cd frontend && npm run typecheck && npm test`.
3. New agent tools go in `backend/app/tools/`, must define a Pydantic `Params` model, a category (`read|write|exec|git|browser|destructive|meta`),
   and need a test in `backend/tests/`. Add the tool to a role in `backend/app/agent/prompts.py`.
4. Behaviour claims in docs must be backed by a test or an entry in `docs/ACCEPTANCE_RESULTS.md`.
5. Open a pull request describing the change and how you verified it.
