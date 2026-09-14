"""The corner app's D-Bus Ask method hands a hotkey question to the overlay as a normal turn."""
import json
import pathlib
import sys
import types
import unittest

HERE = pathlib.Path(__file__).resolve().parents[1]


def load_desktop():
    """Import demo/desktop.py with the GTK stack stubbed out (no display in tests)."""
    gi = types.ModuleType('gi')
    gi.require_version = lambda *a, **k: None
    repository = types.ModuleType('gi.repository')

    class Variant:
        def __init__(self, signature, value):
            self.signature, self.value = signature, value

    class Stub:
        def __getattr__(self, name):
            return Stub()

        def __call__(self, *a, **k):
            return Stub()

    repository.GLib = types.SimpleNamespace(Variant=Variant)
    for name in ('Gdk', 'Gio', 'Gtk', 'GtkLayerShell', 'WebKit2'):
        setattr(repository, name, Stub())
    gi.repository = repository
    saved = {k: sys.modules.get(k) for k in ('gi', 'gi.repository')}
    sys.modules['gi'], sys.modules['gi.repository'] = gi, repository
    sys.path.insert(0, str(HERE / 'demo'))
    try:
        try:
            import cairo  # noqa: F401
        except ImportError:
            raise unittest.SkipTest('pycairo is not installed; the overlay is a Linux desktop option')
        if 'desktop' in sys.modules:
            del sys.modules['desktop']
        import desktop
        return desktop
    finally:
        sys.path.pop(0)
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class FakeView:
    def __init__(self):
        self.scripts = []

    def evaluate_javascript(self, script, *_):
        self.scripts.append(script)


class FakeInvocation:
    def __init__(self):
        self.value, self.error = None, None

    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = (name, message)


class Params:
    def __init__(self, *values):
        self.values = values

    def unpack(self):
        return self.values


class AskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.desktop = load_desktop()

    def app(self, ready=True, failed=False):
        app = self.desktop.Desktop.__new__(self.desktop.Desktop)
        app.ready, app.failed, app.view = ready, failed, FakeView()
        app.released_tokens, app.hold_token, app.microphone_requested = [], None, None
        return app

    def call(self, app, method, text):
        invocation = FakeInvocation()
        app.hold_message(None, None, None, None, method, Params(text), invocation)
        return invocation

    def test_interface_exposes_ask_next_to_say(self):
        source = (HERE / 'demo' / 'desktop.py').read_text(encoding='utf-8')
        self.assertIn('<method name="Ask"><arg type="s" direction="in"/></method>', source)

    def test_ask_submits_the_question_through_the_overlay_bridge(self):
        app = self.app()
        invocation = self.call(app, 'Ask', '  who was better, Jordan or LeBron?  ')
        self.assertIsNone(invocation.error)
        self.assertEqual(app.view.scripts, ['window.miloAsk?.(' + json.dumps('who was better, Jordan or LeBron?') + ')'])

    def test_say_still_speaks_rather_than_asks(self):
        app = self.app()
        self.call(app, 'Say', 'Time to stretch')
        self.assertEqual(app.view.scripts, ['window.miloSay?.("Time to stretch")'])

    def test_ask_rejects_empty_or_oversized_text_and_a_cold_overlay(self):
        app = self.app()
        self.assertEqual(self.call(app, 'Ask', '   ').error[0], 'org.milo.InvalidText')
        self.assertEqual(self.call(app, 'Ask', 'x' * 2001).error[0], 'org.milo.InvalidText')
        self.assertEqual(self.call(self.app(ready=False), 'Ask', 'hello').error[0], 'org.milo.NotReady')
        self.assertEqual(app.view.scripts, [])

    def test_listening_only_animates_the_icon(self):
        app = self.app()
        self.assertIsNone(self.call(app, 'Listening', True).error)
        self.assertIsNone(self.call(app, 'Listening', False).error)
        self.assertEqual(app.view.scripts, ['window.miloListening?.(true)', 'window.miloListening?.(false)'])
        overlay = (HERE / 'demo' / 'overlay.js').read_text(encoding='utf-8')
        self.assertIn("window.miloListening=on=>", overlay)
        self.assertIn("dataset.state='listening'", overlay)
        css = (HERE / 'demo' / 'overlay.css').read_text(encoding='utf-8')
        self.assertIn('@keyframes listen', css)
        self.assertIn('[data-state=waiting] #robot{animation:think', css)
        self.assertIn('@keyframes think', css)

    def test_pressing_the_hotkey_stops_current_speech(self):
        overlay = (HERE / 'demo' / 'overlay.js').read_text(encoding='utf-8')
        listening = overlay[overlay.index('window.miloListening=on=>'):]
        listening = listening[:listening.index('};') + 2]
        self.assertIn("if(on){client.stop();", listening)

    def test_the_overlay_theme_applies_without_a_picker_mount(self):
        """2026-09-13: with the panel gone the theme code found no
        mount and returned before applying anything, so Milo showed the default ivory look whatever
        the saved preference said. The theme and robot art must apply even with no picker to place."""
        themes = (HERE / 'demo' / 'themes.js').read_text(encoding='utf-8')
        self.assertNotIn("if (!mount || document.querySelector('.milo-theme-picker')) return null;", themes)
        self.assertIn('const withPicker = Boolean(mount);', themes)
        self.assertIn("  setTheme(current, { announce: false });", themes)
        self.assertIn("note: 'Black lacquer and signal blue'", themes)
        self.assertIn("face: '#4fb3ff'", themes)

    def test_the_robot_is_click_through_and_there_is_no_panel_or_text_box(self):
        """Clicks on Milo must reach the window behind him (a fullscreen button under his
        corner); the panel animation goes, speech and the bubble carry every reply."""
        overlay = (HERE / 'demo' / 'overlay.js').read_text(encoding='utf-8')
        html = (HERE / 'demo' / 'overlay.html').read_text(encoding='utf-8')
        css = (HERE / 'demo' / 'overlay.css').read_text(encoding='utf-8')
        source = (HERE / 'demo' / 'desktop.py').read_text(encoding='utf-8')
        for gone in ('expand', 'collapse', "$('#text')", "$('#form')", "$('#hold')", "$('#minimize')"):
            self.assertNotIn(gone, overlay)
        for gone in ('id="panel"', 'id="text"', '<form', '<button'):
            self.assertNotIn(gone, html)
        self.assertNotIn('panelIn', css)
        self.assertIn('#robot{position:absolute;bottom:0;right:0;width:108px;height:128px;border:0;padding:0;background:transparent;pointer-events:none}', css)
        for gone in ('EXPANDED_SIZE', 'ROBOT_SIZE', 'action == "expand"', 'action == "collapse"', 'KeyboardMode.ON_DEMAND'):
            self.assertNotIn(gone, source)
        app = self.desktop.Desktop.__new__(self.desktop.Desktop)
        app.size = self.desktop.COLLAPSED_SIZE
        app.thought_rect = None
        self.assertEqual(app.input_rectangles(), [])
        app.thought_rect = (5, 60, 370, 90)
        self.assertEqual(app.input_rectangles(), [(1, 56, 378, 98)])

    def test_a_spoken_reminder_never_opens_the_panel(self):
        overlay = (HERE / 'demo' / 'overlay.js').read_text(encoding='utf-8')
        say = overlay[overlay.index('async function sayFromReminder'):]
        say = say[:say.index('/api/demo/say')]
        self.assertNotIn('expand()', say)

    def test_overlay_defines_the_ask_bridge_as_a_normal_turn(self):
        overlay = (HERE / 'demo' / 'overlay.js').read_text(encoding='utf-8')
        self.assertIn('window.miloAsk=text=>', overlay)
        self.assertIn('client.submit(question)', overlay)

    def test_a_hotkey_ask_never_opens_the_panel(self):
        overlay = (HERE / 'demo' / 'overlay.js').read_text(encoding='utf-8')
        ask = overlay[overlay.index('window.miloAsk=text=>'):]
        ask = ask[:ask.index('};') + 2]
        self.assertNotIn('expand()', ask)

    def test_thought_bubble_and_copy_bridge_are_wired(self):
        overlay = (HERE / 'demo' / 'overlay.js').read_text(encoding='utf-8')
        html = (HERE / 'demo' / 'overlay.html').read_text(encoding='utf-8')
        self.assertIn('id="thought"', html)
        self.assertIn("event.type==='thought'", overlay)
        self.assertIn("bridge('thought-open',{rect:thoughtRect()})", overlay)
        self.assertIn("bridge('copy',{text:", overlay)
        self.assertNotIn('.focus()', overlay)

    def test_desktop_copy_uses_wl_copy_and_logs_only_the_length(self):
        source = (HERE / 'demo' / 'desktop.py').read_text(encoding='utf-8')
        self.assertIn('action == "copy"', source)
        self.assertIn('subprocess.run(["wl-copy"], input=text, text=True, timeout=3, check=True)', source)
        self.assertIn('{"event": "copied", "chars": len(text)}', source)
        copied_log = next(line for line in source.splitlines() if '"event": "copied"' in line)
        self.assertNotIn('"text"', copied_log)

    def test_thought_bubble_changes_the_input_region_not_the_window_size(self):
        source = (HERE / 'demo' / 'desktop.py').read_text(encoding='utf-8')
        self.assertIn('action == "thought-open"', source)
        self.assertIn('action == "thought-close"', source)
        self.assertIn('KeyboardMode.NONE', source)
        self.assertIn('COLLAPSED_SIZE = (380, 330)', source)
        self.assertIn('input_shape_combine_region', source)
        self.assertNotIn('160 + 28 *', source)
        ns = {}
        import textwrap
        block = source[source.index('    @staticmethod\n    def valid_rect'):source.index('    def input_rectangles')]
        exec(textwrap.dedent(block).replace('@staticmethod\n', '', 1), ns)
        valid_rect = ns['valid_rect']
        self.assertEqual(valid_rect({'x': 5, 'y': 60, 'w': 370, 'h': 90}), (5, 60, 370, 90))
        self.assertIsNone(valid_rect({'x': -1, 'y': 60, 'w': 370, 'h': 90}))
        self.assertIsNone(valid_rect({'x': 5, 'y': 60, 'w': 0, 'h': 90}))
        self.assertIsNone(valid_rect({'x': True, 'y': 60, 'w': 370, 'h': 90}))
        self.assertIsNone(valid_rect('nope'))

    def test_the_corner_app_cannot_be_quit_from_the_panel(self):
        overlay = (HERE / 'demo' / 'overlay.js').read_text(encoding='utf-8')
        html = (HERE / 'demo' / 'overlay.html').read_text(encoding='utf-8')
        source = (HERE / 'demo' / 'desktop.py').read_text(encoding='utf-8')
        self.assertNotIn('#quit', overlay)
        self.assertNotIn('id="quit"', html)
        self.assertNotIn('action == "quit"', source)
        self.assertIn('Restart=always', (HERE / 'demo' / 'start').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
