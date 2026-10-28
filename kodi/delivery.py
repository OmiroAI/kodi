"""Frozen dispatch intent and bounded, fail-closed delivery evidence."""
import re

TYPES = {'IMPLEMENTATION', 'DESIGN', 'DOCUMENTATION', 'CONFIGURATION', 'BENCHMARK'}


def requested_type(requirements):
    values = re.findall(r'^Delivery type:[ \t]*(.*)$', requirements, re.M | re.I)
    values = {value.strip().upper() for value in values}
    if len(values) > 1 or (values and not values <= TYPES):
        raise ValueError('Clarify Delivery type: choose one supported scope')
    return next(iter(values), 'IMPLEMENTATION')


def context(contract):
    if not contract:
        return ''
    return ('\n\nFrozen delivery contract:\n' + str(contract) +
        '\nThis dispatch scope overrides historical Backlog/planning-only scheduling text. '
        'Do the requested work; conflicting scope requires a specific blocker. '
        'Never downgrade task type or substitute a proposal. For IMPLEMENTATION return '
        'delivery_status="implemented" and behavioral_evidence as a nonempty list of '
        'concrete changed behavior and focused regression/failure-path checks. '
        'Reviewers must independently verify those claims against the complete source diff '
        'and return delivery_met=true only when the requested scope is delivered. '
        'Documentation, configuration and benchmark scopes are judged by their declared requirements. '
        'Evaluate technical acceptance now; your own completed verdict, downstream CI and '
        'human approval are not prerequisites for source review; they remain separate gates.')


def rejection(contract, author):
    if not contract or contract['task_type'] != 'IMPLEMENTATION':
        return None
    if author.get('task_type', 'IMPLEMENTATION') != contract['task_type']:
        return 'Worker cannot change frozen task type'
    if author.get('delivery_status') != 'implemented':
        return 'Implementation deferred or unconfirmed: implement requirements or report a specific blocker'
    evidence = author.get('behavioral_evidence')
    if (not isinstance(evidence, list) or not evidence or
            any(not isinstance(item, str) or not item.strip() for item in evidence)):
        return 'Implementation requires concrete behavioral and regression evidence; baseline tests alone are insufficient'
    if re.search(r'\b(implementation (?:was |is )?deferred|planning only|proposal.only|no.op|no (?:code changes|durable lesson))\b',
                 str(author.get('summary', '')), re.I):
        return 'Deferred, proposal-only or no-op outcome cannot complete implementation'
    return None
