#!/usr/bin/env python3
"""Opt-in Wayland demo: Python GI, GTK3, gtk-layer-shell, WebKit2 4.1.

Run with python3 desktop.py after the demo server starts on port 8776.
Optional session D-Bus hold control; no autostart or installed Jarvis changes.
"""

from collections import deque
import argparse
import cairo
import json
import os
import subprocess
import sys
import time
from urllib.parse import urlsplit

# This wrapper requires Wayland even when the invoking shell prefers X11.
os.environ["GDK_BACKEND"] = "wayland"
# WebKit/NVIDIA explicit-sync can fail with "Missing acquire timeline".
# Keep this workaround local to Milo; do not alter the desktop environment.
# https://bugs.webkit.org/show_bug.cgi?id=280210
os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"] = "1"
# Optional user-local GStreamer repair. Never change the system plugin set.
audio_plugins = os.environ.get("MILO_GST_PLUGIN_DIR", os.path.expanduser("~/.cache/tmp/milo-native-gst/plugins"))
if os.path.isdir(audio_plugins):
    os.environ["GST_PLUGIN_PATH"] = os.pathsep.join(filter(None, (audio_plugins, os.environ.get("GST_PLUGIN_PATH", ""))))

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell, WebKit2


COLLAPSED_SIZE = (380, 330)  # room for a thought bubble above the robot; input region keeps the rest click-through
# The robot himself never takes pointer input: clicks land on whatever sits behind him (a
# fullscreen button, a video control). Only an open thought bubble is clickable, for copy.
CORNER_MARGIN = 6  # px from the screen edge


def event(name):
    print(json.dumps({"event": name}), flush=True)


def demo_port(value):
    port = int(value)
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


