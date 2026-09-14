"""Offline library lookup for Milo: cited passages from the loopback Kiwix service.

Local results come first; the web provider is only asked when the library has
nothing useful or the request was explicitly for the web. Article text is
untrusted quoted data exactly like web snippets.
"""
from __future__ import annotations

import queue
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from library.adapters import AdapterError, KiwixHttpAdapter  # noqa: E402
try:  # noqa: E402
    from .multihop import candidates, hop_reason, merge, needs_hop
except ImportError:  # Script-style imports used by server.py and the focused tests.
    from multihop import candidates, hop_reason, merge, needs_hop  # noqa: E402

KIWIX_URL = 'http://127.0.0.1:8891'
MAX_SOURCES = 4
NAMESPACE_TITLE = re.compile(r'^(?:Category|Template|File|Wikipedia|Portal|Help|Talk|Draft|Module|User|Book|Special)\s*:', re.I)
EXCERPT_CHARS = 700
MIN_EXCERPT_CHARS = 80


class LibraryUnavailable(ValueError):
    pass


DEFINITION_PATTERNS = (
    re.compile(r"^(?:what\s+)?(?:does\s+)?(?:the\s+)?(?:word\s+|term\s+)?['\"]?([A-Za-z][A-Za-z' -]{0,40}?)['\"]?\s+(?:means?|is defined as)\s*\??$", re.I),
    re.compile(r"^(?:(?:what\s+is\s+)?the\s+)?(?:definition|meaning)\s+of\s+(?:the\s+)?(?:word\s+)?['\"]?([A-Za-z][A-Za-z' -]{0,40}?)['\"]?\s*\??$", re.I),
    re.compile(r"^define\s+['\"]?([A-Za-z][A-Za-z' -]{0,40}?)['\"]?\s*\??$", re.I),
    # "What is a mutex?": one lowercase word after the article is a term, never a topic sentence.
    re.compile(r"^what(?:'s|s| is| are)\s+(?:a|an)\s+([a-z][a-z-]{2,24})\s*\??$", re.I),
)
DICTIONARY_BOOK_RE = re.compile(r'href="/content/(wiktionary[^"/]*)')


def definition_term(query):
    """'what the word wanderlust means' -> 'wanderlust'; None when it is not a definition ask."""
    text = ' '.join((query or '').split())
    for pattern in DEFINITION_PATTERNS:
        match = pattern.match(text)
        if match:
            return match.group(1).strip().lower()
    return None


def _clean(text, limit):
    return ' '.join((text or '').split())[:limit]


def _tidy_excerpt(text):
    """Strip page chrome that survives html_to_text so the model gets prose."""
    text = re.sub(r'\b(?:Asked|Modified|Viewed)\s+[^.]{0,40}(?:ago|times)\b', ' ', text)
    text = re.sub(r'\b\d+\s+Answers?\b', ' ', text)
    return _clean(text, EXCERPT_CHARS)


