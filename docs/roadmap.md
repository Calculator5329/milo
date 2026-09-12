# Roadmap

Open work is a checkbox. Shipped work moves to `docs/changelog.md`.

## Now

- [ ] First setup by a stranger on Windows 11 through the agent path, with the doctor output and probe JSON attached to the report. Until then Windows is documented, not proven.
- [ ] First setup on CachyOS on a machine that is not the author's, same evidence.
- [ ] Voice fit: the current presets read as "narrator", not "small robot". Try the pitch and playback processing from the author's private branch as an optional setting, measured for first-audio impact.

## Next

- [ ] Optional offline library plug-in: ask a local Kiwix server before the web, cited passages. Was in the private build; needs a self-contained client to come back.
- [ ] `gemma4:4b` and CPU-only reference measurements in `docs/evidence/`, so the tier table has readings for small machines instead of estimates.
- [ ] Non-NVIDIA VRAM reading in the doctor (ROCm, Intel) so the suggestion is not RAM-only there.

## Later

- [ ] Plug-in point for actions (open this, run that). Deliberately not in the base install; the prompt tells the model it has no tools until someone wires some.
- [ ] Desktop corner presence: an always-visible small Milo that opens the page, without continuous listening.
