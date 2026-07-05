# Website deployment

The website is built and deployed by the GitHub Actions `Website` workflow.

Required GitHub repository secrets:

| Secret | Purpose |
| --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID used by Wrangler. |
| `CLOUDFLARE_API_TOKEN` | API token for deploying the Worker/static assets. |
| `INDEXNOW_KEY` | Optional IndexNow key file and submit key. |

The workflow runs:

```bash
npm ci --no-audit
npm run docs:build
npx --yes wrangler@latest deploy
npm run indexnow:submit
```

IndexNow submission uses `https://api.indexnow.org/indexnow` by default - the spec's generic endpoint, which forwards submissions to Bing and all other IndexNow partners. If the provider rejects a submission with `403` even though the key file is reachable, the script logs the rejection and does not fail the deployment.
