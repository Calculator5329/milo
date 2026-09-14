"""Offline regression tests for the public launcher and optional engine adapters."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ENGINE_ROOT = ROOT / "milo"
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ROOT))

from milo import config  # noqa: E402
import local_search  # noqa: E402
import model_catalog  # noqa: E402
import reminders  # noqa: E402
import router  # noqa: E402
from library import workspace_book  # noqa: E402


def load_launcher():
    spec = importlib.util.spec_from_file_location("milo_public_launcher", ROOT / "milo.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ConfigWiringTests(unittest.TestCase):
    def test_every_config_key_reaches_its_engine_environment_name(self):
        expected = {
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
        self.assertEqual(config.ENV_NAMES, expected)
        settings = config.load(path=ROOT / "does-not-exist.json", env={})
        env = load_launcher().config_to_env(settings, base={})
        for key, env_name in expected.items():
            value = settings[key]
            wanted = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            self.assertEqual(env[env_name], wanted, key)
        self.assertIn(str(ENGINE_ROOT), env["PYTHONPATH"].split(os.pathsep))

    def test_empty_kiwix_disables_client_and_title_probe_without_network(self):
        with mock.patch.dict(os.environ, {"MILO_KIWIX_URL": ""}), \
             mock.patch.object(local_search, "KiwixHttpAdapter") as adapter, \
             mock.patch.object(router.urllib.request, "urlopen") as open_probe:
            client = local_search.LocalLibraryClient()
            self.assertFalse(client.available())
            with self.assertRaisesRegex(local_search.LibraryUnavailable, "disabled"):
                client.search("anything", threading.Event())
            self.assertIsNone(router.Router().probe)
            adapter.assert_not_called()
            open_probe.assert_not_called()

    def test_empty_workspace_root_disables_book_without_reading_database(self):
        with mock.patch.dict(os.environ, {"MILO_WORKSPACE_ROOT": ""}), \
             mock.patch.object(workspace_book, "_search") as search:
            book = workspace_book.WorkspaceBook()
            self.assertFalse(book.enabled)
            self.assertEqual(book.search("status"), {
                "matches": [], "elapsed_ms": 0, "disabled": True,
            })
            search.assert_not_called()

    def test_models_json_parses_and_bad_value_falls_back_to_defaults(self):
        configured = {"fixture:1b": "Fixture"}
        self.assertEqual(
            model_catalog.models_from_env({"MILO_MODELS": json.dumps(configured)}),
            configured,
        )
        self.assertEqual(
            model_catalog.models_from_env({"MILO_MODELS": "not json"}),
            model_catalog.DEFAULT_MODELS,
        )
        self.assertEqual(
            model_catalog.models_from_env({"MILO_MODELS": "[]"}),
            model_catalog.DEFAULT_MODELS,
        )

    def test_windows_reminders_return_spoken_platform_guard(self):
        with mock.patch.object(reminders.os, "name", "nt"), \
             mock.patch.object(reminders, "create") as create:
            result = reminders.handle("remind me in ten minutes to stretch")
        self.assertEqual(result["spoken"], "Reminders need Linux for now.")
        self.assertEqual(result["action"], "create")
        self.assertIn("error", result)
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
