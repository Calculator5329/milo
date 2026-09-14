"""Milo doctor: what is installed, what is reachable, what this machine can run.

    python milo.py doctor            human-readable report
    python milo.py doctor --json     the same facts as JSON (agents read this)

Every line is a measurement or a clearly labelled suggestion. The exit code is 0 when Milo can
start, 1 when something required is missing. Nothing here downloads, installs, or writes files.
"""
from __future__ import annotations

import argparse
import datetime
import importlib
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from milo import config  # noqa: E402
from milo.voice_catalog import VOICE_PRESETS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Approximate resident size of each menu model as Ollama loads it (GB). These decide the tier
# suggestion below; docs/models.md explains the reasoning and how they were measured.
MODEL_GB = {'gemma4:12b': 8.1, 'gpt-oss:20b': 13.0, 'phi4:latest': 9.1, 'gemma4:4b': 3.3, 'qwen2.5:3b': 2.0}
# Whisper takes GPU memory beside the language model: large-v3-turbo about 1.6 GB, base.en about 0.2 GB.
WHISPER_GB = 1.6
# Suggested default by free VRAM after Whisper. Ordered best first; the first that fits wins.
TIERS = [('gemma4:12b', 10.0), ('gemma4:4b', 4.5), ('qwen2.5:3b', 3.0)]


