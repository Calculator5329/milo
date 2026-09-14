import datetime as dt
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from freshness.config import database_path, load_config
from freshness.ingest import (
    extract_readable_text,
    ingest,
    init_db,
    parse_current_events,
    parse_feed,
    parse_stooq,
    parse_weather,
)
from freshness.search import age_seconds, search, status


RSS_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Example RSS</title><item>
  <title>Battery storage report</title>
  <link>https://example.test/rss-story</link>
  <pubDate>Thu, 10 Sep 2026 08:00:00 GMT</pubDate>
  <description>Fallback battery reporting.</description>
</item></channel></rss>"""

ATOM_SAMPLE = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Example Atom</title><entry>
  <title>Battery storage report</title>
  <link rel="alternate" href="https://example.test/atom-story" />
  <updated>2026-09-12T09:30:00Z</updated>
  <summary>Fallback battery analysis.</summary>
</entry></feed>"""

ARTICLE_HTML = """<!doctype html><html><head><title>Chrome title</title>
<style>.hidden { display: none; }</style></head><body>
<header>Site menu</header><nav>Sections and login</nav>
<main><article><h1>Battery storage report</h1>
<p>A battery breakthrough makes long-duration grid storage practical.</p>
<aside>Related promotions</aside><p>Independent tests found strong results.</p>
</article></main><footer>Copyright example</footer><script>alert('no')</script>
</body></html>"""

CURRENT_EVENTS_HTML = """<html><body><div id="mw-content-text">
<section><h2 id="2026_September_12">September 12, 2026</h2>
<div class="current-events-main vevent"><ul><li>A lunar science mission launched.</li></ul></div></section>
<section><h2 id="2026_September_11">September 11, 2026</h2>
<div class="current-events-main vevent"><ul><li>Researchers published a climate report.</li></ul></div></section>
<section><h2 id="2026_September_10">September 10, 2026</h2>
<div class="current-events-main vevent"><ul><li>This older event must be excluded.</li></ul></div></section>
</div></body></html>"""

WEATHER_JSON = """{
  "latitude": 41.88, "longitude": -87.63, "timezone": "America/Chicago",
  "current": {"time": "2026-09-12T10:00", "temperature_2m": 21.4,
              "apparent_temperature": 20.9, "precipitation": 0.0,
              "weather_code": 1, "wind_speed_10m": 12.2},
  "current_units": {"temperature_2m": "°C", "apparent_temperature": "°C",
                    "precipitation": "mm", "wind_speed_10m": "km/h"},
  "daily": {"time": ["2026-09-12", "2026-09-13"],
            "temperature_2m_max": [24.0, 25.5], "temperature_2m_min": [15.0, 16.5],
            "precipitation_probability_max": [10, 30], "weather_code": [1, 2]},
  "daily_units": {"temperature_2m_max": "°C", "temperature_2m_min": "°C",
                  "precipitation_probability_max": "%"}
}"""

STOOQ_CSV = """Symbol,Date,Time,Open,High,Low,Close,Volume
SPY.US,2026-09-11,22:00:09,650.10,654.20,648.00,653.45,72100123
"""


