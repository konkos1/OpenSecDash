# Project information

## Contributing

OpenSecDash welcomes contributions. Before contributing, read the repository files:

- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `docs/CLA.md`

## Contributor License Agreement

The project uses a Contributor License Agreement (CLA). Pull requests should confirm that the contributor agrees to the CLA. The CLA text lives in `docs/CLA.md` in the repository.

## Security reports

Please do not report security vulnerabilities in public issues. Use the instructions in `SECURITY.md`.

## Releases

Release preparation is documented in `docs/RELEASE.md`.

The Git tag is the release version source of truth. For a tag such as `v0.2.0`, the Docker publish workflow derives `0.2.0`, passes it into the container as `OPENSECDASH_VERSION`, and publishes matching Docker tags. `backend/pyproject.toml` intentionally stays at `0.0.0`.

Release notes are generated from pull requests associated with the tagged changes. The notes list PR number, title, and contributor instead of dumping every commit.

## Project status

OpenSecDash is an early-stage OSS/homelab project. APIs, plugin behavior, and UI details may still change before a stable release.
