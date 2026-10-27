# Explicit Codex author profiles

Operators own `coding_profiles`, `default_coding_profile` and
`recovery_coding_profile` in private runtime configuration. The example defines
Sol (`gpt-6.1-sol`), Terra (`gpt-6.1-terra`) and Luna (`gpt-6-luna`) at medium
effort. Selection is static; this change implements no automatic routing.
`model` is an exact OpenAI ID, `effort` is explicit, and `supported_efforts` is the
operator's verified model/worker compatibility list. Validate availability for
the configured account and installed worker image before enabling a profile;
syntax validation cannot establish provider access. Unknown/non-OpenAI IDs,
missing selections and efforts outside the declared supported combinations fail
before a worker dispatch. Luna does not accept ultra effort.

The existing evidence store records immutable job selections under
`coding_profile` and `recovery_profile`, without schema changes. Initial coding
and ordinary repairs reuse the default snapshot; both rebase conflict repairs
and rebased candidate repairs reuse the recovery snapshot. Transport retries and
restarts retain the snapshot even if runtime configuration changes. To change an
existing job's model deliberately, use a new job after operator inspection.

Configurations without `coding_profiles` retain legacy provider-default author
behavior, recorded with profile `legacy` and unknown requested model/effort.
This compatibility path is not an explicit profile and cannot enable routing:
`coding_routing_enabled` with missing profiles fails. Historical usage rows remain
readable with no invented provenance. Explicit profiles are required before any
future routing activation. All profiles here are OpenAI; changing Sol to Terra or
Luna never supplies a different-family review. Existing independent QA, review,
GitHub checks and human approval remain required for the exact candidate.

Each invocation's usage JSON includes `author_provenance`: profile, requested
model, effort, resolved model, harness version and family. Its ledger phase is
`coding`, `repair`, `rebase_conflict_repair` or `rebased_candidate_repair`.
Failed/timeout runs retain provenance too. Resolved model remains null unless
Codex reports model metadata; the requested model is never used as a substitute.
The worker image is probed with `codex --version`; unavailable version metadata
remains null. Operators may set `codex_harness_version` in private configuration
to the version available in their approved worker image. Each invocation records
that declaration separately as `available_harness_version` (null when absent);
`harness_version` remains the actual probe result, even when it differs or is
unavailable. The declaration never substitutes for a successful probe.
Task status descriptions show persisted default and recovery profile selections,
including requested model and effort; legacy selections show unknown settings.
Existing `usage --tasks` and `usage --daily` expose `author_runs`
with these settings and outcomes. Tokens and provider totals keep their existing
semantics. No credentials are included in these fields.

CLI verification used installed `codex-cli 0.162.1`: `codex exec --help` accepts
`--model` and `--config key=value`. Author commands pass `--model <exact-id>` and
`--config 'model_reasoning_effort="medium"'`, alongside existing isolation flags.
The reasoning setting is documented in the
[official OpenAI configuration reference](https://developers.openai.com/codex/config-reference).
Host CLI verification does not establish the version or account capabilities of
the separately installed worker image. Runtime version probing records that
image's version rather than assuming the host version.

## Sandbox smoke test

1. Use a disposable checkout and private state with the normal isolated worker
   image, egress and dedicated login. Keep publication and merge disabled.
2. For each configured profile, select it as default and recovery in private
   configuration. Confirm its model and effort against the installed worker's
   supported model settings; maintain `supported_efforts` from that verification.
3. Dispatch a trivial file edit with a manual test instruction. Inspect local
   author events, usage provenance and `usage --tasks`: requested model, effort,
   profile and worker version must match. A missing reported resolved model must
   remain null. A provider rejection must block the job, never fall back silently.
4. In that disposable environment, exercise an ordinary QA repair, a content
   rebase conflict and a rebased candidate QA repair. Verify phases and recovery
   profile choices, retaining original checkouts. Restart/retry with changed
   config and confirm the recorded choice is retained.
5. Set an invalid effort or missing default/recovery name on a fresh job and
   confirm an actionable validation failure before author dispatch. Read a legacy
   ledger and confirm unknown metadata stays unknown.

Local mocked regression tests do not supply independent acceptance. Before a PR
is ready, the operator must obtain exact-commit independent QA, different-model
review, passing required GitHub checks and human approval. This files-only change
does not create a PR or grant publication or merge authority.
