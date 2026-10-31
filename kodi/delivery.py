"""Frozen dispatch intent and bounded, fail-closed delivery evidence."""
import re
import json

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
    if not isinstance(author, dict):
        return 'Author report must be a JSON object'
    if re.search(r'\b(implementation (?:was |is )?deferred|planning only|proposal.only|no.op|no (?:code changes|durable lesson))\b',
                 str(author.get('summary', '')), re.I):
        return 'Implementation deferred, proposal-only or no-op outcome cannot complete implementation'
    invalid = [name for name in report_fields(contract, author)
               if name in ('delivery_status', 'behavioral_evidence')]
    if invalid:
        return 'Missing or malformed author report fields: ' + ', '.join(invalid)
    if author.get('task_type', 'IMPLEMENTATION') != contract['task_type']:
        return 'Worker cannot change frozen task type'
    if author.get('delivery_status') != 'implemented':
        return 'Implementation deferred or unconfirmed: implement requirements or report a specific blocker'
    evidence = author.get('behavioral_evidence')
    if (not isinstance(evidence, list) or not evidence or
            any(not isinstance(item, str) or not item.strip() for item in evidence)):
        return 'Implementation requires concrete behavioral and regression evidence; baseline tests alone are insufficient'
    return None


AUTHOR_SCHEMA = {
    'summary': 'what changed',
    'test_steps': ['simple manual test step'],
    'acceptance_evidence': ['how a requirement is satisfied'],
    'delivery_status': 'implemented',
    'behavioral_evidence': ['concrete changed behavior and focused regression checks'],
}


def author_contract(contract=None):
    schema = dict(AUTHOR_SCHEMA)
    if contract and contract['task_type'] != 'IMPLEMENTATION':
        schema['delivery_status'] = 'completed'
        schema['behavioral_evidence'] = ['evidence for the declared scope']
    return ('Return ONLY JSON using this schema: ' + json.dumps(schema) +
            '. Report blockers honestly; never invent implementation evidence. ' +
            'For learning-only no-change use no_change=true, no_change_reason and acceptance_evidence=[].')


def report_fields(contract, author):
    """Allowlisted field diagnostics, never arbitrary worker text."""
    if not isinstance(author, dict):
        return ['report']
    fields = []
    for name in ('test_steps',):
        value = author.get(name)
        valid = isinstance(value, str) and bool(value.strip()) if name == 'summary' else (
            isinstance(value, list) and bool(value) and
            all(isinstance(item, str) and item.strip() for item in value))
        if not valid:
            fields.append(name)
    if contract and contract['task_type'] == 'IMPLEMENTATION':
        if not isinstance(author.get('delivery_status'), str) or not author['delivery_status'].strip():
            fields.append('delivery_status')
        value = author.get('behavioral_evidence')
        if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x.strip() for x in value):
            fields.append('behavioral_evidence')
    return fields


def report_repair_eligible(contract, author):
    if not contract or contract['task_type'] != 'IMPLEMENTATION' or not isinstance(author, dict):
        return False
    if author.get('delivery_status', 'implemented') != 'implemented':
        return False
    if author.get('task_type', 'IMPLEMENTATION') != 'IMPLEMENTATION':
        return False
    if any(author.get(key) for key in ('no_change', 'blocker', 'blockers', 'needs_input', 'permission_request')):
        return False
    text = json.dumps(author)
    return not re.search(r'deferred|proposal|planning only|no.code|no.op|permission|missing.*(?:input|requirement)|need.*clarif', text, re.I)