class LocalLibraryClient:
    def __init__(self, adapter=None, deadline=4.0):
        self.adapter = adapter or KiwixHttpAdapter(base_url=KIWIX_URL, timeout=3)
        self.deadline = deadline
        self._dictionary = (0.0, None)

    def dictionary_book(self):
        """The served Wiktionary book id, rediscovered every ten minutes; None without one."""
        checked, book = self._dictionary
        if time.monotonic() - checked < 600:
            return book
        try:
            match = DICTIONARY_BOOK_RE.search(self.adapter._get('/catalog/v2/entries?count=100'))
            book = match.group(1) if match else None
        except Exception:
            book = None
        self._dictionary = (time.monotonic(), book)
        return book

    def available(self):
        try:
            self.adapter._get('/')
            return True
        except Exception:
            return False

    def search(self, query, cancelled):
        started = time.monotonic()
        term = definition_term(query)
        book = self.dictionary_book() if term else None
        if book:
            # A definition ask goes to the dictionary first; a cross-corpus search for
            # 'what the word wanderlust means' surfaced phrasebooks and a poet (2026-09-12).
            matches = []
            for candidate in dict.fromkeys((term, term.capitalize())):
                # Full-text ranking favours compounds ('ephemeral lake'); the entry itself is a direct read.
                path = '/content/' + book + '/' + candidate.replace(' ', '_')
                try:
                    entry = self.adapter.read(path)
                except (AdapterError, OSError, ValueError):
                    continue
                matches.append({'path': path, 'title': candidate, 'excerpt': entry['content'][:4000], 'book': 'Wiktionary', 'uri': entry['uri']})
                break
            try:
                found = self.adapter.search(term, limit=6, books=[book])
            except (AdapterError, OSError, ValueError):
                found = {}
            matches.extend(sorted(found.get('matches', []), key=lambda m: (m.get('title', '').strip().lower() != term)))
            sources = self._collect(matches, cancelled, started)
            if sources:
                elapsed_ms = round((time.monotonic() - started) * 1000)
                return {
                    'sources': sources,
                    'elapsed_ms': elapsed_ms,
                    'hops': [{'query': query, 'found': len(sources), 'ms': elapsed_ms}],
                    'hop_reason': None,
                }
        try:
            found = self.adapter.search(query, limit=8)
        except (AdapterError, OSError, ValueError) as exc:
            raise LibraryUnavailable('The offline library is not answering.') from exc
        sources = self._collect(found.get('matches', []), cancelled, started)
        first_ms = round((time.monotonic() - started) * 1000)
        hops = [{'query': query, 'found': len(sources), 'ms': first_ms}]
        reason = None if term else hop_reason(query, sources)

        follow_ups = candidates(query, sources, limit=1) if not term and needs_hop(query, sources) else []
        if (follow_ups and not cancelled.is_set()
                and time.monotonic() - started < self.deadline):
            follow_up = follow_ups[0]
            second_started = time.monotonic()
            second_sources = []
            try:
                same_books = found.get('books') or None
                second = self._search_within_deadline(
                    follow_up, same_books, cancelled, started
                )
                if second is None:
                    raise TimeoutError('The follow-up search exceeded the lookup deadline.')
                matches = sorted(
                    second.get('matches', []),
                    key=lambda item: self._title_rank(item.get('title', ''), follow_up),
                )
                second_sources = self._collect(matches, cancelled, started)
            except Exception:
                # The first-hop evidence remains useful even if the optional hop fails.
                second_sources = []
            second_ms = round((time.monotonic() - second_started) * 1000)
            hops.append({'query': follow_up, 'found': len(second_sources), 'ms': second_ms})
            if second_sources:
                sources = merge(
                    sources,
                    second_sources,
                    MAX_SOURCES * EXCERPT_CHARS,
                    via=follow_up,
                )

        return {
            'sources': sources,
            'elapsed_ms': round((time.monotonic() - started) * 1000),
            'hops': hops,
            'hop_reason': reason,
        }

    def _search_within_deadline(self, query, books, cancelled, started):
        """Run the optional hop without letting it extend the original deadline."""
        results = queue.Queue(maxsize=1)

        def search_adapter():
            try:
                outcome = (True, self.adapter.search(query, limit=8, books=books))
            except Exception as exc:
                outcome = (False, exc)
            results.put_nowait(outcome)

        worker = threading.Thread(target=search_adapter, daemon=True)
        worker.start()
        while not cancelled.is_set():
            remaining = self.deadline - (time.monotonic() - started)
            if remaining <= 0:
                return None
            try:
                succeeded, result = results.get(timeout=min(remaining, 0.02))
            except queue.Empty:
                continue
            if succeeded:
                return result
            raise result
        return None

    @staticmethod
    def _title_rank(title, query):
        title = ' '.join((title or '').casefold().split())
        query = ' '.join((query or '').casefold().split())
        if title == query:
            return 0
        if title.startswith(query):
            return 1
        if query in title:
            return 2
        return 3

    def _collect(self, matches, cancelled, started):
        sources, seen = [], set()
        for match in matches:
            if cancelled.is_set() or time.monotonic() - started > self.deadline:
                break
            path = match.get('path', '')
            if not path.startswith('/content/') or path in seen:
                continue
            seen.add(path)
            title = _clean(match.get('title'), 180)
            if NAMESPACE_TITLE.match(title):
                # Category, Template, File and similar pages from the full Wikipedia index are never an answer.
                continue
            excerpt = _tidy_excerpt(match.get('excerpt', ''))
            if len(excerpt) < MIN_EXCERPT_CHARS:
                try:
                    excerpt = _tidy_excerpt(self.adapter.read(path)['content'][:4000])
                except (AdapterError, OSError, ValueError):
                    continue
            if not title or len(excerpt) < MIN_EXCERPT_CHARS:
                continue
            sources.append({'id': len(sources) + 1, 'title': title, 'url': self.adapter.base_url + path,
                            'excerpt': excerpt, 'origin': 'library', 'book': _clean(re.sub(r'^from\s+', '', match.get('book', '')), 80),
                            'citation': match.get('uri', 'kiwix://' + path.removeprefix('/content/'))})
            if len(sources) == MAX_SOURCES:
                break
        return sources
