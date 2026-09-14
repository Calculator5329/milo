"""Persistent, validated Milo preferences shared by every local client."""
from __future__ import annotations

import json
from voice_catalog import VOICE_PRESETS
from model_catalog import MODELS
from speech_audio import settings as voice_settings
from pathlib import Path
from typing import Any

from demo_documents import DocumentError, DocumentWorkspace


PREFERENCES_FILE = "preferences.md"
MAX_UPDATE_ATTEMPTS = 4

DEFAULTS = {
    "voice": "marius",
    "mode": "conversational",
    "sound": "natural",
    "rate": 1,
    "pitch": 0,
    "theme": "workshop",
    "model": "gemma4:12b",
}

ALLOWED = {
    "voice": frozenset(VOICE_PRESETS),
    "mode": frozenset(("conversational", "precise", "brainstorm")),
    "sound": frozenset(("natural", "clear", "small")),
    "theme": frozenset(("workshop", "blueprint", "hifi", "library", "moonroom")),
    "model": frozenset(MODELS),
}


class PreferencesError(DocumentError):
    """A safe, user-facing preference storage or validation failure."""


def _encode(values: dict[str, Any]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"


def _validated(values: Any, *, partial: bool) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise PreferencesError("Preferences must be a JSON object.")

    unknown = set(values) - set(DEFAULTS)
    if unknown:
        raise PreferencesError("Unknown preference field: " + sorted(unknown)[0] + ".")
    if not partial:
        missing = set(DEFAULTS) - set(values)
        if missing:
            raise PreferencesError("Missing preference field: " + sorted(missing)[0] + ".")

    result = dict(values)
    for field, value in result.items():
        if field == "sound" and value == "small-speaker":
            value = "small"
            result[field] = value
        if field in ("rate", "pitch"):
            try:
                result[field] = voice_settings({field:value})[field]
            except ValueError as error:
                raise PreferencesError(str(error)) from None
            continue
        try:
            allowed = value in ALLOWED[field]
        except TypeError:
            allowed = False
        if not allowed:
            raise PreferencesError(f"Unsupported preference value for {field}.")
    return result


class Preferences:
    """Own one retained JSON preference record inside a settings directory."""

    def __init__(self, root: str | Path):
        self.workspace = DocumentWorkspace(root)

    def _document(self) -> dict[str, Any]:
        try:
            return self.workspace.read(PREFERENCES_FILE)
        except DocumentError as error:
            if str(error) != "Document or revision does not exist.":
                raise
        try:
            return self.workspace.create(PREFERENCES_FILE, _encode(DEFAULTS))
        except DocumentError as error:
            if str(error) != "Document already exists; read it before editing.":
                raise
            return self.workspace.read(PREFERENCES_FILE)

    @staticmethod
    def _decode(document: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = json.loads(document["content"])
        except (json.JSONDecodeError, KeyError, TypeError):
            raise PreferencesError("Stored preferences are not valid JSON.") from None
        if isinstance(raw, dict) and 'pitch' not in raw: raw = {**raw, 'pitch': 0}
        if isinstance(raw, dict) and 'model' not in raw: raw = {**raw, 'model': DEFAULTS['model']}
        values = _validated(raw, partial=False)
        return {**values, "revision": document["revision"]}

    def get(self) -> dict[str, Any]:
        """Return the current validated values and their storage revision."""

        return self._decode(self._document())

    def update(self, partial: dict[str, Any]) -> dict[str, Any]:
        """Merge a validated patch, retrying bounded optimistic conflicts."""

        changes = _validated(partial, partial=True)
        for _attempt in range(MAX_UPDATE_ATTEMPTS):
            current = self.get()
            if all(current[field] == value for field, value in changes.items()):
                return current
            merged = {field: current[field] for field in DEFAULTS}
            merged.update(changes)
            try:
                written = self.workspace.replace(
                    PREFERENCES_FILE,
                    _encode(merged),
                    current["revision"],
                )
            except DocumentError as error:
                if str(error) == "Revision conflict: read the document again before editing.":
                    continue
                raise
            return self._decode(written)
        raise PreferencesError("Preferences changed too many times; try again.")
