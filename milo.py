#!/usr/bin/env python3
"""Milo launcher. Works the same on Windows and Linux; always run it from the repo root.

    python milo.py                 start the server (default port from milo.config.json)
    python milo.py --port 8767     start on another port
    python milo.py setup-voices    one-time: cache the speech model and voices
    python milo.py doctor          check services, models, hardware; suggest a model tier
    python milo.py probe [...]     latency probe against a running server (tests/latency_probe.py)
    python milo.py test            run the unit tests

It re-executes itself with the project virtual environment's interpreter (.venv) when that
exists, so people never have to remember to activate anything.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def in_project_venv():
    return Path(sys.executable).resolve() == VENV_PYTHON.resolve()


def main(argv):
    if VENV_PYTHON.is_file() and not in_project_venv():
        return subprocess.call([str(VENV_PYTHON), str(ROOT / 'milo.py'), *argv], cwd=ROOT)
    command = argv[0] if argv and not argv[0].startswith('-') else 'serve'
    rest = argv[1:] if command != 'serve' else argv
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    if command == 'serve':
        return subprocess.call([sys.executable, '-m', 'milo.server', *rest], cwd=ROOT, env=env)
    scripts = {'setup-voices': 'scripts/setup_voices.py', 'doctor': 'scripts/doctor.py', 'probe': 'tests/latency_probe.py'}
    if command in scripts:
        return subprocess.call([sys.executable, str(ROOT / scripts[command]), *rest], cwd=ROOT, env=env)
    if command == 'test':
        return subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', *rest], cwd=ROOT, env=env)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
