"""Disabled-by-default routing from private operator attestations, never prompts.

Cheap classifications attest the complete committed patch digest, not filenames.
The operator must record structural criteria after inspecting the whole change.
"""
import hashlib
import json
import re

MODELS = {
    ('gemini', 'provider default'): ('google', True),
    ('antigravity', 'gemini-4-argon-high'): ('google', True),
    ('antigravity', 'gemini-3-flash'): ('google', True),
    ('antigravity', 'gemini-3.8-flash-low'): ('google', True),
    ('qwen', 'Qwen3.5-397B-A17B'): ('qwen', False),
    ('qwen', 'qwen3.8-27b'): ('qwen', False),
}
CLASSES = ('docs', 'low_complexity', 'stateful', 'concurrent', 'cross_component', 'unknown')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def select(config, patch, assets, sha, author_family='openai'):
    enabled = config.get('review_routing_enabled', False)
    if type(enabled) is not bool:
        raise ValueError('review_routing_enabled must be boolean')
    if not enabled:
        return None
    policy = config.get('review_routing_policy')
    evidence = config.get('review_routing_evidence')
    if not isinstance(policy, dict) or not isinstance(evidence, dict):
        raise ValueError('Routing requires operator policy and benchmark evidence')
    routes = policy.get('routes', {})
    if set(routes) != set(CLASSES):
        raise ValueError('Routing requires all conservative and classified routes')
    if not isinstance(policy.get('id'), str) or not policy['id']:
        raise ValueError('Routing policy identity required')
    if (evidence.get('policy_digest') != digest(policy) or
            evidence.get('approved') is not True or evidence.get('quality_passed') is not True or
            not evidence.get('id') or not evidence.get('operator') or
            not evidence.get('historical_reference')):
        raise ValueError('Validated operator benchmark evidence required')
    # Evidence must include measured, comparable historical results for every route.
    results = evidence.get('results', [])
    for name, route in routes.items():
        validate_route(route, author_family, assets)
        if route['provider'] == 'qwen' and not all(config.get(k) for k in ('qwen_url','qwen_env')):
            raise ValueError('Qwen route requires existing operator transport configuration')
        matches = [r for r in results if r.get('route') == route and r.get('class') == name]
        if len(matches) != 1:
            raise ValueError('Missing or ambiguous benchmark result')
        result = matches[0]
        if (not re.fullmatch('[0-9a-f]{40}', str(result.get('candidate_sha', ''))) or
                not result.get('baseline') or not result.get('context_digest') or
                any(type(result.get(k)) is not int or result[k] < 0
                    for k in ('validated_findings', 'missed_findings', 'failures')) or
                any(result.get(k) is not None and (type(result[k]) is not int or result[k] < 0)
                    for k in ('input_tokens', 'output_tokens'))):
            raise ValueError('Invalid benchmark measurements')
    fallback = policy.get('fallback')
    if fallback is not None:
        validate_route(fallback, author_family, assets)
        if fallback['provider'] != 'qwen' or not all(config.get(k) for k in ('qwen_url','qwen_env')):
            raise ValueError('Only configured Qwen quota fallback is supported')
        if fallback not in routes.values():
            raise ValueError('Fallback requires benchmarked route')
    patch_digest = hashlib.sha256(patch.encode()).hexdigest()
    classification = 'unknown'
    criteria = policy.get('changes', {}).get(patch_digest)
    if criteria:
        classification = criteria.get('class')
        if classification not in CLASSES or not criteria.get('reason'):
            raise ValueError('Invalid operator change criteria')
        if classification in ('docs', 'low_complexity'):
            # Every structural risk must explicitly be absent in the attestation.
            if any(criteria.get(k) is not False for k in ('stateful','concurrent','cross_component','executable' if classification == 'docs' else 'ambiguous')):
                classification = 'unknown'
    override = config.get('review_routing_override', False)
    if type(override) is not bool:
        raise ValueError('review_routing_override must be boolean')
    name = 'unknown' if override else classification
    return dict(candidate_sha=sha, policy_id=policy['id'], policy_digest=digest(policy),
                evidence_id=evidence['id'], evidence_digest=digest(evidence),
                change_digest=patch_digest, classification=classification,
                reason='operator_override' if override else 'verified_change' if criteria else 'unknown_change',
                primary=routes[name], fallback=fallback)


def validate_route(route, author_family, assets):
    if not isinstance(route, dict) or set(route) != {'provider', 'model'}:
        raise ValueError('Route requires provider and pinned model')
    capability = MODELS.get((route['provider'], route['model']))
    if not capability or capability[0] == author_family:
        raise ValueError('Unsupported or same-family reviewer model')
    if assets and not capability[1]:
        raise ValueError('Asset route requires tool capability')
