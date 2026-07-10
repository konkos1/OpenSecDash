# Contributing to OpenSecDash

Thank you for considering a contribution to OpenSecDash. Practical feedback from real homelab setups is very valuable.

The detailed contributor documentation lives on the website:

- Development setup and local checks: https://opensecdash.app/guide/contributing/development
- Code style: https://opensecdash.app/guide/contributing/code-style
- Plugin development: https://opensecdash.app/guide/contributing/plugin-development
- Insight rules: https://opensecdash.app/guide/contributing/insight-rules
- Translations: https://opensecdash.app/guide/contributing/translations
- Project information: https://opensecdash.app/guide/contributing/project

## Quick checklist for pull requests

- Keep changes focused: one feature or fix per PR.
- Follow the [code style](https://opensecdash.app/guide/contributing/code-style) (source: `website/guide/contributing/code-style.md`).
- Add or update tests when behavior changes.
- Update website documentation for user-facing changes.
- Run the backend checks before opening a PR:

```bash
cd backend
uv run pytest -q
uv run pyright ../backend/app ../backend/tests ../plugins
```

- Confirm the CLA checkbox in the pull request template.

## Contributor License Agreement

OpenSecDash uses a lightweight CLA. By contributing, you confirm that you are allowed to submit your work and that it may be distributed as part of OpenSecDash under the project license.

See [docs/CLA.md](docs/CLA.md).

## Security issues

Please do not open public issues for vulnerabilities. Follow [SECURITY.md](SECURITY.md) instead.

## Code of Conduct

Please follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