class FreshnessParsingTests(unittest.TestCase):
    def test_parses_rss_atom_and_readable_article_text(self):
        rss = parse_feed(RSS_SAMPLE, {"name": "RSS source", "group": "news"})
        atom = parse_feed(ATOM_SAMPLE, {"name": "Atom source", "group": "tech"})
        self.assertEqual(rss[0]["url"], "https://example.test/rss-story")
        self.assertEqual(rss[0]["published"], "2026-09-10T08:00:00+00:00")
        self.assertEqual(atom[0]["published"], "2026-09-12T09:30:00+00:00")

        text = extract_readable_text(ARTICLE_HTML)
        self.assertIn("long-duration grid storage", text)
        self.assertIn("Independent tests", text)
        for chrome in ("Site menu", "Sections and login", "Related promotions", "Copyright", "alert"):
            self.assertNotIn(chrome, text)

    def test_parses_current_events_weather_and_market_fixtures(self):
        now = dt.datetime(2026, 9, 12, 12, 0, tzinfo=dt.timezone.utc)
        events = parse_current_events(CURRENT_EVENTS_HTML, now=now)
        self.assertEqual([item["published"][:10] for item in events], ["2026-09-12", "2026-09-11"])
        self.assertNotIn("older event", " ".join(item["text"] for item in events))

        weather = parse_weather(WEATHER_JSON, lat=41.88, lon=-87.63)
        self.assertIn("21.4 °C", weather)
        self.assertIn("2026-09-13", weather)
        market = parse_stooq(STOOQ_CSV, symbol="spy.us")
        self.assertIn("SPY.US", market)
        self.assertIn("653.45", market)

    def test_config_file_and_database_environment_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "freshness.json"
            config_path.write_text(json.dumps({
                "lat": 41.88,
                "lon": -87.63,
                "disabled_feeds": ["BBC World"],
            }), encoding="utf-8")
            self.assertEqual(load_config(config_path), {
                "lat": 41.88,
                "lon": -87.63,
                "disabled_feeds": ["BBC World"],
            })
            override = Path(tmp) / "override.db"
            with mock.patch.dict(os.environ, {"MILO_FRESHNESS_DB": str(override)}):
                self.assertEqual(database_path(), override)


