# ADR-011: Repository

> **Implementation status (2026-07-09):** Partially implemented.
> The repository broadly follows the planned structure, but the current implementation uses package-style plugins under `plugins/`, website docs under `website/`, and Docker assets under `docker/`.



Structure:

```none
opensecdash/

├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── database/
│   │   ├── models/
│   │   ├── plugins/
│   │   ├── services/
│   │   ├── templates/
│   │   ├── static/
│   │   └── main.py
│   │
│   ├── migrations/
│   ├── tests/
│   ├── pyproject.toml
│   └── uv.lock
│
├── plugins/
│   ├── crowdsec/
│   ├── traefik/
│   ├── geoblock/
│   ├── torblock/
│   ├── assets/
│   ├── github_release/
│   └── mqtt/
│
├── docs/
│   ├── adr/
│   ├── architecture/
│   └── screenshots/
│
├── docker/
│   ├── Dockerfile
│   └── docker-compose.example.yml
│
├── scripts/
│
├── tests/
│
├── .github/
│   └── workflows/
│
├── README.md
├── LICENSE
└── .gitignore
```

---

## Implementation notes (2026-07-09)

The current repository differs from the original sketch in several places:

```none
plugins/<name>/
```

are Python packages with `__init__.py` and `plugin.py`.

Documentation for the public website lives under:

```none
website/
```

Docker assets live under:

```none
docker/
```

The Docker build context remains the repository root so the image can copy `backend/`, `plugins/`, `README.md`, and `LICENSE`. Automation therefore uses `docker/Dockerfile` explicitly.

