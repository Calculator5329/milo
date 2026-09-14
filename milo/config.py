"""Milo settings: built-in defaults, optional JSON file, then ``MILO_*`` overrides."""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "milo.config.json"


def _data_dir() -> Path:
    """Return Milo's per-user data directory without relying on HOME being set."""
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        return Path(local) / "Milo" if local else Path.home() / "AppData" / "Local" / "Milo"
    return Path.home() / ".local" / "share" / "milo"


DATA_DIR = _data_dir()
STATE_DIR = Path.home() / ".local" / "state" / "milo"

# The menu shown in Settings. Keys are Ollama tags and values are visible labels.
DEFAULT_MODELS = {
    "gemma4:12b": "Quick · Gemma 4",
    "gpt-oss:20b": "Careful · GPT OSS",
    "phi4:latest": "Balanced · Phi-4",
    "gemma4:4b": "Light · Gemma 4 4B",
}

DEFAULT_VOICES = [
    "marius", "javert", "bill_boerst", "stuart_bell", "alba", "cosette",
    "fantine", "eponine", "jean", "anna", "peter_yearsley", "caro_davy",
]

DEFAULTS = {
    # user_name: Spoken owner name, or an empty string when Milo should not use one.
    "user_name": "",
    # model: Ollama tag selected when the notebook opens and during warmup.
    "model": "gemma4:12b",
    # models: Object mapping each offered Ollama tag to its notebook label.
    "models": DEFAULT_MODELS,
    # voice: Pocket TTS preset selected when the notebook opens and during warmup.
    "voice": "marius",
    # voices: Pocket TTS presets cached by setup-voices and offered by the engine.
    "voices": DEFAULT_VOICES,
    # port: Loopback TCP port used by the demo server.
    "port": 8766,
    # ollama_url: Loopback base URL for the Ollama API.
    "ollama_url": "http://127.0.0.1:11434",
    # whisper_url: Loopback base URL for the whisper.cpp server.
    "whisper_url": "http://127.0.0.1:8178",
    # models_dir: Pocket TTS model cache and exported voice-state directory.
    "models_dir": str(REPO_ROOT / "models"),
    # documents_dir: Notebook document directory.
    "documents_dir": str(DATA_DIR / "documents"),
    # settings_dir: Notebook preference and UI settings directory.
    "settings_dir": str(DATA_DIR / "settings"),
    # memory_path: Markdown notes file the person edits; Milo recalls from it.
    "memory_path": str(STATE_DIR / "memory.md"),
    # reminders_path: Retained reminders JSON path.
    "reminders_path": str(STATE_DIR / "reminders.json"),
    # turn_log: Local turn ledger JSONL path.
    "turn_log": str(STATE_DIR / "turns.jsonl"),
    # freshness_db: SQLite path for the recent-information index.
    "freshness_db": str(STATE_DIR / "freshness.db"),
    # kiwix_url: Loopback Kiwix base URL, or empty to turn the offline library off.
    "kiwix_url": "",
    # workspace_root: Workspace manifest root, or empty to turn the workspace book off.
    "workspace_root": "",
    # personal_book: Personal book configuration JSON path, or empty to turn it off.
    "personal_book": "",
    # silence_ms: End-of-turn silence gate in milliseconds.
    "silence_ms": 350,
    # tts_threads: CPU thread count reserved for Pocket TTS.
    "tts_threads": 2,
}

# Engine-facing names are also accepted as one-run overrides by the config loader.
ENV_NAMES = {
    "user_name": "MILO_OWNER_NAME",
    "model": "MILO_MODEL",
    "models": "MILO_MODELS",
    "voice": "MILO_VOICE",
    "voices": "MILO_VOICES",
    "port": "MILO_PORT",
    "ollama_url": "MILO_OLLAMA_URL",
    "whisper_url": "MILO_WHISPER_URL",
    "models_dir": "MILO_VOICE_DIR",
    "documents_dir": "MILO_DOCUMENTS_DIR",
    "settings_dir": "MILO_SETTINGS_DIR",
    "memory_path": "MILO_MEMORY",
    "reminders_path": "MILO_REMINDERS",
    "turn_log": "MILO_TURN_LOG",
    "freshness_db": "MILO_FRESHNESS_DB",
    "kiwix_url": "MILO_KIWIX_URL",
    "workspace_root": "MILO_WORKSPACE_ROOT",
    "personal_book": "MILO_PERSONAL_BOOK_CONFIG",
    "silence_ms": "MILO_SILENCE_MS",
    "tts_threads": "MILO_TTS_THREADS",
}

_INT_KEYS = {"port", "silence_ms", "tts_threads"}
_JSON_KEYS = {"models", "voices"}
_PATH_KEYS = {
    "models_dir", "documents_dir", "settings_dir", "memory_path",
    "reminders_path", "turn_log", "freshness_db", "workspace_root", "personal_book",
}


def _coerce(key: str, value):
    if key in _INT_KEYS:
        return int(value)
    if key in _JSON_KEYS and isinstance(value, str):
        return json.loads(value)
    return value


def _validate(settings: dict) -> None:
    models = settings["models"]
    if not isinstance(models, dict) or not models or not all(
            isinstance(tag, str) and isinstance(label, str) for tag, label in models.items()):
        raise ValueError("models must be a non-empty JSON object mapping tags to labels")
    voices = settings["voices"]
    if not isinstance(voices, list) or not voices or not all(isinstance(voice, str) for voice in voices):
        raise ValueError("voices must be a non-empty JSON list")
    unknown_voices = set(voices) - set(DEFAULT_VOICES)
    if unknown_voices:
        raise ValueError(f"unknown Pocket TTS voices {sorted(unknown_voices)}")
    if settings["model"] not in models:
        raise ValueError(f"model {settings['model']!r} is not in the models menu {sorted(models)}")
    if settings["voice"] not in voices:
        raise ValueError(f"voice {settings['voice']!r} is not in voices {voices}")


def _resolve_paths(settings: dict) -> None:
    for key in _PATH_KEYS:
        value = settings[key]
        if value == "":
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        settings[key] = str(path)


def load(path: Path | None = None, env: dict | None = None) -> dict:
    """Merge defaults, the JSON file, and environment overrides, then validate."""
    settings = dict(DEFAULTS)
    settings["models"] = dict(DEFAULT_MODELS)
    settings["voices"] = list(DEFAULT_VOICES)
    path = CONFIG_PATH if path is None else path
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} must hold a JSON object.")
        loaded = {
            key: value for key, value in loaded.items()
            if not key.startswith("_comment_") and value is not None
        }
        unknown = set(loaded) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"{path}: unknown keys {sorted(unknown)}")
        settings.update(loaded)
    env = os.environ if env is None else env
    for key, env_name in ENV_NAMES.items():
        raw = env.get(env_name)
        if raw is None:
            raw = env.get("MILO_" + key.upper())
        if raw is None:
            continue
        try:
            settings[key] = _coerce(key, raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            if key == "models":
                settings[key] = dict(DEFAULT_MODELS)
            elif key == "voices":
                settings[key] = list(DEFAULT_VOICES)
            else:
                raise ValueError(f"{env_name} has an invalid value") from None
    _validate(settings)
    _resolve_paths(settings)
    return settings
