# Omiro development system

Use the smallest clear change that satisfies the task. Preserve the fixed
capability boundaries: coding/review workers cannot merge, publish, deploy, access production,
change workflow permissions, or grant themselves new access.

The owner authorizes the deterministic coordinator to request GitHub's protected
auto-merge only for an operator-enabled repository after human approval and
exact-commit independent QA, different-model review and required GitHub checks.
It must serialize updates/merges, revalidate newer bases and never bypass
protection, self-approve, or deploy to production.

Keep the coordinator deterministic. Use existing Kandev capabilities for UI.
Do not add another agent framework, web dashboard, Kubernetes or microservices.
Every ready PR needs exact-commit independent QA, a different-model review,
manual testing instructions, passing GitHub checks and human merge approval.

Run `python3 -m unittest discover -s tests -v` for coordinator changes.
Never read or include credential files in a diff, prompt, report or test fixture.

## Public-source privacy boundary

Treat every tracked file and commit as potentially public. Keep this coordinator
repository reusable and organization-agnostic. Do not commit internal network
architecture, machine/device names, mesh domains, private/shared IP addresses,
personal home paths, private repository inventories, product plans or behavior,
customer data, operational transcripts, private deployment recipes, or credentials.
This includes documentation, examples, fixtures, generated files, screenshots,
commit messages, PR descriptions and logs copied into the repository.

Use fictional organization/repository names, reserved example domains and
RFC 5737 documentation addresses in examples. Provider names, public dependency
pins, public project branding, license ownership and intentional maintainer
routing metadata are allowed. Do not replace one real deployment identifier with
another. Put operator-specific config, QA profiles and deployment integrations
in private storage outside Git; preserve private operations before removing them.
Keep runtime behavior configurable and retain the existing capability boundaries.

Run `python3 scripts/check_public_source.py` before committing and review the diff
for disclosures the automated check cannot recognize. A clean working tree does
not sanitize old commits, tags, PRs or issues. Never change repository visibility,
force-push rewritten history or publish a history-bearing clone without explicit
owner authorization. Prepare a history-free export for a separate public release.
