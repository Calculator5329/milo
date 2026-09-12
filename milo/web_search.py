"""Explicit web queries only. Search excerpts are data; result URLs are never fetched."""
from __future__ import annotations

import ipaddress
import queue
import re
import threading
import time
from urllib.parse import urlsplit


class SearchUnavailable(ValueError):
    pass


def public_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower().rstrip('.')
        if (parsed.scheme not in ('http', 'https') or not host or parsed.username
                or parsed.password or parsed.port not in (None, 80, 443)
                or '.' not in host or host.endswith(('.local', '.internal', '.localhost'))):
            return None
        try:
            ipaddress.ip_address(host)
            return None  # No IP literals in source links, including public ones.
        except ValueError:
            pass
        if host == 'localhost':
            return None
    except ValueError:
        return None
    return value


def clean_sources(rows):
    if not isinstance(rows, list):
        return []
    sources, seen = [], set()
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        url = public_url(row.get('href', row.get('url')))
        title, excerpt = row.get('title'), row.get('body', row.get('excerpt'))
        if not url or url in seen or not isinstance(title, str) or not isinstance(excerpt, str):
            continue
        title = ' '.join(title.split())[:180]
        excerpt = ' '.join(excerpt.split())[:1000]
        if not title or not excerpt:
            continue
        sources.append({'id': len(sources) + 1, 'title': title, 'url': url, 'excerpt': excerpt, 'origin': 'web'})
        seen.add(url)
        if len(sources) == 4:
            break
    return sources


def requested_query(text, explicit=False):
    """No generic 'today' trigger: ordinary personal conversation stays local."""
    if explicit:
        return text.strip()[:400]
    match = re.match(r'^(?:hey milo[,!]?\s+)?(?:please\s+)?(?:(?:can|could|would) you\s+)?(?:please\s+)?(?:search (?:the )?(?:web|internet)(?: for)?|search for|look up|look online for|check online for|find online)\s+(.+)', text, re.I)
    return match.group(1).strip()[:400] if match else None


class SearchClient:
    def __init__(self, provider=None, deadline=12):
        self.provider = provider or self._ddgs
        self.deadline = deadline
        self.busy = threading.Lock()

    @staticmethod
    def _ddgs(query):
        from ddgs import DDGS
        return DDGS(timeout=5).text(query, max_results=4, backend='duckduckgo,brave', region='us-en')

    def search(self, query, cancelled):
        if not self.busy.acquire(blocking=False):
            raise SearchUnavailable('The previous web lookup is still finishing. Try again in a moment.')
        result = queue.Queue(maxsize=1)

        def worker():
            try:
                result.put(('ok', self.provider(query)))
            except Exception:
                result.put(('error', None))
            finally:
                self.busy.release()

        threading.Thread(target=worker, daemon=True).start()
        until = time.monotonic() + self.deadline
        while not cancelled.is_set():
            remaining = until - time.monotonic()
            if remaining <= 0:
                raise SearchUnavailable('The web lookup timed out. Try again shortly.')
            try:
                kind, rows = result.get(timeout=min(.1, remaining))
            except queue.Empty:
                continue
            sources = clean_sources(rows) if kind == 'ok' else []
            if not sources:
                raise SearchUnavailable('I couldn’t retrieve usable web results. Try a more specific search or try again shortly.')
            return sources
        return []
