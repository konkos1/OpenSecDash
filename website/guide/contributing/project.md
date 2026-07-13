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
[`.agents/AGENTS.md`](https://github.com/konkos1/OpenSecDash/blob/main/.agents/AGENTS.md)
and make sure the AI tool receives and follows those instructions. They apply to all
AI-assisted work in this repository in addition to the other contribution guidelines.

## Contributor License Agreement

The project uses a Contributor License Agreement (CLA). Pull requests should confirm that the contributor agrees to the CLA. The CLA text lives in `docs/CLA.md` in the repository.

## Security reports

Please do not report security vulnerabilities in public issues. Use the instructions in `SECURITY.md`.

## Releases

Release preparation is documented in `docs/RELEASE.md`.

The Git tag is the release version source of truth. For a tag such as `v0.2.0`, the Docker publish workflow derives `0.2.0`, passes it into the container as `OPENSECDASH_VERSION`, and publishes matching Docker tags. `backend/pyproject.toml` intentionally stays at `0.0.0`.

Release notes are generated from pull requests associated with the tagged changes. The notes list PR number, title, and contributor instead of dumping every commit.

## Code style and review

There is no heavy style process yet. Prefer readable Python, type hints where helpful, small testable functions, explicit settings/capabilities, and tests for important decisions or bug fixes.

Maintainers may ask for changes to keep the project maintainable, safe for homelabs, and aligned with the plugin-first architecture. Please do not take review comments personally; they are part of keeping a security-focused project healthy.

## Project status

OpenSecDash is an early-stage OSS/homelab project. APIs, plugin behavior, and UI details may still change before a stable release.
