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

Run the docs website locally:

```bash
cd website
npm install
npm run docs:dev
```

Before contributing, read the repository's `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and CLA notes.
