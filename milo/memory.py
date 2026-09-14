"""Bounded, curator-owned cross-session memory for Milo."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import tempfile
import threading
from typing import Any, Sequence
import urllib.request


DEFAULT_MEMORY_PATH = Path.home() / ".local" / "state" / "milo" / "memory.md"
DEFAULT_INDEX_PATH = (
    Path.home() / ".local" / "state" / "milo" / "memory-index.json"
)
MAX_EMBED_SECONDS = 0.8
MEMORY_HEADER = "Notes about the user (curated):"
SEED_TEMPLATE = """## Identity
- [Preferred name or form of address]
- [Stable identity note]

## Projects
- [Current project note]

## Preferences
- [Stable preference]

## Standing reminders
- [Standing reminder]
"""

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "hers", "him",
    "his", "i", "in", "is", "it", "its", "me", "my", "of", "on",
    "or", "our", "ours", "she", "that", "the", "their", "theirs",
    "them", "they", "this", "to", "us", "was", "we", "were", "with",
    "you", "your", "yours",
})


def _memory_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    configured = os.environ.get("MILO_MEMORY")
    return Path(configured).expanduser() if configured else DEFAULT_MEMORY_PATH


def _index_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    configured = os.environ.get("MILO_MEMORY_INDEX")
    return Path(configured).expanduser() if configured else DEFAULT_INDEX_PATH


def _line_key(section: str, text: str) -> str:
    source = f"{section}\0{text}".encode("utf-8")
    return hashlib.sha256(source).hexdigest()


def load(path: str | os.PathLike[str] | None = None) -> list[dict[str, str]]:
    """Load bullet notes under Markdown level-two sections."""

    notes_path = _memory_path(path)
    try:
        source = notes_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []

    result: list[dict[str, str]] = []
    section: str | None = None
    for raw_line in source.splitlines():
        if raw_line.startswith("## "):
            section = raw_line[3:].strip() or None
            continue
        if section is None or not raw_line.startswith("- "):
            continue
        text = raw_line[2:].strip()
        if text and not _is_placeholder(text):
            result.append({
                "section": section,
                "text": text,
                "key": _line_key(section, text),
            })
    return result


def _is_placeholder(text: str) -> bool:
    """Template lines such as ``[Stable preference]`` are prompts to the curator, not notes."""

    return text.startswith("[") and text.endswith("]")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary.name, 0o600)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def init(path: str | os.PathLike[str] | None = None) -> Path:
    """Create a private placeholder-only memory template without overwriting."""

    notes_path = _memory_path(path)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(notes_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(SEED_TEMPLATE)
    except BaseException:
        try:
            notes_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return notes_path


def _validate_note(section: str, text: str) -> tuple[str, str]:
    if not isinstance(section, str) or not section.strip() or "\n" in section:
        raise ValueError("Section must be one non-empty line.")
    if not isinstance(text, str) or not text.strip() or "\n" in text:
        raise ValueError("Note text must be one non-empty line.")
    clean_section = section.strip()
    clean_text = text.strip()
    if clean_section.startswith("#"):
        raise ValueError("Section must not start with a Markdown heading marker.")
    return clean_section, clean_text


def add(
    section: str,
    text: str,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Add one curator-provided note and return its parsed representation."""

    clean_section, clean_text = _validate_note(section, text)
    notes_path = _memory_path(path)
    existing = load(notes_path)
    for line in existing:
        if (
            line["section"].casefold() == clean_section.casefold()
            and line["text"] == clean_text
        ):
            return line

    try:
        source = notes_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        source = ""
    lines = source.splitlines()

    heading_index: int | None = None
    canonical_section = clean_section
    for index, line in enumerate(lines):
        if line.startswith("## ") and line[3:].strip().casefold() == clean_section.casefold():
            heading_index = index
            canonical_section = line[3:].strip()
            break

    if heading_index is None:
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            lines.append("")
        lines.extend((f"## {clean_section}", f"- {clean_text}"))
    else:
        insertion = len(lines)
        for index in range(heading_index + 1, len(lines)):
            if lines[index].startswith("## "):
                insertion = index
                break
        while insertion > heading_index + 1 and not lines[insertion - 1].strip():
            insertion -= 1
        lines.insert(insertion, f"- {clean_text}")

    _atomic_write(notes_path, "\n".join(lines).rstrip() + "\n")
    return {
        "section": canonical_section,
        "text": clean_text,
        "key": _line_key(canonical_section, clean_text),
    }


