"""Search and status helpers for Milo's recent-information index."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3

from .config import database_path


MAX_EXCERPT_CHARS = 700
FRESHNESS_BOOST = 0.05


def _utc_now(value=None):
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _parse_iso(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _hours_since(value, now=None):
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return max(0.0, (_utc_now(now) - parsed).total_seconds() / 3600.0)


def _fts_query(query):
    tokens = re.findall(r"\w+", query or "", flags=re.UNICODE)
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def _excerpt(value):
    clean = " ".join((value or "").split())
    if len(clean) <= MAX_EXCERPT_CHARS:
        return clean
    return clean[:MAX_EXCERPT_CHARS - 1].rstrip() + "…"


def search(query, limit=4, db=None, now=None):
    """Return ranked FTS matches with a compact excerpt and explicit age."""
    path = database_path(db)
    if not path.is_file() or limit <= 0:
        return []
    match = _fts_query(query)
    if not match:
        return []
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT d.title, d.url, d.source, d.published, d.fetched, d.text,
                   snippet(docs_fts, 1, '', '', ' ... ', 64) AS excerpt,
                   bm25(docs_fts, 4.0, 1.0, 0.25, 0.1) AS relevance
            FROM docs_fts
            JOIN docs AS d ON d.rowid = docs_fts.rowid
            WHERE docs_fts MATCH ?
            ORDER BY relevance
            LIMIT ?
            """,
            (match, max(int(limit) * 12, 40)),
        ).fetchall()
    except (OSError, sqlite3.Error, ValueError):
        return []
    finally:
        if "connection" in locals():
            connection.close()
    ranked = []
    for row in rows:
        as_of = row["published"] or row["fetched"]
        hours = _hours_since(as_of, now=now)
        age_days = (hours or 0.0) / 24.0
        freshness_factor = 1.0 + FRESHNESS_BOOST / (1.0 + age_days)
        adjusted = float(row["relevance"]) * freshness_factor
        ranked.append((adjusted, {
            "title": row["title"],
            "url": row["url"],
            "excerpt": _excerpt(row["excerpt"] or row["text"]),
            "source": row["source"],
            "as_of": as_of,
            "age_hours": round(hours, 2) if hours is not None else None,
        }))
    ranked.sort(key=lambda item: item[0])
    return [item[1] for item in ranked[:int(limit)]]


def _empty_status():
    return {"built": False, "docs": 0, "last_run": None, "age_hours": None, "sources": []}


def status(db=None, now=None):
    """Describe index size, sources, and the most recent completed run."""
    path = database_path(db)
    if not path.is_file():
        return _empty_status()
    try:
        connection = sqlite3.connect(path)
        docs = connection.execute("SELECT count(*) FROM docs").fetchone()[0]
        run = connection.execute("SELECT finished FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        sources = [row[0] for row in connection.execute("SELECT DISTINCT source FROM docs ORDER BY source")]
    except (OSError, sqlite3.Error):
        return _empty_status()
    finally:
        if "connection" in locals():
            connection.close()
    last_run = run[0] if run else None
    hours = _hours_since(last_run, now=now)
    return {
        "built": True,
        "docs": docs,
        "last_run": last_run,
        "age_hours": round(hours, 2) if hours is not None else None,
        "sources": sources,
    }


def age_seconds(db=None, now=None):
    """Return seconds since the last completed run, or None before first build."""
    current = status(db=db, now=now)
    parsed = _parse_iso(current["last_run"])
    if parsed is None:
        return None
    return max(0.0, (_utc_now(now) - parsed).total_seconds())
