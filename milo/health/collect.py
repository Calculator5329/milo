"""Dependency-free probes for the Milo health page."""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

try:
    from turn_ledger import DEFAULT_PATH, flags
except ImportError:  # pragma: no cover, useful when imported as audio.milo.health
    from ..turn_ledger import DEFAULT_PATH, flags


UNITS = (
    "milo-experiment",
    "milo-demos",
    "milo-corner-demo",
    "kiwix-library",
    "whisper-server",
    "ollama",
)
DEFAULT_FRESHNESS_PATH = Path.home() / ".local" / "state" / "milo" / "freshness.db"
OLLAMA = "http://127.0.0.1:11434"
KIWIX = "http://127.0.0.1:8891"
WHISPER = "http://127.0.0.1:8178/"


def _default_runner(command):
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout


def _default_fetch(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.getcode(), response.read()


def _short_error(error):
    message = re.sub(r"\s+", " ", str(error)).strip() or error.__class__.__name__
    return message[:160]


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    if hasattr(value, "stdout"):
        return _text(value.stdout)
    return str(value)


def _payload(fetch, url, timeout=5):
    response = fetch(url, timeout=timeout)
    if isinstance(response, tuple) and len(response) == 2:
        status, body = response
        if isinstance(body, (dict, list)):
            return body, status or 200
        return _text(body), status or 200
    if isinstance(response, (dict, list)):
        return response, 200
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()
    if hasattr(response, "read"):
        body = response.read()
    else:
        body = response
    if isinstance(body, (dict, list)):
        return body, status or 200
    return _text(body), status or 200


def _json_payload(fetch, url):
    payload, status = _payload(fetch, url)
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP status {status}")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("JSON response was not an object")
    return payload


def _systemd_values(raw):
    values = {}
    for line in _text(raw).splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def _parse_start(value, now):
    if not value or value in ("n/a", "-", "0"):
        return None
    value = value.strip()
    parts = value.rsplit(" ", 1)
    stamp = parts[0] if len(parts) == 2 else value
    parsed = None
    for format_string in ("%a %Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = dt.datetime.strptime(stamp, format_string)
            break
        except ValueError:
            continue
    if parsed is None:
        return None
    if now.tzinfo is not None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    return parsed


def _unit_probe(runner, now):
    items = {}
    for unit in UNITS:
        command = [
            "systemctl",
            "--user",
            "show",
            unit,
            "-p",
            "ActiveState,SubState,ExecMainStartTimestamp,NRestarts",
        ]
        try:
            values = _systemd_values(runner(command))
            started = _parse_start(values.get("ExecMainStartTimestamp"), now)
            uptime = None if started is None else max(0, int((now - started).total_seconds()))
            items[unit] = {
                "ok": True,
                "active_state": values.get("ActiveState", "unknown"),
                "sub_state": values.get("SubState", "unknown"),
                "restarts": int(values.get("NRestarts", 0) or 0),
                "started_at": values.get("ExecMainStartTimestamp", ""),
                "uptime_seconds": uptime,
            }
        except Exception as error:
            items[unit] = {"ok": False, "error": _short_error(error)}
    result = {"ok": all(item["ok"] for item in items.values()), "items": items}
    if not result["ok"]:
        failed = next(name for name, item in items.items() if not item["ok"])
        result["error"] = f"{failed}: {items[failed]['error']}"
    return result


def _models_probe(fetch):
    resident = _json_payload(fetch, OLLAMA + "/api/ps")
    pulled = _json_payload(fetch, OLLAMA + "/api/tags")
    models = []
    for model in resident.get("models", []):
        models.append(
            {
                "name": model.get("name", ""),
                "size": model.get("size"),
                "vram_size": model.get("size_vram"),
                "expires_at": model.get("expires_at"),
            }
        )
    return {"ok": True, "resident": models, "pulled_count": len(pulled.get("models", []))}


def _library_probe(fetch):
    payload, status = _payload(fetch, KIWIX + "/catalog/v2/entries?count=100")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP status {status}")
    root = ET.fromstring(_text(payload))
    titles = []
    count = 0
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "entry":
            continue
        count += 1
        title = next(
            (child.text.strip() for child in element.iter() if child.tag.rsplit("}", 1)[-1] == "title" and child.text),
            "(untitled)",
        )
        if len(titles) < 30:
            titles.append(title)
    return {"ok": True, "count": count, "titles": titles}


def _whisper_probe(fetch):
    _, status = _payload(fetch, WHISPER, timeout=1)
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP status {status}")
    return {"ok": True, "status": status}


def _freshness_probe(path, now):
    path = Path(path)
    if not path.exists():
        return {"ok": True, "status": "not built"}
    reference = now.timestamp()
    return {"ok": True, "age_seconds": max(0, int(reference - path.stat().st_mtime))}


def _row_date(row):
    value = row.get("at")
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _ledger_probe(path, now):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    today = [row for row in rows if _row_date(row) == now.date()]
    counts = Counter()
    flagged = []
    for row in today:
        marks = flags(row)
        counts.update(marks)
        if marks:
            flagged.append({"time": row.get("at", ""), "app": row.get("app", "?"), "flags": marks})
    return {
        "ok": True,
        "today_count": len(today),
        "flag_counts": dict(sorted(counts.items())),
        "recent_flags": flagged[-5:],
    }


def _soft(probe):
    try:
        return probe()
    except Exception as error:
        return {"ok": False, "error": _short_error(error)}


def collect(now=None, runner=None, fetch=None, ledger_path=None):
    """Collect all Milo health data, keeping individual probes failure-soft."""
    now = now or dt.datetime.now().astimezone()
    runner = runner or _default_runner
    fetch = fetch or _default_fetch
    ledger_path = DEFAULT_PATH if ledger_path is None else ledger_path
    freshness = _soft(lambda: _freshness_probe(DEFAULT_FRESHNESS_PATH, now))
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "units": _unit_probe(runner, now),
        "models": _soft(lambda: _models_probe(fetch)),
        "gpu": _soft(lambda: _gpu_probe(runner)),
        "library": _soft(lambda: _library_probe(fetch)),
        "whisper": _soft(lambda: _whisper_probe(fetch)),
        "freshness": freshness,
        "ledger": _soft(lambda: _ledger_probe(ledger_path, now)),
    }


def _gpu_probe(runner):
    command = [
        "nvidia-smi",
        "--query-gpu=memory.used,memory.total,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    items = []
    for line in _text(runner(command)).splitlines():
        if not line.strip():
            continue
        values = [part.strip() for part in line.split(",")]
        if len(values) != 3:
            raise ValueError("unexpected nvidia-smi columns")
        items.append({"memory_used": int(values[0]), "memory_total": int(values[1]), "utilization_gpu": int(values[2])})
    if not items:
        raise ValueError("nvidia-smi returned no GPUs")
    return {"ok": True, "items": items}
