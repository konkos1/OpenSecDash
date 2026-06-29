# Release Checklist

OpenSecDash publishes Docker images from version tags.

## Version format

Use semantic version tags prefixed with `v`:

```text
v0.1.0
v0.2.0
v1.0.0
```

The Docker publish workflow runs for tags matching:

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
docker build -t opensecdash:local .
docker run --rm -p 8765:8000 -v opensecdash-data:/data opensecdash:local
```

Open <http://localhost:8765> and verify `/health`.

## Create a release tag

```bash
git tag v0.1.0
git push origin v0.1.0
```

The GitHub Actions Docker publish workflow will build and push:

```text
konkos1/opensecdash:v0.1.0
konkos1/opensecdash:latest
```

## Docker Hub prerequisites

Configure these GitHub Actions secrets:

```text
DOCKERHUB_USERNAME
DOCKERHUB_TOKEN
```

Use a Docker Hub access token, not your Docker Hub password.

## After publishing

1. Create a GitHub Release from the tag.
2. Add a short changelog:
   - new features
   - fixes
   - security updates
   - breaking changes, if any
3. Verify the Docker image can be pulled and started:

```bash
docker pull konkos1/opensecdash:v0.1.0
docker run --rm -p 8765:8000 -v opensecdash-data:/data konkos1/opensecdash:v0.1.0
```
