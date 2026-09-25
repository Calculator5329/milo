# Changelog

## 2026-09-24: Latency table, interrupt probe and demo, CI workflow

- The README opens with a measured latency table, from the 2026-09-12 reference reading and a
  2026-09-24 reading taken on a busy machine, with the evidence files in `docs/evidence/`.
- `scripts/interrupt_probe.py` measures how fast a cancelled turn stops on the server: 14.5 ms
  p50 to stream close, no audio chunks after the cancel, six turns.
- `docs/interrupt-demo.gif` and `.mp4`: a real interrupted turn on the page, recorded headless
  by `scripts/record_interrupt_demo.cjs`. No audio.
- The latency probe wrote to a `milo/evidence/` folder that does not exist and crashed after
  the run; it now writes to `evidence/local/` as `docs/latency.md` says.
- `.github/workflows/tests.yml` runs `python milo.py test` on push. README badge added.
- README gains a "How it was built" section.

## 2026-09-13: Hi-fi theme highlights are blue, the thought bubble follows the theme

- The Hi-fi console theme swaps its signal amber for signal blue: accent, texture line, the robot face and eye glow, the theme swatch and note.
- The corner overlay thought bubble reads panel, line, ink, muted, accent and shadow from the active theme instead of a fixed green.

## 2026-09-13

Synced the full Milo engine into the public repository and refreshed the setup
path around the current launcher.

- Added router-first calculator, unit conversion, clock, spelling, recap,
  follow-up, freshness, workspace, personal-book, and Kiwix library behavior.
- Added transient reminders, hearing repairs, command and link bubbles, and the
  optional Wayland corner overlay with hold-to-talk binding.
- Added the optional news index, remembered notes, cloud-provider boundary, and
  214-question evaluation bank with replay services.
- Documented Windows 11 alongside the proven Arch-based Linux path, with clear
  Linux-only notes for systemd reminders and the Wayland overlay.
- Removed stale checkout paths from freshness, health, and replay units.

## 2026-09-12

Standalone Milo was prepared for distribution.

- Added the one-folder launcher, config example, doctor, setup contract, model
  tier notes, and latency probe.
- Added the browser robot, local speech pipeline, interruption handling, and
  loopback-only defaults.
