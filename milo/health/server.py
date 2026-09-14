"""Loopback HTTP server for the Milo health page."""

from __future__ import annotations

import argparse
import html
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .collect import collect


TEMPLATE_PATH = Path(__file__).with_name("page.html")


def _escape(value):
    return html.escape(str(value), quote=False)


def _format_bytes(value):
    if value is None:
        return "-"
    value = float(value)
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while abs(value) >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{value:.1f} {units[index]}" if index else f"{int(value)} B"


def _format_uptime(seconds):
    if seconds is None:
        return "-"
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def _state_class(item):
    if not item.get("ok"):
        return "down"
    if item.get("active_state") == "active" and item.get("sub_state") == "running":
        return "up"
    if item.get("active_state") == "active":
        return "warn"
    return "down"


def _error(probe):
    if probe.get("ok"):
        return ""
    return f'<p class="error"><span class="state-dot down"></span>{_escape(probe.get("error", "probe failed"))}</p>'


def _units(data):
    probe = data.get("units") or {}
    body = _error(probe)
    if probe.get("ok"):
        rows = []
        for name, item in probe.get("items", {}).items():
            state = f'{item.get("active_state", "unknown")}/{item.get("sub_state", "unknown")}'
            rows.append(
                "<tr>"
                f'<td><span class="state-dot {_state_class(item)}"></span>{_escape(name)}</td>'
                f'<td>{_escape(state)}</td>'
                f'<td class="num">{_escape(_format_uptime(item.get("uptime_seconds")))}</td>'
                f'<td class="num">{_escape(item.get("restarts", 0))}</td>'
                "</tr>"
            )
        body += '<table><thead><tr><th>Unit</th><th>State</th><th>Uptime</th><th>Restarts</th></tr></thead><tbody>'
        body += "".join(rows) + "</tbody></table>"
    return body


def _models(data):
    probe = data.get("models") or {}
    body = _error(probe)
    if probe.get("ok"):
        body += f'<p>Resident: <span class="num">{_escape(len(probe.get("resident", [])))}</span>, pulled: <span class="num">{_escape(probe.get("pulled_count", 0))}</span></p>'
        rows = []
        for model in probe.get("resident", []):
            rows.append(
                "<tr>"
                f'<td>{_escape(model.get("name", ""))}</td>'
                f'<td class="num">{_escape(_format_bytes(model.get("size")))}</td>'
                f'<td class="num">{_escape(_format_bytes(model.get("vram_size")))}</td>'
                f'<td>{_escape(model.get("expires_at") or "-")}</td>'
                "</tr>"
            )
        body += '<table><thead><tr><th>Model</th><th>Size</th><th>VRAM</th><th>Expires</th></tr></thead><tbody>'
        body += "".join(rows) + "</tbody></table>"
    return body


def _gpu(data):
    probe = data.get("gpu") or {}
    body = _error(probe)
    if probe.get("ok"):
        rows = []
        for index, item in enumerate(probe.get("items", [])):
            rows.append(
                "<tr>"
                f'<td>GPU {index}</td><td class="num">{_escape(_format_bytes(item.get("memory_used")))} / {_escape(_format_bytes(item.get("memory_total")))}</td>'
                f'<td class="num">{_escape(item.get("utilization_gpu"))}%</td></tr>'
            )
        body += '<table><thead><tr><th>Device</th><th>Memory used / total</th><th>Utilization</th></tr></thead><tbody>'
        body += "".join(rows) + "</tbody></table>"
    return body


def _library(data):
    probe = data.get("library") or {}
    body = _error(probe)
    if probe.get("ok"):
        titles = "".join(f"<li>{_escape(title)}</li>" for title in probe.get("titles", []))
        body += f'<p>Books: <span class="num">{_escape(probe.get("count", 0))}</span></p><ol>{titles}</ol>'
    return body


def _freshness(data):
    probe = data.get("freshness") or {}
    body = _error(probe)
    if probe.get("ok"):
        value = probe.get("status") or f'{_format_uptime(probe.get("age_seconds"))} old'
        body += f'<p>Freshness database: <span class="num">{_escape(value)}</span></p>'
    return body


def _ledger(data):
    probe = data.get("ledger") or {}
    body = _error(probe)
    if probe.get("ok"):
        body += f'<p>Turns today: <span class="num">{_escape(probe.get("today_count", 0))}</span></p>'
        counts = probe.get("flag_counts", {})
        if counts:
            body += "<ul>" + "".join(f'<li>{_escape(flag)}: <span class="num">{_escape(count)}</span></li>' for flag, count in counts.items()) + "</ul>"
        else:
            body += "<p>No flags today.</p>"
        recent = probe.get("recent_flags", [])
        if recent:
            body += '<table><thead><tr><th>Time</th><th>App</th><th>Flags</th></tr></thead><tbody>'
            body += "".join(
                f'<tr><td>{_escape(row.get("time", ""))}</td><td>{_escape(row.get("app", ""))}</td><td>{_escape("; ".join(row.get("flags", [])))}</td></tr>'
                for row in recent
            )
            body += "</tbody></table>"
    return body


def render_page(data):
    """Render the data-only health view without exposing ledger utterances."""
    sections = (
        ("Units", _units(data)),
        ("Models", _models(data)),
        ("GPU", _gpu(data)),
        ("Library", _library(data)),
        ("Freshness", _freshness(data)),
        ("Today's ledger", _ledger(data)),
    )
    content = "".join(f'<section><h2>{_escape(title)}</h2>{body}</section>' for title, body in sections)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("{{GENERATED_AT}}", _escape(data.get("generated_at", ""))).replace("{{CONTENT}}", content)


class _HealthCache:
    def __init__(self, collector):
        self.collector = collector
        self.lock = threading.Lock()
        self.data = None
        self.created = 0.0

    def get(self):
        with self.lock:
            if self.data is None or time.monotonic() - self.created >= 5:
                self.data = self.collector()
                self.created = time.monotonic()
            return self.data


def make_server(port, collector=None):
    """Create a loopback health server, with an injectable collector for tests."""
    cache = _HealthCache(collector or collect)

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/health":
                self._send_json(cache.get())
            elif path == "/":
                self._send_html(render_page(cache.get()))
            else:
                self.send_error(404, "not found")

        def _send_json(self, value):
            body = json.dumps(value, sort_keys=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, value):
            body = value.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format_string, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), HealthHandler)
    server.daemon_threads = True
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the Milo health page on loopback.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MILO_HEALTH_PORT", "8778")))
    args = parser.parse_args(argv)
    server = make_server(args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
