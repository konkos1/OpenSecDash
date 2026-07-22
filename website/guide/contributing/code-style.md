# Code style

This guide describes the conventions used across the OpenSecDash codebase. It applies to
human contributors and AI coding agents alike. The single most important rule:

> **Write code that reads like the surrounding code.** Match its naming, comment density,
> error handling, and structure. When this guide and a file's local style disagree, prefer
> consistency within the file and mention it in the PR.

There is intentionally no auto-formatter configured. The enforced gates are behavior and
types, not formatting:

```bash
cd backend
uv lock --check
uv sync --frozen --group dev
.venv/bin/python -m pytest tests/ -q
.venv/bin/pyright --pythonversion 3.13 app tests ../plugins
```

Both must pass with zero errors before a PR is opened. If you did not run them, say so in
the PR description.

## Python

- **Python 3.13+, fully type-hinted.** Public functions carry parameter and return
  annotations. Pyright is the type checker; new `# type: ignore` comments need a reason on
  the same line.
- **Naming:** `snake_case` for functions/variables, `PascalCase` for classes,
  `UPPER_CASE` for module-level constants, `_leading_underscore` for module-private
  helpers.
- **Imports:** standard library, third-party, then `app.*` — as absolute imports.
  Plugins import from `app.*` and use **relative imports** for their own submodules
  (`from .services import decisions`). Lazy imports inside functions are used only to
  break genuine import cycles or keep optional dependencies out of module import.
- **Constants over magic numbers:** tunables live at module top with a comment explaining
  the chosen value (see `MAX_LINES_PER_TICK` or `CROWDSEC_BAN_DEDUPE_WINDOW` for the
  expected style).
- **Logging:** one `logger = logging.getLogger(__name__)` per module; lazy `%s`
  formatting (`logger.info("Synced %d decisions", count)`), never f-strings in log calls.
  Never log secrets — reuse the redaction helpers in `app/core/logging.py`.

### Comments and docstrings

Comments in this codebase explain **why**, not what. A comment states a constraint the
code cannot express: why a value was chosen, which failure mode a branch prevents, what
breaks if the line is removed. Examples of the expected style are everywhere — e.g. the
threading notes in `app/plugins/manager.py` or the dedupe rationale in
`plugins/crowdsec/services/dedupe.py`.

- Do not narrate code (`# increment counter`), do not leave TODOs without an issue, and
  do not write comments addressed to a reviewer ("this change fixes...").
- Public functions and non-obvious modules get a short docstring; one-line summaries are
  fine.
- Code, comments, docstrings, and commit messages are written in **English**.

### Error handling

- Never use bare `except:`. Catch the narrowest exception that makes sense; log with
  `logger.exception(...)` where the stack trace matters.
- **Failure isolation is a core pattern:** one broken item must not abort the batch, and
  one broken plugin must not take down the app. Background loops, discovery, per-event
  ingestion, and per-plugin hooks all wrap the unit of work in `try/except` and continue
  (see `PluginManager.discover()` and the savepoint-per-event in `_run_datasource_tick`).
- User-facing validation raises `ValueError` with a clear, stable message — several are
  asserted verbatim in tests. Never change an existing error string casually.

### Async and blocking work

The app runs a single event loop for every visitor. **No blocking I/O on the event loop**:

- Route handlers that write to the database or do real work are either plain `def`
  (FastAPI runs them in a worker thread) or `async def` that pushes the blocking part
  into `asyncio.to_thread(...)`.
- Background loops run their tick bodies via `asyncio.to_thread` and sleep between runs.
- Long batches commit periodically (see `EVENTS_COMMIT_EVERY`) so the SQLite write lock
  is not held for the whole batch.

### Database

- **SQLAlchemy ORM only.** No SQL string interpolation; the rare `text()` usage is
  limited to controlled schema statements in `app/database/`.
- **JSON columns require reassignment.** Plain `JSON` columns do not track in-place
  mutation: `obj.data = {**obj.data, "key": value}` — never `obj.data["key"] = value`.
- Schema changes go through an Alembic migration in `backend/migrations/versions/`
  (copy the style of the most recent migration). Settings keys (`plugin.<id>.<key>`,
  `ui.<page>.<name>`) and existing schema are upgrade-contracts: never rename them.
- Queries against `events` must stay on indexed columns and bounded time windows — the
  reference installation is a multi-hundred-MB SQLite database. If a new query pattern
  needs an index, add it in the same PR.
- Secrets are stored encrypted transparently via `app/core/secrets.py`; always read
  settings through `get_setting_value()` and friends.

## Architecture rules

- **The core never imports from `plugins/`.** Plugins import from `app.*`. Where the core
  needs plugin behavior, it goes through the plugin manager, registries, or the hooks in
  `app/plugins/base.py`. See ADR-044 and `docs/adr/` in general — ADRs are binding.
- Plugins are packages: `plugins/<name>/__init__.py` + `plugin.py`, optional `routes.py`,
  `templates/`, `locales.py`, `services/`. Plugin API version is `2`.
- Plugin hooks return **data, not markup or code**: widget/view/rule contributions are
  validated descriptors rendered by core templates.
- Layering: `app/core` (no web/plugin imports) → `app/services` (domain logic) →
  `app/web` (shared web helpers) → `app/api` (routes) → `plugins/`.

## Templates and frontend

- Server-rendered Jinja2 with Tailwind utility classes; htmx and Alpine.js for the small
  interactive parts. **No new frontend frameworks or vendor dependencies** without prior
  discussion.
- Autoescaping stays on. **Never use `|safe`** on data that is not a compile-time
  constant. JS hooks use `data-*` attributes, not inline event handlers.
- Browser navigation and redirects only target internal paths. Validate them with
  `is_safe_local_path`, `safe_local_path`, or `safe_local_redirect_target` from
  `app.web.redirects`; these helpers also reject backslashes that browsers can normalize
  into path separators.
- Every user-visible string goes through `t("...")` with locale entries in **both**
  `backend/app/locales/en.py` and `de.py` (or the plugin's `locales.py`). A PR that adds
  UI text in only one language is incomplete.

## Tests

- Pytest, plain functions, in-memory SQLite via the `db_session` fixture in
  `backend/tests/conftest.py`. Plugin modules are loaded through
  `import_plugin_module(...)` from the same conftest — no `sys.path` tricks.
- Test names describe behavior: `test_ban_unban_reban_within_window_keeps_both_bans_as_distinct_events`.
- Test behavior, not implementation: assert stored rows, rendered values, raised
  messages. Monkeypatch at module boundaries (network, subprocess, plugin manager).
- Bug fixes come with a regression test that fails before the fix.

## Commits and pull requests

- Commit subjects are lowercase with a prefix: `feat:`, `fix:`, `change:`, `enhance:`,
  `docs:` — e.g. `fix: reject action types without an owning plugin`.
- One focused change per PR; no drive-by refactoring or reformatting of untouched code.
- User-facing changes update the website docs (`website/guide/`) in the same PR;
  architectural changes check the relevant ADR (`docs/adr/`).
- The PR description states which checks were run (pytest, pyright, manual smoke) and
  their results.

## For AI coding agents

Everything above applies. Additionally, follow the behavior rules in
[`agents/AGENTS.md`](https://github.com/konkos1/OpenSecDash/blob/main/agents/AGENTS.md)
— they govern how agents work in this repository (questions before changes, ADR
compliance, minimal diffs, honest test reporting).
