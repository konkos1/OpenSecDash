# GitHub Branch Ruleset

This project uses GitHub Rulesets rather than classic branch protection where possible.

A suggested ruleset for `main` is stored at:

```text
.github/rulesets/main.json
```

GitHub does **not** automatically apply rulesets from files in the repository. The file is documentation and an import/apply template.

## What the ruleset does

The `Protect main` ruleset applies to:

```text
refs/heads/main
```

It requires:

- pull requests before changes reach `main`
- at least 1 approving review
- stale approvals dismissed after new commits
- all review conversations resolved
- required status check: `Backend tests`
- branch up to date before merge
- linear history
- no branch deletion
- no force pushes
- no bypass actors by default

## Apply in GitHub UI

1. Open the repository on GitHub.
2. Go to **Settings → Rules → Rulesets**.
3. Click **New ruleset → New branch ruleset**.
4. Use the values from `.github/rulesets/main.json`:
   - Name: `Protect main`
   - Enforcement: `Active`
   - Target branches: `main`
   - Bypass list: empty
5. Enable rules:
   - Restrict deletions
   - Block force pushes
   - Require linear history
   - Require a pull request before merging
   - Required approvals: `1`
   - Dismiss stale approvals
   - Require conversation resolution
   - Require status checks to pass
   - Require branches to be up to date before merging
6. Select the status check after it has run at least once:

```text
Backend tests
```

If GitHub shows a slightly different check name, select the check produced by `.github/workflows/tests.yml` / `Backend tests`.

## Apply with GitHub CLI

If `gh` is installed and authenticated with admin access to the repository:

```bash
gh api \
  --method POST \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  /repos/konkos1/OpenSecDash/rulesets \
  --input .github/rulesets/main.json
```

To preview existing rulesets:

```bash
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  /repos/konkos1/OpenSecDash/rulesets
```

To update an existing ruleset, first find its `id`, then use:

```bash
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  /repos/konkos1/OpenSecDash/rulesets/RULESET_ID \
  --input .github/rulesets/main.json
```

## Notes

- Required status checks can only be selected after they have run at least once.
- Keep Docker publishing separate from PR checks. It should run only on release tags or manually.
- For a young OSS project, 1 required approval is a good balance between safety and low friction.
