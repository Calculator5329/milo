"""Pure helpers for one bounded follow-up search of Milo's local library."""
from __future__ import annotations

import argparse
import json
import re
import threading


MAX_SOURCES = 4
EXCERPT_CHARS = 700
DEFAULT_BUDGET_CHARS = MAX_SOURCES * EXCERPT_CHARS

STOP_WORDS = {
    'a', 'about', 'an', 'and', 'are', 'as', 'at', 'be', 'been', 'but', 'by',
    'can', 'could', 'did', 'do', 'does', 'for', 'from', 'had', 'has', 'have',
    'how', 'i', 'if', 'in', 'into', 'is', 'it', 'its', 'may', 'of', 'on', 'or',
    'should', 'that', 'the', 'their', 'then', 'there', 'these', 'they', 'this',
    'those', 'to', 'was', 'were', 'what', 'when', 'where', 'which', 'who',
    'why', 'will', 'with', 'would', 'you', 'your',
}

WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")
CAPITALISED_PHRASE_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9'-]+)"
    r"(?:\s+(?:(?:and|for|in|of|on|the)\s+)*[A-Z][A-Za-z0-9'-]+)+\b"
)
QUOTED_PHRASE_RES = (
    re.compile(r'"([^"\n]{2,100})"'),
    re.compile(r'“([^”\n]{2,100})”'),
    re.compile(r"(?<!\w)'([^'\n]{2,100})'(?!\w)"),
    re.compile(r'‘([^’\n]{2,100})’'),
)
OBJECT_PATTERNS = (
    re.compile(r"\bis\s+an?\s+([^,.;:\n]{2,100})", re.I),
    re.compile(r"\balso\s+known\s+as\s+([^,.;:\n]{2,100})", re.I),
    re.compile(r"\bsee\s+([^,.;:\n]{2,100})", re.I),
    re.compile(r"\bpart\s+of\s+the\s+([^,.;:\n]{2,100})", re.I),
)


def _words(text):
    return [word.casefold() for word in WORD_RE.findall(text or '')]


def _query_terms(query):
    return {word for word in _words(query) if word not in STOP_WORDS and (len(word) > 1 or word.isdigit())}


def _list_like(excerpt):
    lowered = excerpt.casefold()
    if 'may refer to' in lowered or 'can refer to' in lowered or 'disambiguation' in lowered:
        return True
    lines = [line.strip() for line in excerpt.splitlines() if line.strip()]
    if len(lines) >= 4 and sum(len(line) <= 80 for line in lines) / len(lines) >= 0.7:
        return True
    markers = re.findall(r"(?:^|\n)\s*(?:[-*•]|\d+[.)])\s+", excerpt)
    return len(markers) >= 3


def _title_settles(query, sources):
    """A first-hop source whose title is the query itself (ignoring a parenthetical) already answers it."""
    wanted = ' '.join(_words(query))
    if not wanted:
        return False
    for source in sources:
        title = ' '.join(_words(re.sub(r'\s*\(.*?\)\s*$', '', source.get('title', '') or '')))
        if title == wanted and not _list_like(source.get('excerpt', '')):
            return True
    return False


def hop_reason(query, sources):
    """Explain why the first hop needs another search, or return None."""
    if not sources:
        return 'fewer than two sources'
    if _title_settles(query, sources):
        return None
    if _list_like(sources[0].get('excerpt', '')):
        return 'top source is a disambiguation or list'
    if len(sources) < 2:
        return 'fewer than two sources'
    wanted = _query_terms(query)
    excerpt_words = {
        word
        for source in sources
        for word in _words(source.get('excerpt', ''))
    }
    if wanted and wanted.isdisjoint(excerpt_words):
        return 'query terms are absent from the excerpts'
    return None


def needs_hop(query, sources):
    """Return whether the first source set looks insufficient for the query."""
    return hop_reason(query, sources) is not None


def _clean_candidate(text):
    text = ' '.join((text or '').strip(' \t\r\n\"\'“”‘’()[]{}').split())
    text = re.sub(r'^(?:a|an|see|the)\s+', '', text, flags=re.I)
    text = re.split(r'\s+(?:and|but|that|which|who)\s+', text, maxsplit=1, flags=re.I)[0]
    words = WORD_RE.findall(text)
    if not words or len(words) > 10:
        return None
    text = ' '.join(words)
    if len(text) < 2 or all(word.casefold() in STOP_WORDS for word in words):
        return None
    return text


