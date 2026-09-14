from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_ROOT = ""
DEFAULT_DB = Path("~/.local/state/milo/workspace-book.db").expanduser()
MAX_FILE_BYTES = 200 * 1024
EXCLUDED_PATH_PARTS = ("life-kb", ".orc", "node_modules", "evidence")
SEARCH_STOPWORDS = {
    "a", "about", "and", "are", "did", "do", "does", "for", "from", "how",
    "i", "in", "is", "it", "my", "of", "on", "the", "to", "what", "when",
    "where", "why", "with",
}


@dataclass(frozen=True)
class _Source:
    key: str
    namespace: str
    repo: str
    relative_path: str
    absolute_path: Path
    mtime_ns: int
    size: int


_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_files (
    source_key TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    repo TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    absolute_path TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS book_sections USING fts5(
    id UNINDEXED,
    source_key UNINDEXED,
    path UNINDEXED,
    title UNINDEXED,
    repo,
    file_path,
    heading,
    text,
    mtime UNINDEXED,
    absolute_path UNINDEXED,
    tokenize='unicode61'
);
"""


def _database_path(db: str | os.PathLike[str] | None, env_name: str,
                   default: Path) -> Path:
    configured = db if db is not None else os.environ.get(env_name, str(default))
    return Path(configured).expanduser()


def workspace_root(root: str | os.PathLike[str] | None = None) -> Path | None:
    """Resolve the opt-in workspace root, with an empty value meaning disabled."""
    configured = os.environ.get("MILO_WORKSPACE_ROOT", DEFAULT_ROOT) if root is None else os.fspath(root)
    if not str(configured).strip():
        return None
    return Path(configured).expanduser()


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.executescript(_SCHEMA)
    return connection


@contextmanager
def _session(db_path: Path):
    connection = _connect(db_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return value[:80] or "section"


def _sections(relative_path: str, content: str) -> list[tuple[str, str]]:
    heading_pattern = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
    headings = list(heading_pattern.finditer(content))
    sections: list[tuple[str, str]] = []
    if headings and content[:headings[0].start()].strip():
        fallback = Path(relative_path).stem.replace("-", " ").replace("_", " ").strip()
        sections.append((fallback or "Overview", content[:headings[0].start()].strip()))
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        sections.append((match.group(2).strip(), content[match.start():end].strip()))
    if not headings and content.strip():
        fallback = Path(relative_path).stem.replace("-", " ").replace("_", " ").strip()
        sections.append((fallback or "Overview", content.strip()))
    return sections


def _read_text(path: Path) -> str:
    with path.open("rb") as handle:
        return handle.read(MAX_FILE_BYTES).decode("utf-8", "replace")


def _sync(db_path: Path, sources: Iterable[_Source]) -> dict:
    source_list = list(sources)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    changed = 0
    unchanged = 0
    removed = 0
    with _session(db_path) as connection:
        existing = {
            row["source_key"]: row
            for row in connection.execute(
                """SELECT source_key, namespace, repo, relative_path, absolute_path,
                          mtime_ns, size FROM source_files"""
            )
        }
        current_keys = {source.key for source in source_list}
        for key in existing.keys() - current_keys:
            connection.execute("DELETE FROM book_sections WHERE source_key=?", (key,))
            connection.execute("DELETE FROM source_files WHERE source_key=?", (key,))
            removed += 1

        for source in source_list:
            old = existing.get(source.key)
            if (old is not None
                    and old["namespace"] == source.namespace
                    and old["repo"] == source.repo
                    and old["relative_path"] == source.relative_path
                    and old["absolute_path"] == str(source.absolute_path)
                    and int(old["mtime_ns"]) == source.mtime_ns
                    and int(old["size"]) == source.size):
                unchanged += 1
                continue
            content = _read_text(source.absolute_path)
            connection.execute("DELETE FROM book_sections WHERE source_key=?", (source.key,))
            connection.execute(
                """INSERT OR REPLACE INTO source_files
                   (source_key, namespace, repo, relative_path, absolute_path, mtime_ns, size)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (source.key, source.namespace, source.repo, source.relative_path,
                 str(source.absolute_path), source.mtime_ns, source.size),
            )
            slug_counts: dict[str, int] = {}
            for heading, text in _sections(source.relative_path, content):
                base_slug = _slug(heading)
                slug_counts[base_slug] = slug_counts.get(base_slug, 0) + 1
                count = slug_counts[base_slug]
                slug = base_slug if count == 1 else f"{base_slug}-{count}"
                citation_path = (
                    f"{source.namespace}/{source.repo}/{source.relative_path}#{slug}"
                )
                section_id = hashlib.sha256(
                    f"{source.key}\0{slug}".encode("utf-8")
                ).hexdigest()
                connection.execute(
                    """INSERT INTO book_sections
                       (id, source_key, path, title, repo, file_path,
                        heading, text, mtime, absolute_path)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (section_id, source.key, citation_path,
                     f"{source.repo}: {heading}", source.repo, source.relative_path,
                     heading, text, source.mtime_ns, str(source.absolute_path)),
                )
            changed += 1
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('built_at', ?)", (now,)
        )
    return {
        **_status(db_path),
        "files_changed": changed,
        "files_unchanged": unchanged,
        "files_removed": removed,
    }


def _status(db_path: Path) -> dict:
    if not db_path.is_file():
        return {"docs": 0, "repos": 0, "built_at": None}
    with _session(db_path) as connection:
        docs = connection.execute("SELECT COUNT(*) FROM book_sections").fetchone()[0]
        repos = connection.execute(
            "SELECT COUNT(DISTINCT repo) FROM source_files"
        ).fetchone()[0]
        row = connection.execute(
            "SELECT value FROM metadata WHERE key='built_at'"
        ).fetchone()
    return {"docs": docs, "repos": repos, "built_at": row[0] if row else None}


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[\w.-]+", query, flags=re.UNICODE)[:20]
    useful = [term for term in terms if term.casefold() not in SEARCH_STOPWORDS]
    return useful or terms


def _snippet(text: str, terms: list[str], length: int = 500) -> str:
    compact = " ".join(text.split())
    if len(compact) <= length:
        return compact
    folded = compact.casefold()
    positions = [folded.find(term.casefold()) for term in terms]
    positions = [position for position in positions if position >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - length // 4)
    end = min(len(compact), start + length)
    prefix = "... " if start else ""
    suffix = " ..." if end < len(compact) else ""
    return prefix + compact[start:end].strip() + suffix


def _search(db_path: Path, query: str, limit: int, workspace_boosts: bool) -> dict:
    started = time.perf_counter()
    if not query.strip():
        raise ValueError("query must not be empty")
    limit = max(1, min(int(limit), 50))
    terms = _query_terms(query)
    expression = " OR ".join(
        f'"{term.replace(chr(34), "")}"' for term in terms
    )
    if not db_path.is_file():
        return {"matches": [], "elapsed_ms": int((time.perf_counter() - started) * 1000)}
    with _session(db_path) as connection:
        rows = connection.execute(
            """SELECT path, title, repo, file_path, heading, text,
                      mtime, absolute_path,
                      bm25(book_sections, 0.0, 0.0, 0.0, 0.0, 8.0, 5.0, 7.0, 1.0)
                          AS rank
               FROM book_sections WHERE book_sections MATCH ?
               ORDER BY rank LIMIT 500""",
            (expression,),
        ).fetchall()

    query_folded = query.casefold()
    wanted = {term.casefold() for term in terms}
    status_question = "status" in wanted
    ranked: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        repo = row["repo"].casefold()
        file_path = row["file_path"].casefold()
        heading = row["heading"].casefold()
        text = row["text"].casefold()
        score = max(0.0, -float(row["rank"]))
        score += sum(0.35 for term in wanted if term in text)
        score += sum(1.5 for term in wanted if term in heading)
        score += sum(2.5 for term in wanted if term in file_path)
        score += sum(6.0 for term in wanted if term in repo)
        if workspace_boosts and status_question:
            if Path(file_path).name.casefold() == "status.md":
                score += 8.0
            if file_path == "docs/roadmap.md" and re.search(r"\bnow\b", heading):
                score += 6.0
        if query_folded in f"{repo} {file_path} {heading} {text}":
            score += 2.0
        ranked.append((score, row))
    ranked.sort(key=lambda item: (-item[0], item[1]["path"]))

    matches = []
    for score, row in ranked[:limit]:
        timestamp = datetime.fromtimestamp(
            int(row["mtime"]) / 1_000_000_000, timezone.utc
        ).isoformat().replace("+00:00", "Z")
        matches.append({
            "title": row["title"],
            "path": row["path"],
            "snippet": _snippet(row["text"], terms),
            "score": float(score),
            "as_of": timestamp,
        })
    return {"matches": matches,
            "elapsed_ms": int((time.perf_counter() - started) * 1000)}


def _read(db_path: Path, path: str) -> dict:
    if not db_path.is_file():
        raise KeyError(path)
    with _session(db_path) as connection:
        row = connection.execute(
            "SELECT path, absolute_path, text FROM book_sections WHERE path=?",
            (path,),
        ).fetchone()
    if row is None:
        raise KeyError(path)
    return {
        "path": row["path"],
        "uri": Path(row["absolute_path"]).as_uri(),
        "content": row["text"],
    }


def _is_excluded(path: Path) -> bool:
    folded_parts = [part.casefold() for part in path.parts]
    return any(
        excluded in part
        for part in folded_parts
        for excluded in EXCLUDED_PATH_PARTS
    )


def _workspace_sources(root: Path, manifest_path: Path) -> list[_Source]:
    payload = json.loads(manifest_path.read_text())
    repos = payload.get("repos", [])
    if not isinstance(repos, list):
        raise ValueError("workspace manifest 'repos' must be a list")
    root = root.resolve()
    sources: list[_Source] = []
    for entry in repos:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        manifest_path_value = entry["path"]
        if _is_excluded(Path(manifest_path_value)):
            continue
        repo_root = (root / manifest_path_value).resolve()
        try:
            repo_root.relative_to(root)
        except ValueError:
            continue
        repo = str(entry.get("name") or Path(manifest_path_value).name)
        candidates = [
            repo_root / "docs" / "roadmap.md",
            repo_root / "docs" / "changelog.md",
            repo_root / "STATUS.md",
            repo_root / "README.md",
            repo_root / "CLAUDE.md",
        ]
        docs = repo_root / "docs"
        if docs.is_dir():
            candidates.extend(docs.glob("*.md"))
        for candidate in sorted(set(candidates)):
            if _is_excluded(candidate) or not candidate.is_file():
                continue
            resolved = candidate.resolve()
            try:
                relative = resolved.relative_to(repo_root).as_posix()
            except ValueError:
                continue
            stat = resolved.stat()
            sources.append(_Source(
                key=str(resolved), namespace="workspace", repo=repo,
                relative_path=relative, absolute_path=resolved,
                mtime_ns=stat.st_mtime_ns, size=stat.st_size,
            ))
    return sources


def build(root: str | os.PathLike[str] | None = None,
          db: str | os.PathLike[str] | None = None,
          manifest: str | os.PathLike[str] | None = None) -> dict:
    root_path = workspace_root(root)
    if root_path is None:
        return {"docs": 0, "repos": 0, "built_at": None,
                "files_changed": 0, "files_unchanged": 0, "files_removed": 0,
                "disabled": True}
    manifest_path = Path(manifest).expanduser() if manifest is not None else root_path / "workspace.json"
    db_path = _database_path(db, "MILO_WORKSPACE_BOOK", DEFAULT_DB)
    return _sync(db_path, _workspace_sources(root_path, manifest_path))


def status(db: str | os.PathLike[str] | None = None) -> dict:
    return _status(_database_path(db, "MILO_WORKSPACE_BOOK", DEFAULT_DB))


class WorkspaceBook:
    def __init__(self, db: str | os.PathLike[str] | None = None):
        self.db = _database_path(db, "MILO_WORKSPACE_BOOK", DEFAULT_DB)
        self.enabled = workspace_root() is not None

    def search(self, query: str, limit: int = 5) -> dict:
        if not self.enabled:
            return {"matches": [], "elapsed_ms": 0, "disabled": True}
        return _search(self.db, query, limit, workspace_boosts=True)

    def read(self, path: str) -> dict:
        if not self.enabled:
            raise KeyError(path)
        return _read(self.db, path)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and search Milo's workspace book")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("build", help="incrementally build the workspace index")
    search_parser = subparsers.add_parser("search", help="search the workspace index")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=5)
    subparsers.add_parser("status", help="show index status")
    args = parser.parse_args(argv)
    if args.command == "build":
        result = build()
    elif args.command == "search":
        result = WorkspaceBook().search(args.query, args.limit)
    else:
        result = status()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
