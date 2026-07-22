# Development

Install backend dependencies and run checks:

```bash
cd backend
uv lock --check
uv sync --frozen --group dev
.venv/bin/python -m pytest tests/ -q
.venv/bin/pyright --pythonversion 3.13 app tests ../plugins
```

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

Before contributing, read the repository's `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and CLA notes.

If you want to add a new integration, see [Plugin development](./plugin-development.md). For heuristic web-probe detections, see [Contributing insight rules](./insight-rules.md). For UI languages, see [Translations](./translations.md).
