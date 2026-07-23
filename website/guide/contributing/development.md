# Development

Install backend dependencies and run checks:

```bash
cd backend
uv lock --check
uv sync --python "$(cat .python-version)" --frozen --group dev
.venv/bin/python -m pytest tests/ -q
.venv/bin/pyright --pythonversion "$(cut -d. -f1,2 .python-version)" app tests ../plugins
```

The exact Python patch version in `backend/.python-version` is shared by local
development, CI, release validation, and the production image. `uv.lock` supplies the
same application dependencies to development and production; development additionally
installs the tools from the `dev` dependency group.

Run the app locally:

```bash
cd backend
uv run --frozen uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

To test the Docker image from your working tree, replace `image: konkos1/opensecdash:latest` in your `docker-compose.yml` with `build: { context: ., dockerfile: docker/Dockerfile }` and run `docker compose up -d --build`.

Run the docs website locally:

```bash
cd website
npm ci
npm run audit:ci
npm run docs:dev
```

The documentation dev and preview servers bind to loopback. The build audit checks
production dependencies separately and permits a high/critical development finding
only when `audit-allowlist.json` names the advisory, explains its scope, and has a
future expiry date. New or expired findings fail CI.

Release images are built twice without a dependency cache. CI compares their complete
Python package lists, checks FastAPI/Uvicorn/WebSockets against `uv.lock`, audits the
locked Python runtime and npm build dependencies, generates an SPDX SBOM, and blocks
publication on fixable high or critical image findings. A temporary vulnerability
exception must name each CVE/advisory, explain why it is not fixable, limit the affected
scope, and include an expiry date; exceptions must be reviewed in the workflow rather
than implemented by globally hiding scanner findings.

The publish workflow also runs the complete backend/security suite, Pyright, Alembic,
Tailwind, and the documentation build before exercising Fresh, Small, Large, and
Upgrade profiles inside the release-candidate image. The profile JSON reports enforce
the documented readiness and search thresholds and are retained with the SBOM and scan
reports. Local profile commands live in `backend/tests/performance/README.md`; they use
temporary SQLite databases and must never be pointed at a development database.

Before contributing, read the repository's `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and CLA notes.

If you want to add a new integration, see [Plugin development](./plugin-development.md). For heuristic web-probe detections, see [Contributing insight rules](./insight-rules.md). For UI languages, see [Translations](./translations.md).
