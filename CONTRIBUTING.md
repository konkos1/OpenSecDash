# Contributing to OpenSecDash

Thank you for considering a contribution to OpenSecDash. The project is built for homelab enthusiasts, and practical feedback from real setups is extremely valuable.

## Good ways to contribute

- Report bugs with clear reproduction steps.
- Improve documentation and setup examples.
- Add or improve tests.
- Build datasource plugins, for example for Nginx, Caddy, Authelia, Authentik, firewall logs, or other homelab tools.
- Improve UI accessibility and responsive behavior.
- Add carefully scoped insights/correlation rules.
- Help with Docker and deployment examples.

## Before opening a pull request

1. Keep changes focused. One feature or fix per PR is much easier to review.
2. Add or update tests when behavior changes.
3. Run the local checks:

```bash
cd backend
uv run pytest -q
uv run pyright ../backend/app ../backend/tests ../plugins
```

4. Update documentation when user-facing behavior changes.
5. Confirm the CLA in the pull request template.

## Contributor License Agreement

OpenSecDash uses a lightweight CLA. By contributing, you confirm that you are allowed to submit your work and that it may be distributed as part of OpenSecDash under the project license.

See [docs/CLA.md](docs/CLA.md).

## Plugin development

OpenSecDash is plugin-first. A plugin can provide capabilities such as:

- `datasource`
- `enrichment`
- `action`
- `export`
- `page`
- `widget`
- `insight`

Plugins define metadata, settings, capabilities, and optional translations. The core app handles storage, settings UI, diagnostics, and lifecycle wiring.

If you want to add a datasource plugin, start by looking at the existing plugins in [`plugins/`](plugins/), especially `traefik_log` and `apps_inventory`.

## Security-related contributions

Please do not open public issues for vulnerabilities. Follow [SECURITY.md](SECURITY.md) instead.

## Code style

There is no heavy style process yet. Prefer:

- readable Python with type hints where helpful
- small functions around testable behavior
- explicit settings and plugin capabilities
- tests for important decisions and bug fixes

## Pull request review

Maintainers may ask for changes to keep the project maintainable, safe for homelabs, and aligned with the plugin-first architecture. Please do not take review comments personally; they are part of keeping a security-focused project healthy.