def remove(
    key: str,
    path: str | os.PathLike[str] | None = None,
) -> bool:
    """Remove every note matching a stable line key."""

    if not isinstance(key, str) or not key:
        raise ValueError("A note key is required.")
    notes_path = _memory_path(path)
    try:
        source = notes_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False

    kept: list[str] = []
    section: str | None = None
    removed = False
    for raw_line in source.splitlines():
        if raw_line.startswith("## "):
            section = raw_line[3:].strip() or None
        if section is not None and raw_line.startswith("- "):
            text = raw_line[2:].strip()
            if text and _line_key(section, text) == key:
                removed = True
                continue
        kept.append(raw_line)

    if removed:
        _atomic_write(notes_path, "\n".join(kept).rstrip() + "\n")
    return removed


def _stem(token: str) -> str:
    if token.endswith("ing") and len(token) > 5:
        token = token[:-3]
        if len(token) > 2 and token[-1] == token[-2]:
            token = token[:-1]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        token = token[:-1]
    return token


def _tokens(text: str) -> set[str]:
    return {
        _stem(token)
        for token in _TOKEN_RE.findall(text.lower())
        if token not in _STOPWORDS
    }


class TokenOverlapScorer:
    """Dependency-free relevance scorer used when embeddings are unavailable."""

    def score(self, query: str, lines: Sequence[dict[str, str]]) -> list[float]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return [0.0] * len(lines)
        result = []
        for line in lines:
            line_tokens = _tokens(line["text"])
            if not line_tokens:
                result.append(0.0)
                continue
            overlap = len(query_tokens & line_tokens)
            result.append(overlap / math.sqrt(len(query_tokens) * len(line_tokens)))
        return result


class OllamaEmbedder:
    """Score memory lines with cached Ollama embeddings and a bounded fallback."""

    def __init__(
        self,
        model: str = "nomic-embed-text",
        url: str = "http://127.0.0.1:11434",
        *,
        cache_path: str | os.PathLike[str] | None = None,
        timeout: float = MAX_EMBED_SECONDS,
        fallback: TokenOverlapScorer | None = None,
    ):
        if not isinstance(model, str) or not model:
            raise ValueError("Embedding model is required.")
        if not isinstance(url, str) or not url:
            raise ValueError("Ollama URL is required.")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("Embedding timeout must be positive.")
        self.model = model
        self.url = url.rstrip("/")
        self.cache_path = _index_path(cache_path)
        self.timeout = min(float(timeout), MAX_EMBED_SECONDS)
        self.fallback = fallback or TokenOverlapScorer()
        self.used_fallback = False

    def _read_cache(self) -> dict[str, list[float]]:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeError):
            return {}
        if not isinstance(payload, dict) or payload.get("model") != self.model:
            return {}
        raw_vectors = payload.get("vectors")
        if not isinstance(raw_vectors, dict):
            return {}
        vectors: dict[str, list[float]] = {}
        for key, raw_vector in raw_vectors.items():
            if not isinstance(key, str) or not isinstance(raw_vector, list) or not raw_vector:
                continue
            if any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                for value in raw_vector
            ):
                continue
            vectors[key] = [float(value) for value in raw_vector]
        return vectors

    def _write_cache(self, vectors: dict[str, list[float]]) -> None:
        payload = json.dumps(
            {"version": 1, "model": self.model, "vectors": vectors},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        _atomic_write(self.cache_path, payload + "\n")

    def _request_embeddings(
        self,
        texts: Sequence[str],
        timeout: float,
    ) -> list[list[float]]:
        body = json.dumps(
            {"model": self.model, "input": list(texts)},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(10_000_001)
        if len(raw) > 10_000_000:
            raise ValueError("Ollama embedding response is too large.")
        payload = json.loads(raw.decode("utf-8"))
        embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise ValueError("Ollama returned an invalid embedding count.")
        clean: list[list[float]] = []
        for raw_vector in embeddings:
            if not isinstance(raw_vector, list) or not raw_vector:
                raise ValueError("Ollama returned an invalid embedding vector.")
            vector = []
            for value in raw_vector:
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(value)
                ):
                    raise ValueError("Ollama returned a non-finite embedding value.")
                vector.append(float(value))
            clean.append(vector)
        return clean

    def _bounded_embeddings(self, texts: Sequence[str]) -> list[list[float]]:
        outcome: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def request() -> None:
            try:
                outcome.put((True, self._request_embeddings(texts, self.timeout)))
            except Exception as exc:
                outcome.put((False, exc))

        worker = threading.Thread(target=request, name="milo-memory-embed", daemon=True)
        worker.start()
        worker.join(self.timeout)
        if worker.is_alive():
            raise TimeoutError("Ollama embedding request timed out.")
        successful, value = outcome.get_nowait()
        if not successful:
            raise value
        return value

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right) or not left:
            raise ValueError("Embedding dimensions do not match.")
        left_size = math.sqrt(sum(value * value for value in left))
        right_size = math.sqrt(sum(value * value for value in right))
        if not left_size or not right_size:
            return 0.0
        return sum(a * b for a, b in zip(left, right)) / (left_size * right_size)

    def score(self, query: str, lines: Sequence[dict[str, str]]) -> list[float]:
        note_lines = list(lines)
        if not note_lines:
            self.used_fallback = False
            return []

        cached = self._read_cache()
        keys = [line["key"] for line in note_lines]
        missing: list[dict[str, str]] = []
        seen_missing: set[str] = set()
        for line in note_lines:
            key = line["key"]
            if key not in cached and key not in seen_missing:
                missing.append(line)
                seen_missing.add(key)

        inputs = [query, *(line["text"] for line in missing)]
        try:
            embeddings = self._bounded_embeddings(inputs)
            query_vector = embeddings[0]
            for line, vector in zip(missing, embeddings[1:]):
                cached[line["key"]] = vector
            current_vectors = {key: cached[key] for key in dict.fromkeys(keys)}
            scores = [
                self._cosine(query_vector, current_vectors[key])
                for key in keys
            ]
        except Exception:
            self.used_fallback = True
            return self.fallback.score(query, note_lines)

        self.used_fallback = False
        self._write_cache(current_vectors)
        return scores


