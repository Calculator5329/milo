from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Iterable

try:
    from .workspace_book import (
        MAX_FILE_BYTES,
        _Source,
        _database_path,
        _read,
        _search,
        _status,
        _sync,
    )
except ImportError:
    from workspace_book import (  # type: ignore[no-redef]
        MAX_FILE_BYTES,
        _Source,
        _database_path,
        _read,
        _search,
        _status,
        _sync,
    )


DEFAULT_DB = Path("~/.local/state/milo/personal-book.db").expanduser()
DEFAULT_CONFIG = Path("~/.local/state/milo/personal-book.json").expanduser()
DEFAULT_GLOBS = ("**/*.md", "**/*.txt")

ENABLED_TRIGGERS = (
    re.compile(r"\bcheck\s+my\s+notes(?:\s+for)?\b", re.IGNORECASE),
    re.compile(r"\b(?:look\s+)?in\s+my\s+notes(?:\s+for)?\b", re.IGNORECASE),
    re.compile(r"\bfrom\s+my\s+notes\b", re.IGNORECASE),
    re.compile(r"\bsearch\s+my\s+notes\s+for\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+did\s+i\s+write\s+about\b", re.IGNORECASE),
)


def strip_trigger(text: str) -> tuple[str, bool]:
    for pattern in ENABLED_TRIGGERS:
        match = pattern.search(text)
        if match is None:
            continue
        after = text[match.end():].strip()
        before = text[:match.start()].strip()
        query = after or before
        query = re.sub(r"^[\s,:;-]*(?:for|about|on|regarding)\s+", "", query,
                       flags=re.IGNORECASE)
        return query.strip(" ,:;-"), True
    return text, False


def _load_configuration(config_path: Path) -> tuple[list[Path], list[str]]:
    if not config_path.is_file():
        return [], list(DEFAULT_GLOBS)
    payload = json.loads(config_path.read_text())
    roots_value = payload.get("roots", [])
    globs_value = payload.get("globs", list(DEFAULT_GLOBS))
    if not isinstance(roots_value, list) or not all(isinstance(item, str) for item in roots_value):
        raise ValueError("personal book 'roots' must be a list of paths")
    if not isinstance(globs_value, list) or not all(isinstance(item, str) for item in globs_value):
        raise ValueError("personal book 'globs' must be a list of patterns")
    roots = []
    for value in roots_value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = config_path.parent / path
        roots.append(path)
    return roots, globs_value


def _personal_sources(roots: Iterable[Path], globs: Iterable[str]) -> list[_Source]:
    sources: list[_Source] = []
    seen: set[Path] = set()
    for configured_root in roots:
        root = configured_root.resolve()
        if not root.is_dir():
            continue
        repo = root.name or "notes"
        candidates: set[Path] = set()
        for pattern in globs:
            candidates.update(root.glob(pattern))
        for candidate in sorted(candidates):
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError:
                continue
            seen.add(resolved)
            stat = resolved.stat()
            sources.append(_Source(
                key=str(resolved), namespace="personal", repo=repo,
                relative_path=relative, absolute_path=resolved,
                mtime_ns=stat.st_mtime_ns, size=min(stat.st_size, MAX_FILE_BYTES),
            ))
    return sources


def build(roots: Iterable[str | os.PathLike[str]] | None = None,
          db: str | os.PathLike[str] | None = None,
          config: str | os.PathLike[str] | None = None,
          globs: Iterable[str] | None = None) -> dict:
    config_path = Path(config).expanduser() if config is not None else DEFAULT_CONFIG
    configured_roots, configured_globs = _load_configuration(config_path)
    root_paths = ([Path(root).expanduser() for root in roots]
                  if roots is not None else configured_roots)
    patterns = list(globs) if globs is not None else configured_globs
    db_path = _database_path(db, "MILO_PERSONAL_BOOK", DEFAULT_DB)
    return _sync(db_path, _personal_sources(root_paths, patterns))


def status(db: str | os.PathLike[str] | None = None) -> dict:
    return _status(_database_path(db, "MILO_PERSONAL_BOOK", DEFAULT_DB))


class PersonalBook:
    def __init__(self, db: str | os.PathLike[str] | None = None):
        self.db = _database_path(db, "MILO_PERSONAL_BOOK", DEFAULT_DB)

    def search(self, query: str, limit: int = 5) -> dict:
        return _search(self.db, query, limit, workspace_boosts=False)

    def read(self, path: str) -> dict:
        return _read(self.db, path)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and search Milo's opt-in personal book")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("build", help="incrementally build the personal index")
    search_parser = subparsers.add_parser("search", help="search the personal index")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=5)
    subparsers.add_parser("status", help="show index status")
    args = parser.parse_args(argv)
    if args.command == "build":
        result = build()
    elif args.command == "search":
        result = PersonalBook().search(args.query, args.limit)
    else:
        result = status()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
