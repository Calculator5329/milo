"""Milo settings: built-in defaults, then `milo.config.json` at the repo root, then `MILO_*` env vars.

The config file is optional and never committed (see .gitignore); `milo.config.example.json`
shows every key. Environment variables use the upper-cased key with a `MILO_` prefix, so
`MILO_PORT=8767` or `MILO_MODEL=phi4:latest` override the file for one run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / 'milo.config.json'

# The menu shown in Settings. Keys are Ollama tags; values are the labels people see.
# docs/models.md explains which tier fits which GPU; the doctor script suggests one.
DEFAULT_MODELS = {
    'gemma4:12b': 'Quick · Gemma 4 12B',
    'gpt-oss:20b': 'Careful · GPT OSS 20B',
    'phi4:latest': 'Balanced · Phi-4 14B',
    'gemma4:4b': 'Light · Gemma 4 4B',
    'qwen2.5:3b': 'Tiny · Qwen 2.5 3B',
}

DEFAULTS = {
    # Spoken name in the system prompt and the voice audition line. Empty means no name.
    'user_name': '',
    # Ollama tag used when the page loads and for the warmup. Must be a key of `models`.
    'model': 'gemma4:12b',
    # Which menu entries to offer. Trim this to what is actually pulled so the page never
    # offers a model that would need a cold download mid-conversation.
    'models': DEFAULT_MODELS,
    # Pocket TTS preset voice cached by scripts/setup_voices.py.
    'voice': 'marius',
    'voices': ['marius', 'javert', 'bill_boerst', 'stuart_bell', 'alba'],
    'port': 8766,
    'ollama_url': 'http://127.0.0.1:11434',
    'whisper_url': 'http://127.0.0.1:8178',
    # Where setup_voices.py caches the Pocket TTS model and voice states. Inside the
    # repo by default so one folder holds everything and nothing lands in a home cache.
    'models_dir': str(REPO_ROOT / 'models'),
    # End-of-turn silence gate in ms. Raise toward 500 if turns end mid-sentence.
    'silence_ms': 350,
    # CPU threads for Pocket TTS. Two is enough for streaming speech; more steals from Ollama.
    'tts_threads': 2,
}

_INT_KEYS = {'port', 'silence_ms', 'tts_threads'}


def _coerce(key, value):
    if key in _INT_KEYS:
        return int(value)
    if key in ('models', 'voices') and isinstance(value, str):
        return json.loads(value)
    return value


def load(path: Path | None = None, env: dict | None = None) -> dict:
    """Merge defaults, the JSON file, and MILO_* environment overrides; validate the result."""
    settings = dict(DEFAULTS)
    path = CONFIG_PATH if path is None else path
    if path.is_file():
        loaded = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(loaded, dict):
            raise ValueError(f'{path} must hold a JSON object.')
        unknown = set(loaded) - set(DEFAULTS)
        if unknown:
            raise ValueError(f'{path}: unknown keys {sorted(unknown)}')
        settings.update(loaded)
    env = os.environ if env is None else env
    for key in DEFAULTS:
        raw = env.get('MILO_' + key.upper())
        if raw is not None:
            settings[key] = _coerce(key, raw)
    if settings['model'] not in settings['models']:
        raise ValueError(f"model {settings['model']!r} is not in the models menu {sorted(settings['models'])}")
    if settings['voice'] not in settings['voices']:
        raise ValueError(f"voice {settings['voice']!r} is not in voices {settings['voices']}")
    models_dir = Path(settings['models_dir']).expanduser()
    if not models_dir.is_absolute():
        models_dir = REPO_ROOT / models_dir  # relative paths are relative to the repo, not the shell
    settings['models_dir'] = str(models_dir)
    return settings