def _context_measurement(content: str) -> int:
    try:
        from conversation import _context_units
    except ImportError:
        from .conversation import _context_units
    return _context_units([{"role": "user", "content": content}])


def _render(lines: Sequence[dict[str, str]]) -> str:
    return "\n".join((MEMORY_HEADER, *(f"- {line['text']}" for line in lines)))


def recall(
    recent_turns: list[str],
    budget_units: int = 400,
    scorer: Any = None,
    path: str | os.PathLike[str] | None = None,
) -> str:
    """Return the most relevant curated notes inside a conservative unit budget."""

    if not isinstance(recent_turns, list) or any(
        not isinstance(turn, str) for turn in recent_turns
    ):
        raise ValueError("Recent turns must be a list of strings.")
    if not isinstance(budget_units, int) or isinstance(budget_units, bool) or budget_units <= 0:
        raise ValueError("Memory budget must be a positive integer.")
    lines = load(path)
    if not lines:
        return ""

    query = "\n".join(turn.strip() for turn in recent_turns[-3:])
    active_scorer = scorer or OllamaEmbedder()
    try:
        scores = list(active_scorer.score(query, lines))
        if len(scores) != len(lines) or any(
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(score)
            for score in scores
        ):
            raise ValueError("Scorer returned invalid scores.")
    except Exception:
        scores = TokenOverlapScorer().score(query, lines)

    identity_indexes = [
        index
        for index, line in enumerate(lines)
        if line["section"].casefold() == "identity"
    ][:2]
    required = set(identity_indexes)
    ranked_indexes = sorted(
        (index for index in range(len(lines)) if index not in required),
        key=lambda index: (-scores[index], index),
    )

    chosen: list[dict[str, str]] = []
    for index in identity_indexes:
        candidate = [*chosen, lines[index]]
        if _context_measurement(_render(candidate)) > budget_units:
            return ""
        chosen = candidate

    for index in ranked_indexes:
        candidate = [*chosen, lines[index]]
        if _context_measurement(_render(candidate)) <= budget_units:
            chosen = candidate

    if not chosen:
        return ""
    return _render(chosen)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create the placeholder notes file.")
    commands.add_parser("list", help="List curated notes and their keys.")

    add_parser = commands.add_parser("add", help="Add one curated note.")
    add_parser.add_argument("section")
    add_parser.add_argument("text")

    remove_parser = commands.add_parser("remove", help="Remove a note by key.")
    remove_parser.add_argument("key")

    recall_parser = commands.add_parser("recall", help="Recall notes for a query.")
    recall_parser.add_argument("query")
    recall_parser.add_argument("--budget", type=int, default=400)

    commands.add_parser("reindex", help="Rebuild the Ollama embedding cache.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "init":
        try:
            path = init()
        except FileExistsError:
            print(f"Memory file already exists: {_memory_path()}")
            return 1
        print(f"Initialized {_memory_path(path)}")
        return 0
    if args.command == "list":
        for line in load():
            print(f"{line['key']} {line['section']}: {line['text']}")
        return 0
    if args.command == "add":
        line = add(args.section, args.text)
        print(f"Added {line['key']}")
        return 0
    if args.command == "remove":
        if not remove(args.key):
            print(f"No note found for key {args.key}")
            return 1
        print(f"Removed {args.key}")
        return 0
    if args.command == "recall":
        result = recall([args.query], budget_units=args.budget)
        if result:
            print(result)
        return 0
    if args.command == "reindex":
        index = _index_path()
        try:
            index.unlink()
        except FileNotFoundError:
            pass
        lines = load()
        embedder = OllamaEmbedder(cache_path=index)
        embedder.score("curated user memory", lines)
        if embedder.used_fallback and lines:
            print("Ollama embeddings unavailable, index was not rebuilt.")
            return 1
        print(f"Indexed {len(lines)} note line(s) in {index}")
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
