from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path


class AdapterError(RuntimeError):
    pass


KIWIX_BOOK_ROUTES = (
    ("wiktionary_en_simple_all_nopic_2026-04", re.compile(
        r"\b(define|definition|mean|meaning|etymology|pronunciation|word for)\b", re.I)),
    ("ifixit_en_all_2025-12", re.compile(
        r"\b(repair|replace|cracked|swollen.{0,20}battery|joystick drift|cooling fan|coffee maker)\b", re.I)),
    ("stackoverflow.com_en_all_2023-11", re.compile(
        r"\b(python|javascript|typescript|rust|golang|java|c\+\+|sql|api|exception|stack trace|code)\b", re.I)),
    ("unix.stackexchange.com_en_all_2026-02", re.compile(
        r"\b(bash|shell|systemd|chmod|grep|btrfs|linux|unix|stdout|stderr|mount|sudo)\b", re.I)),
    ("superuser.com_en_all_2026-02", re.compile(
        r"\b(windows|ntfs|dual boot|router|usb|monitor|display resolution|wifi)\b", re.I)),
    ("gutenberg_en_lcc-q_2026-03", re.compile(
        r"\b(book|gutenberg|darwin|astronomy|natural history|classic text)\b", re.I)),
)


def suggest_kiwix_books(query: str) -> list[str]:
    """Return conservative native-index routes; an empty list means global search."""
    return [book for book, pattern in KIWIX_BOOK_ROUTES if pattern.search(query)]


def normalize_kiwix_query(query: str, books: list[str] | None) -> str:
    normalized = query
    capital = re.search(r"\bcapital of ([A-Za-z .-]+)", normalized, re.I)
    if capital:
        normalized = capital.group(1)
    if books and "wiktionary_en_simple_all_nopic_2026-04" in books:
        normalized = re.sub(
            r"\b(?:what is the |define |definition of |meaning of )", "", normalized,
            flags=re.I,
        )
    stopwords = {
        "a", "about", "and", "are", "by", "can", "did", "do", "does", "for",
        "how", "i", "in", "is", "it", "of", "should", "take", "the", "to",
        "what", "when", "where", "why", "with",
    }
    words = re.findall(r"[A-Za-z0-9+#.-]+", normalized)
    compact = [word for word in words if word.casefold() not in stopwords]
    normalized = " ".join(compact)
    return normalized.strip(" ?.!") or query


def _rerank_kiwix(matches: list[dict], query: str) -> list[dict]:
    wanted = {word.casefold() for word in re.findall(r"[A-Za-z0-9+#]+", query)
              if len(word) > 2}
    def relevance(pair) -> tuple[float, float]:
        rank, match = pair
        title = set(re.findall(r"[A-Za-z0-9+#]+", match.get("title", "").casefold()))
        excerpt = set(re.findall(r"[A-Za-z0-9+#]+", match.get("excerpt", "").casefold()))
        title_coverage = len(wanted & title) / max(1, len(wanted))
        excerpt_coverage = len(wanted & excerpt) / max(1, len(wanted))
        return (title_coverage * 4 + excerpt_coverage, -rank)
    return [match for _, match in sorted(enumerate(matches), key=relevance, reverse=True)]


@dataclass(frozen=True)
class KiwixAdapter:
    executable: str = "kiwix-search"
    timeout: int = 30

    def resolved_executable(self) -> str | None:
        configured = shutil.which(self.executable)
        if configured:
            return configured
        root = Path(os.environ.get("LOCAL_AI_LIBRARY_ROOT", "~/Library")).expanduser()
        candidates = sorted(
            root.glob("sources/kiwix/tools/runtime/*/kiwix-search"), reverse=True
        )
        return str(candidates[0]) if candidates else None

    def available(self) -> bool:
        return self.resolved_executable() is not None

    def search(self, zim: Path, query: str, limit: int = 10) -> dict:
        if not zim.is_file() or zim.suffix.lower() != ".zim":
            raise AdapterError(f"not a ZIM file: {zim}")
        executable = self.resolved_executable()
        if not executable:
            raise AdapterError("kiwix-search is not installed")
        completed = subprocess.run(
            [executable, str(zim), query], capture_output=True, text=True,
            timeout=self.timeout, check=False, shell=False,
        )
        if completed.returncode != 0:
            raise AdapterError((completed.stderr or completed.stdout).strip()[:2000])
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        return {"zim": str(zim.resolve()), "query": query, "matches": lines[:max(1, min(limit, 50))]}


