# Release Checklist

OpenSecDash publishes Docker images from version tags. The Git tag is the release version source of truth; `backend/pyproject.toml` intentionally stays at `0.0.0`.

## Version format

Use semantic version tags prefixed with `v`:

```text
v0.1.0
v0.2.0
v1.0.0
```

The Docker publish and GitHub Release workflows run for tags matching:

```text
v*.*.*
```

## Before tagging

1. Make sure the working tree is clean.
2. Run tests and type checks:

```bash
cd backend
uv run pytest -q
uv run pyright ../backend/app ../backend/tests ../plugins
```

3. Check dependencies/security status when appropriate:

```bash
cd backend
uv lock --upgrade --dry-run
uv export --format requirements-txt --all-groups --no-hashes > /tmp/opensecdash-requirements.txt
uvx pip-audit --strict --desc off -r /tmp/opensecdash-requirements.txt
```

4. Update `README.md` or docs if behavior changed.
5. Confirm Docker builds locally if packaging changed:

```bash
docker build -f docker/Dockerfile -t opensecdash:local .
docker run --rm -p 8765:8000 -v opensecdash-data:/data opensecdash:local
```

Open <http://localhost:8765> and verify `/health`.

## Create a release tag

```bash
git tag v0.1.0
git push origin v0.1.0
```

The GitHub Actions Docker publish workflow derives `0.1.0` from the tag, passes it into the image as `OPENSECDASH_VERSION`, and builds/pushes:

```text
konkos1/opensecdash:v0.1.0
konkos1/opensecdash:0.1.0
konkos1/opensecdash:latest
```

The Release workflow publishes a GitHub Release for the same tag. Release notes are generated from pull requests associated with commits between the previous version tag and the new tag, not from a full commit list. Each entry includes the PR number, title, and contributor.

## After publishing

1. Verify the generated GitHub Release notes.
2. Verify the Docker image can be pulled and started:

```bash
docker pull konkos1/opensecdash:v0.1.0
docker run --rm -p 8765:8000 -v opensecdash-data:/data konkos1/opensecdash:v0.1.0
```