def run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def http(url, timeout=4):
    """GET a URL; return (status, body) or (None, error string). Never raises."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        return exc.code, ''
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, str(exc)


def gpu_info():
    """Total VRAM in GB from nvidia-smi when present; AMD and Intel report as unknown."""
    if not shutil.which('nvidia-smi'):
        return {'vendor': 'none-or-unknown', 'name': None, 'vram_gb': None,
                'note': 'nvidia-smi not found. Ollama may still use an AMD or Intel GPU; VRAM is unknown here.'}
    out = run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'])
    first = out.splitlines()[0] if out else ''
    if ',' not in first:
        return {'vendor': 'nvidia', 'name': None, 'vram_gb': None, 'note': 'nvidia-smi answered but gave no GPU line.'}
    name, mib = [x.strip() for x in first.split(',', 1)]
    return {'vendor': 'nvidia', 'name': name, 'vram_gb': round(int(mib) / 1024, 1), 'note': None}


def ram_gb():
    if os.name == 'nt':
        out = run(['powershell', '-NoProfile', '-Command', '(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory'], timeout=20)
        return round(int(out) / 1024 ** 3, 1) if out.isdigit() else None
    try:
        text = Path('/proc/meminfo').read_text()
        kib = int(re.search(r'MemTotal:\s+(\d+)', text).group(1))
        return round(kib / 1024 ** 2, 1)
    except (OSError, AttributeError, ValueError):
        return None


def suggest_model(vram_gb, ram_gb_value):
    if vram_gb:
        budget = vram_gb - WHISPER_GB
        for tag, need in TIERS:
            if budget >= need:
                return tag, f'{vram_gb} GB VRAM minus ~{WHISPER_GB} GB for Whisper leaves {budget:.1f} GB; {tag} needs ~{need} GB.'
        return 'qwen2.5:3b', f'{vram_gb} GB VRAM is tight; the smallest model, partly on CPU.'
    if ram_gb_value and ram_gb_value >= 16:
        return 'gemma4:4b', 'No NVIDIA VRAM reading; with 16 GB+ RAM a 4B model runs on CPU with slower replies.'
    return 'qwen2.5:3b', 'No GPU reading and limited RAM; the smallest model is the safe start.'


def check_python():
    v = sys.version_info
    ok = (3, 11) <= (v.major, v.minor) <= (3, 13)
    return {'ok': ok, 'version': platform.python_version(), 'executable': sys.executable,
            'note': None if ok else 'Milo needs Python 3.11 to 3.13 (torch and pocket-tts wheels). Create .venv with one of those.'}


def check_imports():
    found = {}
    for name in ('torch', 'pocket_tts', 'ddgs', 'scipy'):
        try:
            module = importlib.import_module(name)
            found[name] = getattr(module, '__version__', 'present')
        except Exception as exc:  # ImportError or a broken native wheel; both mean "not usable"
            found[name] = None if isinstance(exc, ImportError) else f'broken: {type(exc).__name__}'
    return found


def check_voices(settings):
    root = Path(settings['models_dir'])
    configured = [voice for voice in settings['voices'] if voice in VOICE_PRESETS]
    missing = [voice for voice in configured if not (root / (voice + '.safetensors')).is_file()]
    return {'models_dir': str(root), 'voices': configured, 'missing': missing,
            'fixture': (root / 'milo-hello.wav').is_file()}


def check_kiwix(settings):
    base_url = settings['kiwix_url'].rstrip('/')
    if not base_url:
        return {'enabled': False, 'reachable': False, 'books': None, 'error': None}
    status, body = http(base_url + '/catalog/v2/entries?count=100', timeout=3)
    if status != 200:
        root_status, root_body = http(base_url + '/', timeout=2)
        return {'enabled': True, 'reachable': root_status is not None, 'books': None,
                'error': None if root_status is not None else (root_body or body or f'HTTP {status}')}
    books = len(set(re.findall(r'href=["\']/content/([^/"\']+)', body)))
    if books == 0:
        books = len(re.findall(r'<entry\b', body))
    return {'enabled': True, 'reachable': True, 'books': books, 'error': None}


def _timestamp_age_hours(value):
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    return round(max(0.0, (now - parsed.astimezone(datetime.timezone.utc)).total_seconds()) / 3600, 1)


def check_freshness(settings):
    path = Path(settings['freshness_db']).expanduser()
    if not path.is_file():
        return {'path': str(path), 'present': False, 'docs': 0, 'newest': None,
                'newest_age_hours': None, 'error': None}
    try:
        uri = path.resolve().as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True) as connection:
            docs, newest = connection.execute(
                'SELECT COUNT(*), MAX(COALESCE(published, fetched)) FROM docs'
            ).fetchone()
        return {'path': str(path), 'present': True, 'docs': docs, 'newest': newest,
                'newest_age_hours': _timestamp_age_hours(newest), 'error': None}
    except (OSError, sqlite3.Error) as error:
        return {'path': str(path), 'present': True, 'docs': None, 'newest': None,
                'newest_age_hours': None, 'error': str(error)}


def check_overlay():
    """The corner overlay (milo/demo/desktop.py) needs GTK 3, gtk-layer-shell, WebKit2GTK 4.1 and pycairo."""
    if platform.system() != 'Linux':
        return {'platform': False, 'gi': False, 'gtk3': False, 'layer_shell': False, 'webkit': False,
                'cairo': False, 'note': 'The corner overlay is Linux-only.'}
    result = {'platform': True, 'gi': False, 'gtk3': False, 'layer_shell': False, 'webkit': False,
              'cairo': False, 'note': None}
    try:
        gi = importlib.import_module('gi')
        result['gi'] = True
        gi.require_version('Gtk', '3.0')
        importlib.import_module('gi.repository.Gtk')
        result['gtk3'] = True
        gi.require_version('GtkLayerShell', '0.1')
        importlib.import_module('gi.repository.GtkLayerShell')
        result['layer_shell'] = True
        gi.require_version('WebKit2', '4.1')
        importlib.import_module('gi.repository.WebKit2')
        result['webkit'] = True
    except Exception as error:
        result['note'] = type(error).__name__
    try:
        importlib.import_module('cairo')
        result['cairo'] = True
    except Exception as error:
        result['note'] = result['note'] or type(error).__name__
    return result


def check_reminder_support():
    if platform.system() != 'Linux':
        return {'available': False, 'path': None, 'note': 'Reminders need Linux for now.'}
    executable = shutil.which('systemd-run')
    return {'available': bool(executable), 'path': executable,
            'note': None if executable else 'systemd-run --user is unavailable, so reminders are disabled.'}


def check_ollama(settings):
    status, body = http(settings['ollama_url'].rstrip('/') + '/api/tags')
    if status != 200:
        return {'reachable': False, 'error': body or f'HTTP {status}', 'pulled': [], 'missing': list(settings['models'])}
    try:
        tags = {m['name'] for m in json.loads(body).get('models', [])}
    except (ValueError, KeyError, TypeError):
        tags = set()
    # `phi4:latest` and `phi4` are the same tag to Ollama.
    def have(tag):
        return tag in tags or tag.removesuffix(':latest') in {t.removesuffix(':latest') for t in tags}
    pulled = [m for m in settings['models'] if have(m)]
    return {'reachable': True, 'error': None, 'pulled': pulled,
            'missing': [m for m in settings['models'] if m not in pulled], 'default_pulled': have(settings['model'])}


def check_whisper(settings):
    status, body = http(settings['whisper_url'].rstrip('/') + '/')
    return {'reachable': status is not None, 'status': status, 'error': None if status else body}


def check_running(settings):
    status, body = http(f"http://127.0.0.1:{settings['port']}/api/health", timeout=2)
    if status != 200:
        return {'running': False}
    try:
        return {'running': True, **json.loads(body)}
    except ValueError:
        return {'running': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--json', action='store_true', help='print the report as JSON')
    args = parser.parse_args()
    try:
        settings = config.load()
        config_error = None
    except (ValueError, OSError) as exc:
        settings, config_error = dict(config.DEFAULTS), str(exc)
    gpu, ram = gpu_info(), ram_gb()
    suggestion, why = suggest_model(gpu['vram_gb'], ram)
    report = {
        'platform': {'os': platform.system(), 'release': platform.release(), 'machine': platform.machine()},
        'python': check_python(),
        'venv': {'present': (ROOT / '.venv').is_dir(), 'active': Path(sys.prefix).resolve() == (ROOT / '.venv').resolve()},
        'imports': check_imports(),
        'config': {'path': str(config.CONFIG_PATH), 'present': config.CONFIG_PATH.is_file(), 'error': config_error,
                   'model': settings['model'], 'voice': settings['voice'], 'user_name': settings['user_name'], 'port': settings['port']},
        'voices': check_voices(settings),
        'ollama': check_ollama(settings),
        'whisper': check_whisper(settings),
        'kiwix': check_kiwix(settings),
        'freshness': check_freshness(settings),
        'overlay': check_overlay(),
        'reminders': check_reminder_support(),
        'hardware': {'gpu': gpu, 'ram_gb': ram},
        'suggested_model': {'tag': suggestion, 'why': why, 'matches_config': suggestion == settings['model']},
        'server': check_running(settings),
    }
    required = [report['python']['ok'], all(report['imports'][k] not in (None,) and not str(report['imports'][k]).startswith('broken')
                                              for k in ('torch', 'pocket_tts', 'ddgs')),
                not report['voices']['missing'], report['ollama']['reachable'], report['ollama'].get('default_pulled', False),
                report['whisper']['reachable'], config_error is None]
    report['ready'] = all(required)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report['ready'] else 1

    def line(ok, text):
        print(('  ok   ' if ok else '  MISSING ') + text)
    def optional(text):
        print('  opt  ' + text)
    p = report['python']
    line(p['ok'], f"Python {p['version']} at {p['executable']}" + (f"  ({p['note']})" if p['note'] else ''))
    line(report['venv']['present'], f".venv {'present' if report['venv']['present'] else 'not created'} at {ROOT / '.venv'}")
    for name, version in report['imports'].items():
        line(bool(version) and not str(version).startswith('broken'), f'{name} {version or "not installed"}')
    c = report['config']
    line(config_error is None, f"config {c['path']} {'(file present)' if c['present'] else '(defaults; no file yet)'}"
         + (f'  ERROR {config_error}' if config_error else f"  model={c['model']} voice={c['voice']} name={c['user_name'] or '(none)'} port={c['port']}"))
    optional(f"milo.config.json {'present' if c['present'] else 'not present, built-in defaults are active'}")
    v = report['voices']
    line(not v['missing'], f"voices in {v['models_dir']}: " + ('all cached' if not v['missing'] else f"missing {v['missing']} (run: python milo.py setup-voices)"))
    o = report['ollama']
    line(o['reachable'], f"Ollama at {settings['ollama_url']}: " + ('reachable' if o['reachable'] else f"unreachable ({o['error']})"))
    if o['reachable']:
        line(o['default_pulled'], f"default model {settings['model']} {'pulled' if o['default_pulled'] else 'NOT pulled (run: ollama pull ' + settings['model'] + ')'}")
        print(f"         menu pulled: {o['pulled'] or 'none'}; not pulled: {o['missing'] or 'none'}")
    w = report['whisper']
    line(w['reachable'], f"whisper.cpp server at {settings['whisper_url']}: " + ('reachable' if w['reachable'] else f"unreachable ({w['error']})"))
    k = report['kiwix']
    if not k['enabled']:
        optional('offline Kiwix library disabled, set kiwix_url to enable it')
    elif k['reachable']:
        count = f", {k['books']} books in catalog" if k['books'] is not None else ''
        optional(f"Kiwix at {settings['kiwix_url']}: reachable{count}")
    else:
        optional(f"Kiwix at {settings['kiwix_url']}: unavailable ({k['error']})")
    fresh = report['freshness']
    if not fresh['present']:
        optional(f"freshness database not present at {fresh['path']}")
    elif fresh['error']:
        optional(f"freshness database at {fresh['path']} could not be read ({fresh['error']})")
    else:
        age = (f", newest row {fresh['newest_age_hours']} hours old"
               if fresh['newest_age_hours'] is not None else ', no dated rows')
        optional(f"freshness database at {fresh['path']}: {fresh['docs']} rows{age}")
    overlay = report['overlay']
    if overlay['platform']:
        optional('Linux corner overlay dependencies (python-gobject gtk3 gtk-layer-shell webkit2gtk-4.1 python-cairo): '
                 f"gi={'yes' if overlay['gi'] else 'no'}, Gtk 3={'yes' if overlay['gtk3'] else 'no'}, "
                 f"GtkLayerShell={'yes' if overlay['layer_shell'] else 'no'}, WebKit2={'yes' if overlay['webkit'] else 'no'}, "
                 f"cairo={'yes' if overlay['cairo'] else 'no'}")
    else:
        optional(overlay['note'])
    reminder_support = report['reminders']
    optional(('systemd-run --user available at ' + reminder_support['path'])
             if reminder_support['available'] else reminder_support['note'])
    g = report['hardware']['gpu']
    print(f"  info GPU: {g['name'] or g['vendor']}" + (f", {g['vram_gb']} GB VRAM" if g['vram_gb'] else '') + (f"  ({g['note']})" if g['note'] else ''))
    print(f"  info RAM: {ram} GB" if ram else '  info RAM: unknown')
    s = report['suggested_model']
    print(f"  suggest default model {s['tag']}" + ('' if s['matches_config'] else f" (config has {settings['model']})") + f"  because {s['why']}")
    srv = report['server']
    print(f"  info server on port {settings['port']}: " + (('running, ready' if srv.get('ready') else 'running, still warming up') if srv['running'] else 'not running'))
    print('READY: Milo can start.' if report['ready'] else 'NOT READY: fix the MISSING lines above, then run doctor again.')
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    sys.exit(main())
