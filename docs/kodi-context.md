# Kodi repository context

Kodi is a deterministic delivery coordinator, not an agent framework. Read
AGENTS.md and docs/operations.md for capability boundaries and delivery gates.
The coding and review containers cannot publish, merge or deploy. Host-only
helpers use the scoped GitHub App. Local tests run with:

```
python3 -m unittest discover -s tests -v
```

PR acceptance also requires independent offline QA on the configured QA host, different-model
review, GitHub checks and human CODEOWNER approval at the candidate commit.
Keep runtime configurations, provider credentials and production outside source.

## Confirmed lessons

- GitHub check inspection uses paginated REST check runs and commit statuses.
  The installed CLI's GraphQL status rollup requests Actions workflow metadata,
  which exceeds the App's Checks-only grant. Do not add broader permissions to
  fix that query. Regression: paginated check-reader coverage.
  Verification: private-CI reader regression tests and independent acceptance.
- Review the entire candidate diff after repair, including earlier commits.
  Regression: complete-branch review coverage.
  Verification: multi-commit review-context regression coverage.
- Refresh canonical source with a fast-forward-only update; dirty, divergent or
  unexpected origins require operator maintenance. Never reset user changes.
  Regression: canonical-source validation coverage.
  Verification: canonical-source failure-path tests.
