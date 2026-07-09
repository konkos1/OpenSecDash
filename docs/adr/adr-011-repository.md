# ADR-011: Repository

> **Implementation status (2026-07-09):** Partially implemented.
> The repository broadly follows the planned structure, but the current implementation uses package-style plugins under `plugins/`, website docs under `website/`, and no separate `docker/` directory.



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
│   └── docker-compose.yml
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

The current root contains the Dockerfile and compose example directly instead of a separate `docker/` directory.