class FreshnessIntegrationTests(unittest.TestCase):
    NOW = dt.datetime(2026, 9, 12, 12, 0, tzinfo=dt.timezone.utc)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "freshness.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _feeds(self):
        return [
            {"name": "RSS source", "url": "https://feed.test/rss", "group": "news", "enabled": True, "max_items": 5},
            {"name": "Atom source", "url": "https://feed.test/atom", "group": "tech", "enabled": True, "max_items": 5},
            {"name": "Wikipedia Current Events", "url": "https://feed.test/wiki", "group": "wiki", "enabled": True, "max_items": 2},
            {"name": "Open-Meteo", "url": "https://weather.test/?latitude={lat}&longitude={lon}", "group": "weather", "enabled": True, "max_items": 1},
            {"name": "Stooq SPY", "url": "https://market.test/?s=spy.us", "group": "markets", "enabled": True, "max_items": 1},
            {"name": "Broken feed", "url": "https://feed.test/broken", "group": "news", "enabled": True, "max_items": 5},
        ]

    def _fetch(self, url):
        responses = {
            "https://feed.test/rss": RSS_SAMPLE,
            "https://feed.test/atom": ATOM_SAMPLE,
            "https://example.test/rss-story": ARTICLE_HTML,
            "https://example.test/atom-story": ARTICLE_HTML,
            "https://feed.test/wiki": CURRENT_EVENTS_HTML,
            "https://weather.test/?latitude=41.88&longitude=-87.63": WEATHER_JSON,
            "https://market.test/?s=spy.us": STOOQ_CSV,
        }
        if url == "https://feed.test/broken":
            raise OSError("fixture feed is unavailable")
        return responses[url]

    def test_full_ingest_search_ranking_as_of_and_failure_isolation(self):
        result = ingest(
            db=self.db,
            feed_entries=self._feeds(),
            settings={"lat": 41.88, "lon": -87.63, "disabled_feeds": []},
            fetch=self._fetch,
            sleeper=lambda _seconds: None,
            now=self.NOW,
        )
        self.assertEqual(result["feeds_ok"], 5)
        self.assertEqual(result["feeds_failed"], 1)
        self.assertEqual(result["docs_added"], 6)

        results = search("battery breakthrough", limit=4, db=self.db, now=self.NOW)
        self.assertEqual(results[0]["source"], "Atom source")
        self.assertLessEqual(len(results[0]["excerpt"]), 700)
        self.assertEqual(results[0]["as_of"], "2026-09-12T09:30:00+00:00")
        self.assertAlmostEqual(results[0]["age_hours"], 2.5)

        current = status(db=self.db, now=self.NOW)
        self.assertTrue(current["built"])
        self.assertEqual(current["docs"], 6)
        self.assertEqual(current["last_run"], "2026-09-12T12:00:00+00:00")
        self.assertIn("Open-Meteo", current["sources"])
        self.assertEqual(age_seconds(db=self.db, now=self.NOW), 0.0)

    def test_prunes_documents_older_than_fourteen_days(self):
        conn = init_db(self.db)
        conn.execute(
            "INSERT INTO docs(id, source, \"group\", title, url, published, fetched, text) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            ("old", "Old source", "news", "Old report", "https://old.test/report",
             "2026-08-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00", "stale material"),
        )
        conn.commit()
        conn.close()

        ingest(db=self.db, feed_entries=[], settings={}, fetch=self._fetch,
               sleeper=lambda _seconds: None, now=self.NOW)
        with sqlite3.connect(self.db) as check:
            self.assertEqual(check.execute("SELECT count(*) FROM docs WHERE id = 'old'").fetchone()[0], 0)

    def test_caps_articles_at_twenty_five_and_spaces_fetches(self):
        items = "".join(
            f"<item><title>Item {number}</title><link>https://article.test/{number}</link></item>"
            for number in range(30)
        )
        feed_xml = f"<rss><channel>{items}</channel></rss>"
        article_fetches = []
        sleeps = []

        def fetch(url):
            if url == "https://feed.test/many":
                return feed_xml
            article_fetches.append(url)
            return ARTICLE_HTML

        result = ingest(
            db=self.db,
            feed_entries=[{"name": "Many", "url": "https://feed.test/many", "group": "news",
                           "enabled": True, "max_items": 100}],
            settings={}, fetch=fetch, sleeper=sleeps.append, now=self.NOW,
        )
        self.assertEqual(result["docs_added"], 25)
        self.assertEqual(len(article_fetches), 25)
        self.assertEqual(sleeps, [0.5] * 24)

    def test_existing_article_url_is_not_fetched_again(self):
        article_fetches = []

        def fetch(url):
            if url == "https://feed.test/rss":
                return RSS_SAMPLE
            article_fetches.append(url)
            return ARTICLE_HTML

        feed = {"name": "RSS source", "url": "https://feed.test/rss", "group": "news",
                "enabled": True, "max_items": 5}
        ingest(db=self.db, feed_entries=[feed], settings={}, fetch=fetch,
               sleeper=lambda _seconds: None, now=self.NOW)
        second = ingest(db=self.db, feed_entries=[feed], settings={}, fetch=fetch,
                        sleeper=lambda _seconds: None, now=self.NOW + dt.timedelta(hours=1))
        self.assertEqual(article_fetches, ["https://example.test/rss-story"])
        self.assertEqual(second["docs_added"], 0)

    def test_config_can_disable_a_feed_without_fetching_it(self):
        def unexpected_fetch(_url):
            self.fail("disabled feed was fetched")

        result = ingest(
            db=self.db,
            feed_entries=[{"name": "Disabled", "url": "https://feed.test/disabled", "group": "news",
                           "enabled": True, "max_items": 5}],
            settings={"disabled_feeds": ["Disabled"]}, fetch=unexpected_fetch,
            sleeper=lambda _seconds: None, now=self.NOW,
        )
        self.assertEqual(result["feeds_ok"], 0)
        self.assertEqual(result["feeds_failed"], 0)

    def test_missing_database_is_not_built(self):
        missing = Path(self.tmp.name) / "missing.db"
        self.assertEqual(search("anything", db=missing), [])
        self.assertEqual(
            status(db=missing),
            {"built": False, "docs": 0, "last_run": None, "age_hours": None, "sources": []},
        )
        self.assertIsNone(age_seconds(db=missing))
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
