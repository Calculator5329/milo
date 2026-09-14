#!/usr/bin/env python3
"""Launch and maintain Milo's v3 demo engine.

    python milo.py                       start the demo server
    python milo.py serve --port 8767     start it on another port
    python milo.py doctor                inspect required and optional dependencies
    python milo.py setup-voices          cache Pocket TTS and configured voices
    python milo.py test                  run the engine unit tests
    python milo.py probe [...]           probe a running test server
    python milo.py bank [...]            run the evaluation question bank
    python milo.py freshness [command]   ingest by default, or run search/status
    python milo.py health                collect the health snapshot

When ``.venv`` exists, the launcher re-executes itself with that interpreter.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from milo import config

ROOT = Path(__file__).resolve().parent
ENGINE_ROOT = ROOT / "milo"
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def in_project_venv() -> bool:
    return Path(sys.executable).resolve() == VENV_PYTHON.resolve()


def config_to_env(settings: dict, base: dict | None = None) -> dict:
    """Translate loaded public settings into the engine's environment contract."""
    env = dict(os.environ if base is None else base)
    for key, env_name in config.ENV_NAMES.items():
        value = settings[key]
        env[env_name] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    paths = [str(ENGINE_ROOT), str(ROOT)]
    paths.extend(part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part)
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
    return env


def _has_option(args: list[str], option: str) -> bool:
    return any(arg == option or arg.startswith(option + "=") for arg in args)


def _command(argv: list[str]) -> tuple[str, list[str]]:
    if not argv or argv[0].startswith("-"):
        return "serve", argv
    return argv[0], argv[1:]


def _run(args: list[str], cwd: Path, env: dict) -> int:
    return subprocess.call([str(arg) for arg in args], cwd=cwd, env=env)


def main(argv: list[str]) -> int:
    if VENV_PYTHON.is_file() and not in_project_venv():
        return subprocess.call([str(VENV_PYTHON), str(ROOT / "milo.py"), *argv], cwd=ROOT)

    command, rest = _command(argv)
    known = {"serve", "setup-voices", "doctor", "probe", "test", "bank", "freshness", "health"}
    if command not in known:
        print(__doc__, file=sys.stderr)
        return 2

    try:
        settings = config.load()
        env = config_to_env(settings)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        if command != "doctor":
            print(f"Milo config error: {error}", file=sys.stderr)
            return 2
        settings = dict(config.DEFAULTS)
        env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(ENGINE_ROOT), str(ROOT)))}

    if command == "serve":
        args = list(rest)
        configured = {
            "--port": settings["port"],
            "--voice-dir": settings["models_dir"],
            "--documents": settings["documents_dir"],
            "--settings": settings["settings_dir"],
        }
        for option, value in configured.items():
            if not _has_option(args, option):
                args.extend((option, str(value)))
        return _run([sys.executable, "demo_server.py", *args], ENGINE_ROOT, env)

    if command == "test":
        # The suite exercises the engine's compiled-in defaults; only the import path is exported.
        clean = {name: value for name, value in os.environ.items() if name not in config.ENV_NAMES.values()}
        clean["PYTHONPATH"] = env["PYTHONPATH"]
        return _run([sys.executable, "-m", "unittest", "discover", "-s", "tests", *rest], ENGINE_ROOT, clean)
    if command == "probe":
        return _run([sys.executable, "tests/latency_probe.py", *rest], ENGINE_ROOT, env)
    if command == "bank":
        return _run([sys.executable, "-m", "evaluations.bank_run", *rest], ENGINE_ROOT, env)
    if command == "freshness":
        return _run([sys.executable, "-m", "freshness", *(rest or ["ingest"])], ENGINE_ROOT, env)
    if command == "health":
        return _run([sys.executable, "-m", "health", *rest], ENGINE_ROOT, env)

    script = "scripts/setup_voices.py" if command == "setup-voices" else "scripts/doctor.py"
    return _run([sys.executable, str(ROOT / script), *rest], ROOT, env)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
