# Website deployment

The website is built and deployed by the GitHub Actions `Website` workflow.

The workflow runs:

```bash
npm ci
npm run docs:build
npx --yes wrangler@latest deploy
npm run indexnow:submit
```
