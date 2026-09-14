"""Fetch and retain recent documents for Milo's local freshness index."""

from __future__ import annotations

import datetime as dt
import re
import time
import urllib.request

from .article import extract_readable_text
from .config import load_config, load_feeds
from .snapshots import parse_stooq, parse_weather
from .store import document_id, init_db, insert_document, url_exists
from .syndication import parse_feed
from .util import iso_time, utc_now
from .wiki import parse_current_events


FETCH_TIMEOUT = 10
ARTICLE_DELAY_SECONDS = 0.5
MAX_ARTICLES_PER_FEED = 25
RETENTION_DAYS = 14
USER_AGENT = "Milo freshness/1.0"


def fetch_url(url):
    """Fetch one URL with the fixed timeout and a plain identifying user agent."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
        payload = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _feed_limit(feed):
    try:
        requested = int(feed.get("max_items", MAX_ARTICLES_PER_FEED))
    except (TypeError, ValueError):
        requested = MAX_ARTICLES_PER_FEED
    return max(0, min(requested, MAX_ARTICLES_PER_FEED))


def _symbol_from_url(url):
    match = re.search(r"[?&]s=([^&]+)", url, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _ingest_syndication(connection, feed, fetch, sleeper, fetched):
    items = parse_feed(fetch(feed["url"]), feed)[:_feed_limit(feed)]
    added = 0
    article_fetches = 0
    for item in items:
        if url_exists(connection, item["url"]):
            continue
        if article_fetches:
            sleeper(ARTICLE_DELAY_SECONDS)
        article_fetches += 1
        try:
            body = extract_readable_text(fetch(item["url"]))
        except Exception:
            body = ""
        if not body:
            body = extract_readable_text(item.get("summary", ""))
        if not body:
            continue
        added += int(insert_document(connection, {
            "id": document_id(item["url"]),
            "source": item["source"],
            "group": item["group"],
            "title": item["title"],
            "url": item["url"],
            "published": item["published"],
            "fetched": fetched,
            "text": body,
        }))
    return added


def _ingest_wiki(connection, feed, fetch, fetched, now):
    added = 0
    for item in parse_current_events(fetch(feed["url"]), now=now)[:_feed_limit(feed)]:
        if url_exists(connection, item["url"]):
            continue
        added += int(insert_document(connection, {
            "id": document_id(item["url"]),
            "source": feed["name"],
            "group": "wiki",
            "title": item["title"],
            "url": item["url"],
            "published": item["published"],
            "fetched": fetched,
            "text": item["text"],
        }))
    return added


def _ingest_weather(connection, feed, settings, fetch, fetched):
    lat = settings.get("lat", 0.0)
    lon = settings.get("lon", 0.0)
    url = feed["url"].format(lat=lat, lon=lon)
    body = parse_weather(fetch(url), lat=lat, lon=lon)
    return int(bool(body) and insert_document(connection, {
        "id": document_id("weather", fetched),
        "source": feed["name"],
        "group": "weather",
        "title": f"Weather snapshot as of {fetched}",
        "url": url,
        "published": fetched,
        "fetched": fetched,
        "text": body,
    }))


def _insert_market_snapshot(connection, lines, urls, fetched):
    if not lines:
        return 0
    return int(insert_document(connection, {
        "id": document_id("markets", fetched),
        "source": "Stooq",
        "group": "markets",
        "title": f"Markets snapshot as of {fetched}",
        "url": urls[0] if urls else "https://stooq.com/",
        "published": fetched,
        "fetched": fetched,
        "text": " ".join(lines),
    }))


def ingest(db=None, feed_entries=None, settings=None, fetch=None, sleeper=None, now=None):
    """Run one isolated ingest and return its recorded counters."""
    fetch = fetch or fetch_url
    sleeper = sleeper or time.sleep
    settings = dict(load_config() if settings is None else settings)
    entries = list(load_feeds() if feed_entries is None else feed_entries)
    disabled = set(settings.get("disabled_feeds") or [])
    entries = [feed for feed in entries if feed.get("enabled", True) and feed.get("name") not in disabled]
    started_at = utc_now(now)
    started = iso_time(started_at)
    connection = init_db(db)
    feeds_ok = 0
    feeds_failed = 0
    docs_added = 0
    failures = []
    market_lines = []
    market_urls = []
    try:
        cutoff = iso_time(started_at - dt.timedelta(days=RETENTION_DAYS))
        connection.execute("DELETE FROM docs WHERE julianday(fetched) < julianday(?)", (cutoff,))
        connection.commit()
        for feed in entries:
            try:
                group = feed.get("group")
                if group == "wiki":
                    docs_added += _ingest_wiki(connection, feed, fetch, started, started_at)
                elif group == "weather":
                    docs_added += _ingest_weather(connection, feed, settings, fetch, started)
                elif group == "markets":
                    line = parse_stooq(fetch(feed["url"]), symbol=_symbol_from_url(feed["url"]))
                    if line:
                        market_lines.append(line)
                        market_urls.append(feed["url"])
                else:
                    docs_added += _ingest_syndication(connection, feed, fetch, sleeper, started)
                feeds_ok += 1
            except Exception as exc:
                feeds_failed += 1
                failures.append({"feed": feed.get("name", "Unknown"), "error": str(exc)})
            connection.commit()
        docs_added += _insert_market_snapshot(connection, market_lines, market_urls, started)
        finished = iso_time(started_at if now is not None else None)
        connection.execute(
            "INSERT INTO runs(started, finished, feeds_ok, feeds_failed, docs_added) VALUES(?, ?, ?, ?, ?)",
            (started, finished, feeds_ok, feeds_failed, docs_added),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "started": started,
        "finished": finished,
        "feeds_ok": feeds_ok,
        "feeds_failed": feeds_failed,
        "docs_added": docs_added,
        "failures": failures,
    }


run_ingest = ingest
