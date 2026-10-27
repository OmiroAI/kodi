"""Operator-owned author selection; snapshots use the existing evidence store."""
import json
import re


def validate_profile(value):
    if not isinstance(value, dict):
        raise ValueError('Coding profile must contain model and effort')
    model, effort = value.get('model'), value.get('effort')
    if not isinstance(model, str) or not re.fullmatch(r'gpt-[a-zA-Z0-9.-]+', model):
        raise ValueError('Coding profile requires an exact OpenAI gpt model ID')
    supported = value.get('supported_efforts')
    if (not isinstance(supported, list) or not supported or
            any(e not in ('low', 'medium', 'high', 'xhigh', 'max', 'ultra') for e in supported)
            or effort not in supported):
        raise ValueError('Coding profile effort must be in operator-verified supported_efforts; smoke-test the worker image')
    if model == 'gpt-6-luna' and effort == 'ultra':
        raise ValueError('gpt-6-luna does not support ultra reasoning effort')
    return value


def author_config(config, store, job, recovery=False):
    if config.get('coding_routing_enabled') and not config.get('coding_profiles'):
        raise ValueError('Explicit coding_profiles are required before routing can be enabled')
    kind = 'recovery_profile' if recovery else 'coding_profile'
    row = store.db.execute('SELECT data FROM evidence WHERE job=? AND kind=? ORDER BY id LIMIT 1',
                           (job, kind)).fetchone()
    if row:
        selection = json.loads(row['data'])
    else:
        profiles = config.get('coding_profiles')
        if 'coding_profiles' not in config:
            selection = {'profile': 'legacy', 'model': None, 'effort': None}
        else:
            if not isinstance(profiles, dict) or not profiles:
                raise ValueError('Configure nonempty coding_profiles and default/recovery selections')
            if any(not isinstance(name,str) or not name or name=='legacy' for name in profiles):
                raise ValueError('Coding profile names must be nonempty strings; legacy is reserved')
            for value in profiles.values():
                validate_profile(value)
            for key in ('default_coding_profile', 'recovery_coding_profile'):
                if not isinstance(config.get(key),str) or config[key] not in profiles:
                    raise ValueError(key+' must name a configured coding profile')
            name = config['recovery_coding_profile' if recovery else 'default_coding_profile']
            selection = dict(profiles[name], profile=name)
        store.evidence(job, '', kind, selection)
    if selection['profile'] != 'legacy':
        validate_profile(selection)
    return dict(config, selected_coding_profile=selection)
