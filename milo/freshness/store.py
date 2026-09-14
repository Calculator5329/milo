"""SQLite schema and document writes for the freshness index."""

import hashlib
import sqlite3

from .config import database_path


SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    "group" TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published TEXT,
    fetched TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS docs_url_idx ON docs(url);
CREATE INDEX IF NOT EXISTS docs_fetched_idx ON docs(fetched);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    title, text, source, "group", content='docs', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
    INSERT INTO docs_fts(rowid, title, text, source, "group")
    VALUES (new.rowid, new.title, new.text, new.source, new."group");
END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, title, text, source, "group")
    VALUES ('delete', old.rowid, old.title, old.text, old.source, old."group");
END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, title, text, source, "group")
    VALUES ('delete', old.rowid, old.title, old.text, old.source, old."group");
    INSERT INTO docs_fts(rowid, title, text, source, "group")
    VALUES (new.rowid, new.title, new.text, new.source, new."group");
END;
CREATE TABLE IF NOT EXISTS runs (
    started TEXT NOT NULL,
    finished TEXT NOT NULL,
    feeds_ok INTEGER NOT NULL,
    feeds_failed INTEGER NOT NULL,
    docs_added INTEGER NOT NULL
);
"""


def init_db(db=None):
    path = database_path(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def document_id(url, suffix=""):
    return hashlib.sha256((url + suffix).encode("utf-8")).hexdigest()


def url_exists(connection, url):
    return connection.execute("SELECT 1 FROM docs WHERE url = ? LIMIT 1", (url,)).fetchone() is not None


def insert_document(connection, document):
    cursor = connection.execute(
        'INSERT OR IGNORE INTO docs(id, source, "group", title, url, published, fetched, text) '
        "VALUES(:id, :source, :group, :title, :url, :published, :fetched, :text)",
        document,
    )
    return cursor.rowcount == 1
