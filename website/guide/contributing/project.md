# Project information

## Contributing

OpenSecDash welcomes contributions. Practical feedback from real homelab setups is especially valuable.

Good ways to contribute:

- Report bugs with clear reproduction steps.
- Improve documentation and setup examples.
- Add or improve tests.
- Build datasource plugins, for example for Nginx, Caddy, Authelia, Authentik, firewall logs, or other homelab tools.
- Improve UI accessibility and responsive behavior.
- Add carefully scoped insights/correlation rules.
- Help with Docker and deployment examples.

Before contributing, read the repository files:

- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `docs/CLA.md`

### Using AI tools

If you use an AI assistant or coding agent for a contribution, read
[`agents/AGENTS.md`](https://github.com/konkos1/OpenSecDash/blob/main/agents/AGENTS.md)
and make sure the AI tool receives and follows those instructions. They apply to all
AI-assisted work in this repository in addition to the other contribution guidelines.

## Contributor License Agreement

The project uses a Contributor License Agreement (CLA). Pull requests should confirm that the contributor agrees to the CLA. The CLA text lives in `docs/CLA.md` in the repository.

## Security reports

Please do not report security vulnerabilities in public issues. Use the instructions in `SECURITY.md`.

## Releases

Release preparation is documented in `docs/RELEASE.md`.

The Git tag is the release version source of truth. For a tag such as `v0.2.0`, the Docker publish workflow derives `0.2.0`, passes it into the container as `OPENSECDASH_VERSION`, and publishes matching Docker tags. `backend/pyproject.toml` intentionally stays at `0.0.0`.

Before Docker Hub publication, the release workflow audits the locked Python runtime
and website build dependencies, builds the image twice with cold dependency caches,
compares all installed Python package versions, verifies core runtime versions against
`uv.lock`, creates an SPDX SBOM, and scans OS and Python packages. Fixable high or
critical image findings block the push. The package lists, audit reports, image scan,
and SBOM are retained as workflow artifacts.

Build validation, supply-chain checks, and publication are separate jobs that exchange
the same short-lived release-candidate image artifact. The pinned SBOM generator is
retried once when it fails, its SPDX output is validated, and publication runs only
after the supply-chain gate passes. No alternate generator fallback is used.

Release notes are generated from pull requests associated with the tagged changes. The notes list PR number, title, and contributor instead of dumping every commit.

## Code style and review

Follow the [Code style](./code-style.md) guide for all contributions. Match the
surrounding code, keep changes focused, use type hints, and add regression tests for bug
fixes. Before opening a pull request, run the required checks:

```bash
cd backend
uv lock --check
uv sync --python "$(cat .python-version)" --frozen --group dev
.venv/bin/python -m pytest tests/ -q
.venv/bin/pyright --pythonversion "$(cut -d. -f1,2 .python-version)" app tests ../plugins
```

Both checks must pass without errors. If a check was not run, state that clearly in the
pull request description.

Maintainers may ask for changes to keep the project maintainable, safe for homelabs, and aligned with the plugin-first architecture. Please do not take review comments personally; they are part of keeping a security-focused project healthy.

## Project status

OpenSecDash now contains the feature scope planned for its first stable v1 release. It remains an independently maintained OSS/homelab project, and APIs, plugin behavior, or UI details can still change until a stable v1 tag is published. Later-scope work is intentionally tracked separately instead of expanding the v1 target indefinitely.