class Desktop:
    def __init__(self, port):
        try:
            from native_audio import NativeAudio
            self.audio = NativeAudio(self.audio_event)
        except (ImportError, ValueError):
            self.audio = None
        self.port = port
        self.url = f"http://127.0.0.1:{port}/overlay"
        self.microphone_requested = None
        self.thought_rect = None
        self.size = COLLAPSED_SIZE
        self.ready = False
        self.failed = False
        self.hold_token = None
        self.released_tokens = deque(maxlen=128)
        self.bus_owner = Gio.bus_own_name(Gio.BusType.SESSION, "org.milo.Companion", Gio.BusNameOwnerFlags.NONE, self.bus_acquired, None, None)
        self.window = Gtk.Window(title="Milo demo")
        self.window.set_decorated(False)
        self.window.set_app_paintable(True)
        visual = self.window.get_screen().get_rgba_visual()
        if visual:
            self.window.set_visual(visual)
        style = Gtk.CssProvider()
        style.load_from_data(b"window { background-color: transparent; }")
        self.window.get_style_context().add_provider(
            style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        GtkLayerShell.init_for_window(self.window)
        GtkLayerShell.set_namespace(self.window, "Milo demo")
        GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.OVERLAY)
        for edge in (GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self.window, edge, True)
            GtkLayerShell.set_margin(self.window, edge, CORNER_MARGIN)
        GtkLayerShell.set_exclusive_zone(self.window, 0)
        pinned = self.primary_monitor()
        if pinned is not None:
            GtkLayerShell.set_monitor(self.window, pinned)

        manager = WebKit2.UserContentManager()
        manager.connect("script-message-received::milo", self.message)
        manager.register_script_message_handler("milo")
        policies = WebKit2.WebsitePolicies(autoplay=WebKit2.AutoplayPolicy.ALLOW)
        self.view = WebKit2.WebView(user_content_manager=manager, website_policies=policies)
        self.view.set_is_muted(False)
        self.view.set_background_color(Gdk.RGBA(0, 0, 0, 0))
        self.view.get_settings().set_enable_media_stream(True)
        self.view.get_settings().set_media_playback_requires_user_gesture(False)
        self.view.connect("permission-request", self.permission)
        self.view.connect("decide-policy", self.navigation)
        self.view.connect("create", lambda *_: None)
        self.view.connect("load-changed", self.loaded)
        self.view.connect("load-failed", self.load_failed)
        self.window.add(self.view)
        self.window.connect("destroy", lambda *_: Gtk.main_quit())
        self.window.connect("focus-out-event", self.focus_out)
        self.resize()
        self.view.load_uri(self.url)

    def bus_acquired(self, connection, _name):
        xml = '<node><interface name="org.milo.Companion"><method name="TestVoice"/><method name="Say"><arg type="s" direction="in"/></method><method name="Ask"><arg type="s" direction="in"/></method><method name="Listening"><arg type="b" direction="in"/></method><method name="Press"><arg type="s" direction="in"/></method><method name="Release"><arg type="s" direction="in"/></method></interface></node>'
        info = Gio.DBusNodeInfo.new_for_xml(xml)
        self.bus_registration = connection.register_object('/org/milo/Companion', info.interfaces[0], self.hold_message, None, None)

    def hold_message(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        if method == "TestVoice":
            self.view.evaluate_javascript("window.miloTestVoice?.()", -1, None, None, None, None, None)
            invocation.return_value(GLib.Variant("()", ()))
            return
        if method == "Listening":
            # The hotkey assistant holds the key: the icon shows the listening animation, no recording here.
            on = bool(parameters.unpack()[0])
            self.view.evaluate_javascript("window.miloListening?.(" + ("true" if on else "false") + ")", -1, None, None, None, None, None)
            invocation.return_value(GLib.Variant("()", ()))
            return
        if method in ("Say", "Ask"):
            # Say speaks fixed text (reminders); Ask submits a question as a full turn, which is how
            # the hotkey assistant (jarvis, now milo) hands over anything that is not a desktop command.
            text = parameters.unpack()[0]
            if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                invocation.return_dbus_error('org.milo.InvalidText', 'Invalid speech text')
                return
            if not self.ready or self.failed:
                invocation.return_dbus_error('org.milo.NotReady', 'Milo overlay is not ready')
                return
            bridge = "window.miloSay?.(" if method == "Say" else "window.miloAsk?.("
            self.view.evaluate_javascript(bridge + json.dumps(text.strip()) + ")", -1, None, None, None, None, None)
            event('ask received' if method == "Ask" else 'say received')
            invocation.return_value(GLib.Variant("()", ()))
            return
        token = parameters.unpack()[0]
        if not token or len(token) > 64 or any(c not in '0123456789:' for c in token):
            invocation.return_dbus_error('org.milo.InvalidToken', 'Invalid hold token')
            return
        if method == 'Release':
            self.released_tokens.append(token)
            if token == self.hold_token:
                self.hold_token = None
                self.microphone_requested = None
                self.view.evaluate_javascript('window.miloHoldRelease?.()', -1, None, None, None, None, None)
                event('hold released')
        elif self.ready and not self.failed and token not in self.released_tokens and token != self.hold_token:
            self.hold_token = token
            self.microphone_requested = time.monotonic()
            self.view.evaluate_javascript('window.miloHoldPress?.()', -1, None, None, None, None, None)
            event('hold pressed')
        invocation.return_value(GLib.Variant('()', ()))

    def local(self, uri):
        try:
            parsed = urlsplit(uri or "")
            return (
                parsed.scheme == "http"
                and parsed.hostname == "127.0.0.1"
                and parsed.port == self.port
                and parsed.username is None
                and parsed.password is None
            )
        except ValueError:
            return False

    def primary_monitor(self):
        """The Gdk monitor for MILO_MONITOR (default DP-1), matched by its Hyprland position.

        Layer-shell surfaces otherwise land on whichever output the compositor
        picks, and Milo belongs on the primary screen every time.
        """
        wanted = os.environ.get("MILO_MONITOR", "DP-1")
        try:
            rows = json.loads(subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=3).stdout)
            target = next((m for m in rows if m.get("name") == wanted), None)
        except (OSError, ValueError, subprocess.SubprocessError):
            target = None
        display = self.window.get_display()
        if target is not None:
            for index in range(display.get_n_monitors()):
                monitor = display.get_monitor(index)
                geometry = monitor.get_geometry()
                if (geometry.x, geometry.y) == (target["x"], target["y"]):
                    event("monitor " + wanted)
                    return monitor
        event("monitor fallback for " + wanted)
        return display.get_primary_monitor()

    def resize(self):
        """The layer keeps one fixed size; only the input region ever changes.

        Resizing the layer-shell surface for every thought bubble made Hyprland animate
        the change with its overshoot curve (a visible grow-then-shrink), and a surface
        that covers the whole box swallowed clicks meant for windows behind it. The box
        stays COLLAPSED_SIZE, the keyboard is never grabbed, and the input region names
        only the open bubble, so the robot and everything else pass through to the desktop.
        """
        width, height = COLLAPSED_SIZE
        self.size = (width, height)
        self.view.set_size_request(width, height)
        self.window.resize(width, height)
        GtkLayerShell.set_keyboard_mode(
            self.window,
            GtkLayerShell.KeyboardMode.NONE,
        )
        self.apply_input_region()

    @staticmethod
    def valid_rect(rect):
        """A page rectangle {x, y, w, h} in CSS pixels, or None when malformed."""
        if not isinstance(rect, dict):
            return None
        values = []
        for key in ("x", "y", "w", "h"):
            value = rect.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0 or value > 10000:
                return None
            values.append(value)
        if values[2] < 1 or values[3] < 1:
            return None
        return tuple(values)

    def input_rectangles(self):
        """Window-coordinate rectangles that accept pointer input: the open bubble, nothing else."""
        if not self.thought_rect:
            return []
        x, y, w, h = self.thought_rect
        pad = 4
        return [(max(0, x - pad), max(0, y - pad), w + 2 * pad, h + 2 * pad)]

    def apply_input_region(self):
        region = cairo.Region()
        for x, y, w, h in self.input_rectangles():
            region.union(cairo.RectangleInt(int(x), int(y), int(w), int(h)))
        self.window.input_shape_combine_region(region)

    def focus_out(self, *_args):
        GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.NONE)
        return False

    def audio_event(self, value):
        self.view.evaluate_javascript("window.miloNativeAudio?.("+json.dumps(value)+")", -1, None, None, None, None, None)

    def message(self, _manager, result):
        if not self.local(self.view.get_uri()):
            return
        try:
            message = json.loads(result.get_js_value().to_string())
        except (ValueError, TypeError):
            return
        if not isinstance(message, dict):
            return
        action = message.get("action")
        if action in ("pcm-start", "pcm-audio", "pcm-sentence-end", "pcm-end", "pcm-stop"):
            try:
                if self.audio: self.audio.handle(message)
            except (ValueError, KeyError, TypeError):
                self.audio_event({"id": message.get("id"), "type": "error", "message": "Native streaming audio failed."})
                if self.audio: self.audio.stop()
        elif action == "playback-error":
            print(json.dumps({"event":"playback error", "detail":str(message.get("detail"))[:250]}), flush=True)
        elif action == "audio-meter":
            print(json.dumps({"event":"audio meter", "state":message.get("state"), "peak":message.get("peak")}), flush=True)
        elif action == "diagnostic":
            kind = message.get("kind")
            if kind in ("working", "transcript", "speaking", "caption", "idle", "error", "listening"):
                event("client " + kind)
        elif action == "thought-open":
            rect = message.get("rect")
            self.thought_rect = self.valid_rect(rect)
            self.apply_input_region()
        elif action == "thought-close":
            self.thought_rect = None
            self.apply_input_region()
        elif action == "copy":
            text = message.get("text")
            if not isinstance(text, str) or not text or len(text) > 10000:
                event("copy refused")
                return
            try:
                subprocess.run(["wl-copy"], input=text, text=True, timeout=3, check=True)
            except (OSError, subprocess.SubprocessError):
                event("copy failed")
            else:
                print(json.dumps({"event": "copied", "chars": len(text)}), flush=True)
        elif action == "open-demos":
            try:
                Gio.AppInfo.launch_default_for_uri(
                    f"http://127.0.0.1:{self.port}/demo", None
                )
            except GLib.Error:
                event("browser launch failed")
        elif action == "microphone-start":
            self.microphone_requested = time.monotonic()
        elif action == "microphone-stop":
            self.microphone_requested = None

    def permission(self, view, request):
        recent = (
            self.microphone_requested is not None
            and 0 <= time.monotonic() - self.microphone_requested <= 10
        )
        allowed = (
            self.local(view.get_uri())
            and recent
            and isinstance(request, WebKit2.UserMediaPermissionRequest)
            and request.props.is_for_audio_device
            and not request.props.is_for_video_device
            and not WebKit2.user_media_permission_is_for_display_device(request)
        )
        self.microphone_requested = None
        if allowed:
            request.allow()
        else:
            request.deny()
        event("permission allowed" if allowed else "permission denied")
        return True

    def navigation(self, _view, decision, kind):
        if kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()
            return True
        if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if not self.local(uri):
                decision.ignore()
                return True
            self.microphone_requested = None
        return False

    def loaded(self, _view, state):
        if state == WebKit2.LoadEvent.STARTED:
            if self.audio: self.audio.stop()
            self.failed = False
        if state == WebKit2.LoadEvent.FINISHED and not self.failed:
            if self.audio and self.audio.available:
                self.view.evaluate_javascript("window.miloPcmStreaming=true", -1, None, None, None, None, None)
        if state == WebKit2.LoadEvent.FINISHED and not self.ready and not self.failed:
            self.ready = True
            event("ready")

    def load_failed(self, *_args):
        self.failed = True
        event("load failed")
        return False

    def run(self):
        self.window.show_all()
        self.apply_input_region()
        try:
            Gtk.main()
        finally:
            if self.audio: self.audio.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=demo_port, default=8776)
    args = parser.parse_args()
    initialized, _ = Gtk.init_check([])
    if not initialized or not GtkLayerShell.is_supported():
        print("Milo demo needs a Wayland desktop with layer-shell support.", file=sys.stderr)
        return 1
    Desktop(args.port).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
