# Coding profile routing

Routing defaults off and uses the existing Codex login, worker image and proxy.
Private operator config may set `coding_routing` with `mode` (`off`, `shadow`,
`active`), `profiles`, `default` (`Sol`), `timeout` (1–120 seconds, default 30),
and `context_chars` (1–32000 characters, default 16000). Profiles must contain
exactly `Sol`, `Terra`, `Luna`, each with an exact provider `model` identifier
and `effort` (`low`, `medium`, `high`, `xhigh`). No model IDs or credentials are
implicitly installed. The configured Sol profile is the conservative default.
Off preserves the prior provider configuration. Shadow codes with Sol while
recording Luna's recommendation; active applies a validated route. Reviewer
selection and all protected-path and exact-commit acceptance gates remain intact.

Triage has a read-only source mount, bounded requirements/reference input and a
short independent runtime limit. It may inspect source through read-only tools.
Uncertain, invalid and unavailable results fall back once. Validated missing
business requirements block coding. Decisions, including failed triage and
escalation evidence, use the existing durable evidence store. Input, policy,
configuration or base changes invalidate reuse. Interrupted checkouts still
require operator inspection; this feature does not automatically resume them.
A substantive QA test failure or concrete blocking review finding escalates a
lower profile once on the next existing coding attempt, retaining its checkout
and budgets. Operational and ambiguous feedback leaves the profile unchanged.
QA escalation requires exit code 1 and a test-failure diagnostic; missing exit
status, container launch failures and timeouts do not qualify. Worker launch, transport,
authentication and quota failures never cause capability escalation. Recovery
uses Sol. Triage ledger rows use phase `triage`; legacy accounting exports map
unrecognized phases to `unknown` while retaining measured usage and time.

## Disposable live pilot

Use an isolated, disposable repository and private state/config with existing
operator credentials. Do not enable production routing. Prepare three synthetic
tickets: an explicit mechanical rename, a bounded implementation using an
existing pattern, and a cross-component stateful change. Add a ticket missing
business requirements. Record the requirements and base SHA, then run each
representative task separately with each configured profile in disposable state.
Run shadow and active routing separately, inspecting routing evidence before
coding and the applied-model task description afterward. Verify read-only triage
mounts and that the clarification ticket blocks. Do not publish or merge pilots.

For every task/profile record completion, elapsed time, measured input/output
usage (unknown when unavailable), retries, QA/review results and minutes of human
repair. Compare outcomes on identical requirements/base, retaining failures and
uncertainty rather than inferring model performance from names. Use these results
to evaluate the initial rules. Live model calls are never unit-test dependencies.
Independent exact-commit QA, different-model review, required GitHub checks and
human approval for this change remain pending operator acceptance.
