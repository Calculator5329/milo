# Milo corner overlay

`desktop.py` is an optional Linux Wayland layer-shell client. It puts Milo in
the corner, opens the local `/overlay` page, shows command and link bubbles,
and forwards hold-to-talk events over the session D-Bus name
`org.milo.Companion`. The normal browser page works without the overlay.

The overlay needs the Milo server first. From the repository root, use one
terminal for the server and another for the overlay:

```sh
python milo.py --port 8776
python milo/demo/desktop.py --port 8776
```

The `desktop.py` default is port 8776. Pass `--port 8766` instead when the
server is using its normal port. It refuses to start unless a Wayland
layer-shell environment is available. There is no autostart or global shortcut
installed by this demo.

On CachyOS or Arch, install the requested runtime packages with:

```sh
sudo pacman -S python-gobject gtk3 gtk-layer-shell webkit2gtk-4.1 python-cairo
```

The client uses GTK 3, gtk-layer-shell and WebKit2GTK 4.1 through GObject
introspection. The overlay is Linux only for now, and the layer-shell path is
Wayland only. Milo himself is click-through: clicks land on whatever window is
behind him, and only an open thought bubble accepts a click (to copy its text).
There is no panel and no text box; speech and the bubbles carry every reply.

## Hold-to-talk

The Hyprland Lua binding in `hold-keybind.lua` binds `SUPER + ALT + SPACE`.
It creates a fresh numeric token for each hold and calls the overlay's `Press`
method on key-down and `Release` method on key-up. Load it through the same
Hyprland Lua mechanism used for the rest of your session bindings.

Press and Release must carry the same fresh token for each hold; a token that was
already released is ignored, so a fixed token would work once. A plain
`hyprland.conf` can keep the token in a file:

```ini
bind = SUPER ALT, SPACE, exec, sh -c 'date +%s%N > /tmp/milo-hold; gdbus call --session --dest org.milo.Companion --object-path /org/milo/Companion --method org.milo.Companion.Press "$(cat /tmp/milo-hold)"'
bindr = SUPER ALT, SPACE, exec, sh -c 'gdbus call --session --dest org.milo.Companion --object-path /org/milo/Companion --method org.milo.Companion.Release "$(cat /tmp/milo-hold)"'
```

The Lua binding is the tested path.

When the overlay or server is stopped, the binding has nothing to contact. The
hotkey does not run shell commands from Milo's spoken answer, and reminder
delivery uses the same overlay D-Bus name when it is available.
