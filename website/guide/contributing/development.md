# Development

Install backend dependencies and run checks:

```bash
cd backend
uv run pytest -q
uv run pyright ../backend/app ../backend/tests ../plugins
```

Run the app locally:

```bash
cd backend
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

To test the Docker image from your working tree, replace `image: konkos1/opensecdash:latest` in your `docker-compose.yml` with `build: .` and run `docker compose up -d --build`.

Run the docs website locally:

```bash
cd website
npm install
npm run docs:dev
```

Before contributing, read the repository's `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and CLA notes.

If you want to add a new integration, see [Plugin development](./plugin-development.md). For heuristic web-probe detections, see [Contributing insight rules](./insight-rules.md). For UI languages, see [Translations](./translations.md).
