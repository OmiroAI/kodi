"""Allowlisted observations for the external accounting API."""
import json
import math
import re
import unicodedata
from datetime import datetime, timezone

MAX_COUNT = 2**63 - 1
UNKNOWN_MODELS = {'provider default', 'rebase recovery', 'base repair', 'unknown'}


class InvalidActivity(ValueError):
    """A ledger record needs explicit reconciliation before delivery."""


def _data(value):
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _count(value):
    return value if type(value) is int and 0 <= value <= MAX_COUNT else None


def _text(value):
    if not isinstance(value, str) or not value or value.strip() != value:
        return None
    try:
        if len(value.encode('utf-8')) > 256:
            return None
    except UnicodeError:
        return None
    return None if any(unicodedata.category(c) == 'Cc' for c in value) else value


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
        if parsed.utcoffset() is None:
            raise ValueError('missing timezone')
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise InvalidActivity('invalid_occurrence_time') from None


def _common(row, source_instance, prefix):
    return {'schema_version': 1, 'source': 'kodi', 'source_instance': source_instance,
            'event_id': prefix + ':' + str(row['id']), 'occurred_at': _timestamp(row['created'])}


def _nested(data, field, key):
    details = data.get(field)
    return _count(details.get(key)) if isinstance(details, dict) else None


def _counts(provider, data):
    incoming = outgoing = cached = reasoning = None
    if provider == 'qwen':
        incoming, outgoing = _count(data.get('prompt_tokens')), _count(data.get('completion_tokens'))
        cached = _nested(data, 'prompt_tokens_details', 'cached_tokens')
        reasoning = _nested(data, 'completion_tokens_details', 'reasoning_tokens')
    elif provider in ('codex', 'antigravity'):
        incoming, outgoing = _count(data.get('input_tokens')), _count(data.get('output_tokens'))
        cached = (_count(data['cached_input_tokens']) if 'cached_input_tokens' in data
                  else _nested(data, 'input_tokens_details', 'cached_tokens'))
        reasoning = (_count(data['reasoning_output_tokens']) if 'reasoning_output_tokens' in data
                     else _nested(data, 'output_tokens_details', 'reasoning_tokens'))
    if incoming is not None and outgoing is not None and incoming > MAX_COUNT - outgoing:
        raise InvalidActivity('token_total_out_of_range')
    if incoming is not None and cached is not None and cached > incoming:
        cached = None
    if outgoing is not None and reasoning is not None and reasoning > outgoing:
        reasoning = None
    return {'input_tokens': incoming, 'output_tokens': outgoing,
            'cached_input_tokens': cached, 'reasoning_output_tokens': reasoning}


def _duration(seconds):
    if type(seconds) not in (int, float) or not 0 <= seconds <= MAX_COUNT / 1000:
        return None
    if not math.isfinite(seconds):
        return None
    value = round(seconds * 1000)
    return value if value <= MAX_COUNT else None


def usage_event(row, source_instance):
    data = _data(row['data'])
    payload = _common(row, source_instance, 'usage')
    phase, outcome = row.get('phase'), row.get('outcome')
    payload.update(kind='worker_run',
                   phase=phase if phase in ('coding', 'review', 'recovery') else 'unknown',
                   outcome=outcome if outcome in ('succeeded', 'failed', 'timeout') else 'unknown',
                   duration_ms=_duration(row['seconds']))
    payload.update(_counts(row['provider'], data))
    provider = _text(row['provider'])
    model = _text(row.get('model'))
    if model in UNKNOWN_MODELS:
        model = None
    model = model or _text(data.get('model'))
    if provider:
        payload['provider'] = provider
    if model:
        payload['model'] = model
    return payload


def evidence_event(row, source_instance):
    if row['kind'] not in ('qa', 'review'):
        return None
    data = _data(row['data'])
    verdict = data.get('passed' if row['kind'] == 'qa' else 'approved')
    if type(verdict) is not bool:
        raise InvalidActivity('invalid_verdict')
    payload = _common(row, source_instance, 'evidence')
    outcomes = ('failed', 'passed') if row['kind'] == 'qa' else ('rejected', 'approved')
    payload.update(kind='qa_completed' if row['kind'] == 'qa' else 'review_completed',
                   outcome=outcomes[verdict])
    sha = row.get('sha')
    if isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{40}(?:[0-9a-f]{24})?', sha):
        payload['candidate_sha'] = sha
    return payload