def _covered(candidate, query, titles):
    normalized = ' '.join(_words(candidate))
    query_text = ' '.join(_words(query))
    if normalized and re.search(r'(?<!\w)' + re.escape(normalized) + r'(?!\w)', query_text):
        return True
    for title in titles:
        title_text = ' '.join(_words(title))
        if normalized and re.search(r'(?<!\w)' + re.escape(normalized) + r'(?!\w)', title_text):
            return True
    return False


def candidates(query, sources, limit=3):
    """Return recurring follow-up terms from excerpts in stable relevance order."""
    if limit <= 0:
        return []
    titles = [source.get('title', '') for source in sources]
    excerpts = [source.get('excerpt', '') for source in sources]
    mentions = []
    offset = 0
    for excerpt in excerpts:
        for match in CAPITALISED_PHRASE_RE.finditer(excerpt):
            mentions.append((offset + match.start(), match.group(0)))
        for pattern in QUOTED_PHRASE_RES:
            for match in pattern.finditer(excerpt):
                mentions.append((offset + match.start(), match.group(1)))
        for pattern in OBJECT_PATTERNS:
            for match in pattern.finditer(excerpt):
                mentions.append((offset + match.start(1), match.group(1)))
        offset += len(excerpt) + 1

    first_mentions = {}
    display = {}
    for position, raw in sorted(mentions):
        candidate = _clean_candidate(raw)
        if not candidate or _covered(candidate, query, titles):
            continue
        key = candidate.casefold()
        first_mentions.setdefault(key, position)
        display.setdefault(key, candidate)

    combined = '\n'.join(excerpts)
    ranked = []
    for key, candidate in display.items():
        count = len(re.findall(r'(?<!\w)' + re.escape(candidate) + r'(?!\w)', combined, re.I))
        ranked.append((-count, first_mentions[key], candidate.casefold(), candidate))
    ranked.sort()
    return [item[3] for item in ranked[:limit]]


def _source_key(source):
    return (
        source.get('citation')
        or source.get('url')
        or source.get('title', '').casefold()
    )


def merge(first, second, budget_chars=DEFAULT_BUDGET_CHARS, via=None):
    """Combine both hops without exceeding the source or excerpt budgets."""
    if budget_chars <= 0:
        return []
    # The first hop's two best sources stay ahead of the follow-up: the follow-up explains
    # something the excerpts named, it never replaces the article the question was about.
    ordered = [(source, False) for source in first[:2]]
    ordered.extend((source, True) for source in second)
    ordered.extend((source, False) for source in first[2:])

    selected = []
    seen = set()
    for source, is_second in ordered:
        key = _source_key(source)
        if key in seen:
            continue
        seen.add(key)
        copied = dict(source)
        if is_second:
            copied['hop'] = 2
            copied['via'] = via if via is not None else copied.get('via', '')
        selected.append(copied)
        if len(selected) == MAX_SOURCES:
            break

    if not selected:
        return []
    base, extra = divmod(budget_chars, len(selected))
    allocations = [min(len(source.get('excerpt', '')), base + (index < extra))
                   for index, source in enumerate(selected)]
    remaining = budget_chars - sum(allocations)
    while remaining:
        changed = False
        for index, source in enumerate(selected):
            available = len(source.get('excerpt', '')) - allocations[index]
            if available <= 0:
                continue
            grant = min(available, remaining)
            allocations[index] += grant
            remaining -= grant
            changed = True
            if not remaining:
                break
        if not changed:
            break

    merged = []
    for source, allocation in zip(selected, allocations):
        if allocation <= 0:
            continue
        source['excerpt'] = source.get('excerpt', '')[:allocation].rstrip()
        source['id'] = len(merged) + 1
        merged.append(source)
    return merged


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run Milo local search with one bounded follow-up hop.')
    parser.add_argument('query')
    args = parser.parse_args(argv)
    try:
        from .local_search import LocalLibraryClient
    except ImportError:
        from local_search import LocalLibraryClient

    try:
        receipt = LocalLibraryClient().search(args.query, threading.Event())
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
