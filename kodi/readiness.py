"""Read-only operator catalog prerequisites; preparation never grants enablement."""
import re


def blockers(repo_key, repo, requirements):
    reasons = []
    if repo.get('enabled', True) is not True:
        reasons.append('operator: repository is disabled; explicitly authorize enablement')
    links = repo.get('onboarding_dependencies', [])
    if not isinstance(links, list) or any(not isinstance(link, str) or not link for link in links):
        return reasons + ['operator: invalid dependency links; reconcile prerequisite identities']
    links = list(links)
    task_links = re.findall(r'^Dependency:(.*)$', requirements, re.M)
    if any(not re.fullmatch(r'\S+', link.strip()) for link in task_links):
        return reasons + ['operator: missing or malformed dependency identity']
    links += [link.strip() for link in task_links]
    records = repo.get('readiness', [])
    if not isinstance(records, list):
        return reasons + ['operator: invalid readiness catalog; reconcile prerequisite records']
    for identity in dict.fromkeys(links):
        matches = [r for r in records if isinstance(r, dict) and r.get('id') == identity
                   and r.get('repo') == repo_key]
        if len(matches) != 1:
            reasons.append(f'{identity}: operator must reconcile missing or ambiguous prerequisite')
            continue
        record = matches[0]
        checks = record.get('checks')
        if not isinstance(checks, list) or not checks:
            reasons.append(f'{identity}: operator must declare required onboarding checks')
            continue
        check_ids = [check.get('id') for check in checks if isinstance(check, dict)]
        if any(not isinstance(key, str) or not key for key in check_ids) or len(set(check_ids)) != len(checks):
            reasons.append(f'{identity}: operator must reconcile missing or ambiguous check identities')
            continue
        for check in checks:
            if not isinstance(check, dict):
                reasons.append(f'{identity}: operator must reconcile invalid check')
                continue
            if (check.get('state') != 'resolved' or not check.get('evidence')
                    or not all(check.get(k) for k in ('id', 'actor', 'reason', 'action'))):
                reasons.append(f"{identity}/{check.get('id', 'unknown')}: "
                    f"{check.get('actor') or 'operator'}: {check.get('reason') or 'check unknown'}; "
                    f"{check.get('action') or 'validate prerequisite with evidence'}")
    return reasons
