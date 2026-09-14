"""Bounded web search. Search excerpts are data; result URLs are never fetched."""
from __future__ import annotations

import ipaddress
import queue
import re
import sys
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


LIBRARY_PREFIX = 'http://127.0.0.1:8891/content/'


def library_url(value):
    """Only the loopback Kiwix article path may appear as a non-public source link."""
    return value if isinstance(value, str) and value.startswith(LIBRARY_PREFIX) and '..' not in value and len(value) <= 2000 else None


PUBLIC_SITES = (
    ('wikipedia', 'https://en.wikipedia.org/wiki/'),
    ('wiktionary', 'https://en.wiktionary.org/wiki/'),
    ('archlinux', 'https://wiki.archlinux.org/title/'),
    ('wikibooks', 'https://en.wikibooks.org/wiki/'),
    ('wikivoyage', 'https://en.wikivoyage.org/wiki/'),
)


def library_public_url(value):
    """The public page behind a Kiwix article path, so a 'where is the page' ask gets a real link."""
    if not library_url(value):
        return None
    rest = value[len(LIBRARY_PREFIX):]
    book, _, path = rest.partition('/')
    slug = path.rsplit('/', 1)[-1]
    if not slug or slug.startswith('index'):
        return None
    for prefix, site in PUBLIC_SITES:
        if book.startswith(prefix):
            return site + slug
    return None


def clean_sources(rows):
    if not isinstance(rows, list):
        return []
    sources, seen = [], set()
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        raw_url = row.get('href', row.get('url'))
        origin = 'library' if row.get('origin') == 'library' and library_url(raw_url) else 'web'
        url = raw_url if origin == 'library' else public_url(raw_url)
        title, excerpt = row.get('title'), row.get('body', row.get('excerpt'))
        if not url or url in seen or not isinstance(title, str) or not isinstance(excerpt, str):
            continue
        title = ' '.join(title.split())[:180]
        excerpt = ' '.join(excerpt.split())[:1000]
        if not title or not excerpt:
            continue
        source = {'id': len(sources) + 1, 'title': title, 'url': url, 'excerpt': excerpt, 'origin': origin}
        if origin == 'library':
            source['book'] = ' '.join(str(row.get('book', '')).split())[:80]
            public = library_public_url(url)
            if public:
                source['public_url'] = public
        sources.append(source)
        seen.add(url)
        if len(sources) == 4:
            break
    return sources


REQUEST_START = r'^(?:hey milo[,!]?\s+)?(?:please\s+)?(?:(?:can|could|would) you\s+)?(?:please\s+)?'
WEB_REQUEST = re.compile(
    REQUEST_START
    + r'(?:search (?:the )?(?:web|internet)(?: for)?|google|search google(?: for)?|search online(?: for)?|'
      r'look online(?: for)?|check online(?: for)?|find online(?: for)?|web search:?|'
      r'look (?:it |this |that )?up (?:online|on the (?:web|internet))(?: for)?:?|'
      r'look up (?:online|on the (?:web|internet))(?: for)?:?|'
      r'find (?:on the (?:web|internet)|online)(?: for)?:?|'
      r'what does (?:the )?internet say about)\s+(.+)',
    re.I,
)
LIBRARY_REQUEST = re.compile(
    REQUEST_START
    + r'(?:search (?:the |your |my )?(?:offline )?library(?: for)?|'
      r'look (?:in|through) (?:the |your |my )?(?:offline )?library(?: for)?|'
      r'check (?:the |your |my )?(?:offline )?library(?: for)?)\s+(.+)',
    re.I,
)
GENERIC_REQUEST = re.compile(REQUEST_START + r'(?:search for|look up)\s+(.+)', re.I)


def _requested(pattern, text):
    match = pattern.match(text or '')
    return match.group(1).strip().rstrip('?.!:,')[:400] if match else None


def requested_web(text):
    """Return the query only when the utterance explicitly names the public web."""
    return _requested(WEB_REQUEST, text)


def requested_query(text, explicit=False):
    """Return explicit web, library, and generic lookup queries, but never infer from 'today'."""
    if explicit:
        return text.strip()[:400]
    return (requested_web(text) or _requested(LIBRARY_REQUEST, text)
            or _requested(GENERIC_REQUEST, text))


class SearchClient:
    def __init__(self, provider=None, deadline=12):
        self.provider = provider or self._ddgs
        self.deadline = deadline
        self.busy = threading.Lock()

    # DuckDuckGo and Brave refuse in bursts ("No results found." within 200 ms) while the
    # other engines keep answering, so a miss on one backend moves to the next (2026-09-13).
    BACKENDS = ('duckduckgo,brave', 'bing', 'google', 'yahoo')

    @classmethod
    def _ddgs(cls, query):
        from ddgs import DDGS
        failure = None
        for backend in cls.BACKENDS:
            try:
                rows = DDGS(timeout=5).text(query, max_results=4, backend=backend, region='us-en')
            except Exception as error:
                failure = f'{backend}: {type(error).__name__}: {str(error)[:80]}'
                continue
            if rows:
                return rows
            failure = f'{backend}: empty'
        raise SearchUnavailable(f'Every search backend failed ({failure}).')

    def search(self, query, cancelled, deadline=None):
        if not self.busy.acquire(blocking=False):
            raise SearchUnavailable('The previous web lookup is still finishing. Try again in a moment.')
        result = queue.Queue(maxsize=1)

        def worker():
            try:
                result.put(('ok', self.provider(query)))
            except Exception as error:
                print(f'web search failed for {query!r}: {type(error).__name__}: {str(error)[:160]}',
                      file=sys.stderr, flush=True)
                result.put(('error', None))
            finally:
                self.busy.release()

        threading.Thread(target=worker, daemon=True).start()
        wait_budget = self.deadline if deadline is None else min(self.deadline, deadline)
        until = time.monotonic() + wait_budget
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