class _KiwixResultsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.matches: list[dict] = []
        self.current: dict | None = None
        self.field: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        if tag == "li":
            self.current = {"title": "", "excerpt": "", "book": ""}
        elif self.current is not None and tag == "a" and attributes.get("href", "").startswith("/content/"):
            self.current["path"] = attributes["href"]
            self.field = "title"
        elif self.current is not None and tag == "cite":
            self.field = "excerpt"
        elif self.current is not None and tag == "div" and attributes.get("class") == "book-title":
            self.field = "book"

    def handle_endtag(self, tag: str) -> None:
        if tag in {"a", "cite", "div"}:
            self.field = None
        if tag == "li" and self.current is not None:
            if self.current.get("path"):
                self.matches.append({key: " ".join(value.split()) if isinstance(value, str) else value
                                     for key, value in self.current.items()})
            self.current = None

    def handle_data(self, data: str) -> None:
        if self.current is not None and self.field:
            self.current[self.field] += data


@dataclass(frozen=True)
class KiwixHttpAdapter:
    base_url: str = "http://127.0.0.1:8891"
    timeout: int = 15

    def _get(self, path: str) -> str:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=self.timeout) as response:
                    return response.read(2_000_000).decode("utf-8", "replace")
            except urllib.error.URLError as exc:
                last_error = exc
                time.sleep(0.2 * (attempt + 1))
        raise last_error if last_error else AdapterError("Kiwix HTTP request failed")

    def search(self, query: str, limit: int = 10, books: list[str] | None = None) -> dict:
        native_query = normalize_kiwix_query(query, books)
        parameters: list[tuple[str, str]] = [("pattern", native_query)]
        parameters.extend(("books.name", book) for book in (books or []))
        raw = self._get("/search?" + urllib.parse.urlencode(parameters))
        parser = _KiwixResultsParser()
        parser.feed(raw)
        matches = []
        ranked = _rerank_kiwix(parser.matches, native_query)
        for rank, match in enumerate(ranked[:max(1, min(limit, 50))], 1):
            matches.append({
                **match,
                "score": 1.0 / (60 + rank),
                "uri": f"kiwix://{match['path'].removeprefix('/content/')}",
                "local_url": f"{self.base_url}{match['path']}",
            })
        return {"query": query, "native_query": native_query, "books": books or [], "matches": matches}

    def read(self, path: str) -> dict:
        if not path.startswith("/content/") or ".." in path:
            raise AdapterError("Kiwix article path must start with /content/")
        from .extract import html_to_text
        content = html_to_text(self._get(path))[:50_000]
        return {"path": path, "uri": f"kiwix://{path.removeprefix('/content/')}", "content": content}


@dataclass(frozen=True)
class ManAdapter:
    timeout: int = 15

    def page(self, name: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_.:+-]{1,100}", name):
            raise AdapterError("invalid man-page name")
        completed = subprocess.run(
            ["man", "--pager=cat", name], capture_output=True, text=True,
            timeout=self.timeout, check=False, shell=False,
            env={"MANWIDTH": "100", "PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
        if completed.returncode != 0:
            raise AdapterError((completed.stderr or completed.stdout).strip()[:2000])
        return {"name": name, "content": completed.stdout[:40_000], "citation": f"man:{name}"}


class ReadOnlySQLiteAdapter:
    MAX_ROWS = 200
    MAX_SQL_CHARS = 10_000
    ALLOWED_PREFIXES = ("select", "with", "pragma table_info", "pragma table_xinfo", "pragma foreign_key_list")

    def __init__(self, path: Path):
        self.path = path.resolve()
        if not self.path.is_file():
            raise AdapterError(f"database does not exist: {self.path}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.set_authorizer(self._authorize)
        connection.set_progress_handler(lambda: 1, 2_000_000)
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _authorize(action: int, _one, _two, _db, _trigger) -> int:
        allowed = {
            sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
            sqlite3.SQLITE_PRAGMA, sqlite3.SQLITE_RECURSIVE,
        }
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

    def schema(self) -> dict:
        with self._session() as db:
            tables = db.execute(
                "SELECT name, type, sql FROM sqlite_master "
                "WHERE type IN ('table','view') ORDER BY name"
            ).fetchall()
        return {"database": str(self.path), "objects": [dict(row) for row in tables]}

    def health(self) -> dict:
        with self._session() as db:
            rows = [row[0] for row in db.execute("PRAGMA quick_check(1)").fetchall()]
        return {"database": str(self.path), "ok": rows == ["ok"], "detail": rows[:10]}

    def query(self, sql: str) -> dict:
        normalized = " ".join(sql.strip().split()).lower()
        if not normalized.startswith(self.ALLOWED_PREFIXES):
            raise AdapterError("only read-only SELECT, WITH, and schema PRAGMA statements are allowed")
        if len(sql) > self.MAX_SQL_CHARS or ";" in sql.rstrip(";"):
            raise AdapterError("query is too large or contains multiple statements")
        with self._session() as db:
            cursor = db.execute(sql)
            columns = [column[0] for column in cursor.description or []]
            rows = [dict(row) for row in cursor.fetchmany(self.MAX_ROWS + 1)]
        truncated = len(rows) > self.MAX_ROWS
        return {
            "database": str(self.path), "columns": columns,
            "rows": rows[:self.MAX_ROWS], "truncated": truncated,
        }
