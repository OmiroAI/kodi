"""Policy is operator-owned; issue text cannot grant capabilities."""
import re

SENSITIVE = re.compile(r"(^|/)(\.github|auth|security|migrations?|ota|firmware)(/|\.)|(^|/)(Dockerfile|docker-compose[^/]*|AGENTS\.md)$", re.I)
SECRET = re.compile(r"(^|/)(\.env(?:\..*)?|[^/]*\.(?:pem|key)|credentials[^/]*|secrets?)(/|$)", re.I)


def changed_paths_safe(paths):
    for path in paths:
        if not path or path.startswith('/') or '..' in path.split('/'):
            raise ValueError("Invalid changed path")
        if SECRET.search(path) and not path.endswith('.env.example'):
            raise ValueError("Secret-shaped file in changes")
        if SENSITIVE.search(path):
            raise ValueError("Sensitive change requires human design review: " + path)


def validate_review(value, author_family, reviewer_family):
    if author_family == reviewer_family:
        raise ValueError("Reviewer must use another model family")
    required = {'approved', 'criteria_met', 'simple', 'blocking_findings', 'summary'}
    if not isinstance(value, dict) or not required <= value.keys():
        raise ValueError("Incomplete independent review")
    if any(type(value[k]) is not bool for k in ('approved','criteria_met','simple')):
        raise ValueError("Review verdicts must be booleans")
    if 'delivery_met' in value and type(value['delivery_met']) is not bool:
        raise ValueError("Delivery verdict must be a boolean")
    if not isinstance(value['blocking_findings'], list) or not isinstance(value['summary'], str):
        raise ValueError("Invalid review findings")
    if not value['summary'].strip():
        raise ValueError("Review summary must be nonempty")
    if any(not isinstance(f, (str, dict)) or not f for f in value['blocking_findings']):
        raise ValueError("Invalid blocking finding")
    if value['approved'] and (not value['criteria_met'] or not value['simple']
                              or value.get('delivery_met') is False or value['blocking_findings']):
        raise ValueError("Contradictory review verdict")
    if value['approved'] and re.search(
            r"\b(?:not approved|do not (?:approve|merge)|criteria (?:are )?not met|"
            r"unresolved (?:defect|bug|blocking finding)|blocking (?:defect|bug) remains)\b",
            value['summary'], re.I):
        raise ValueError("Summary contradicts approval")
    return {**value, 'author_family': author_family, 'reviewer_family': reviewer_family}
