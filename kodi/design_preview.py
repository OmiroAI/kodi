"""Resolve task-scoped artifacts registered by an operator's private integration.

No worker-selected URL is fetched or published. The existing preview service owns
access control and artifact retention; the coordinator only projects its links.
"""
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def review_text(config, board_id, ids):
    if not isinstance(ids, list) or not 1 <= len(ids) <= 6 or any(not isinstance(key, str) for key in ids) or len(set(ids)) != len(ids):
        raise ValueError('Select between one and six distinct registered previews')
    registry = config.get('design_previews', {})
    lines = ['Design review (delivery acceptance is still required):']
    for key in ids:
        if not isinstance(key, str) or not key or len(key) > 60 or any(c.isspace() for c in key) or key not in registry:
            raise ValueError('Preview is not registered')
        item = registry[key]
        if (item.get('task') != board_id or item.get('private') is not True or
                item.get('retained_during_review') is not True):
            raise ValueError('Preview must be private, retained and scoped to this task')
        url = item.get('url', '')
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.fragment or parsed.query or len(url) > 500 or
                any(c.isspace() or ord(c) < 32 for c in url)):
            raise ValueError('Preview requires an HTTPS URL without embedded credentials')
        if item.get('kind') not in ('mockup', 'application render'):
            raise ValueError('Identify mockup or application render')
        for field in ('viewport', 'state', 'hardware', 'interaction', 'sound', 'recovery'):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 180 or '\n' in value:
                raise ValueError('Missing or invalid preview '+field)
        lines.append(f"{key}: {item['kind']} — {item['viewport']} — {item['state']}\n{url}\n"
                     f"Hardware: {item['hardware']}; Interaction: {item['interaction']}; Sound: {item['sound']}\n"
                     f"If unavailable: {item['recovery']}")
    text = '\n\n'.join(lines)
    if len(text) > 2500:
        raise ValueError('Preview review exceeds notification budget')
    return text


def available(config, board_id):
    entries = []
    for key, item in config.get('design_previews', {}).items():
        if isinstance(item, dict) and item.get('task') == board_id:
            # Fail closed on an incomplete registration, without blocking ordinary coding.
            try:
                entries.append(review_text(config, board_id, [key]))
            except (ValueError, TypeError):
                continue
    return '\nRegistered visual review previews:\n' + '\n'.join(entries) if entries else ''


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_links(config, ids):
    """Only probe registered URLs, never follow a redirect to another artifact."""
    opener = build_opener(_NoRedirect)
    for key in ids:
        try:
            request = Request(config['design_previews'][key]['url'], method='GET')
            with opener.open(request, timeout=5) as response:
                if response.status != 200:
                    raise ValueError('Preview unavailable')
                response.read(1)
        except Exception:
            raise ValueError('Registered preview unavailable; use its recovery instructions and retry') from None
