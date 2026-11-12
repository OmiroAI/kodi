# Prerequisites and retained repository ownership

A `Dependency: <external_id>` line can identify an existing queued job by its
exact external identity (for example, its issue URL). The job must belong to the
same repository. Titles, queue numbers and cross-repository jobs do not establish
this relationship. Other dependency identities retain the operator readiness
catalog behavior; repository onboarding checks still apply.

The dependent waits until the identified job is done. Its status shows the
prerequisite identity, task number and current state. Cycles block admission.
An idle blocked or queued dependent can yield retained repository ownership to
its prerequisite, including a prerequisite reached through other verified task
links. It retains its checkout, branch, candidate, evidence and attempt budgets.
Unrelated work cannot use this exception.

Running invocations, unreleased worker capacity (including expired leases),
uncertain remote mutations and retained delivery reservations prevent yielding.
Active stage states cannot yield. Ready routing, repository enablement, stage
and provider capacity, and exclusive delivery gates continue to apply.

To check manually in a disposable queue, retain a blocked dependent with a
candidate and checkout, and give it a Dependency line naming a Ready prerequisite
in the same enabled repository. Confirm the prerequisite starts, the dependent's
status names the prerequisite and its retained files and attempts remain intact.
Repeat with an unrelated Ready task, a dependency cycle, a non-Ready prerequisite,
and a dependent with a running invocation or unreconciled mutation: confirm each
remains blocked. Do not use the check to resume operational jobs.
