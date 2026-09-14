# Milo demos

Open **http://127.0.0.1:8776/demo**. This is a review branch, with a real local
notebook and optional Linux desktop icon alongside deliberately scripted layout
studies. The installed Windows+Space shortcut and original Milo service are separate.

Ported from the author's private review branch.
Portable artifact: [milo-portable-demo.zip](../distribution/artifacts/milo-portable-demo.zip),
SHA-256 `fcac7f31b9f83843cad069f98d2f8fbd13313e6bc38e32987bb2446f0e58d025`.

## Try it

1. **Corner companion:** compare Peek, Shelf and Sidekick with the arrows. These
   desktop studies are simulations. The separate native corner icon is real: click
   Milo to open it, type a request, or hold to talk. Collapsing stops microphone/audio.
2. **Working notebook:** open the synthetic project brief and ask for its main idea.
   `Create note weekend plan: Take a walk.` writes literal text silently.
   `Add to weekend plan: Bring a notebook.` appends with a retained prior version.
   Search for `seeds`, edit a note, or restore an earlier version. Import a chosen
   Markdown/text file or download the current editor; existing names are never
   overwritten by import.
3. **Draft a guide:** prepares a local model draft using the selected note. Review or
   edit the proposed text, then Apply. It is not saved until applied. The latest
   unapplied draft is available across the demo surfaces until the server restarts
   or another draft replaces it. Edits to an unapplied draft stay in that tab until Apply. Saved notes and their revisions persist on disk.
4. **Choosing a brain:** inspect a rule-based proposal. The inspector never sends it.
   The notebook supports an explicitly configured Chat Completions provider; the
   current question alone goes to it, without history, documents, or raw audio.
5. **Voice Lab and Themes:** compare identical voice samples, natural/clear/small
   speaker filters and narrow playback-rate changes. Pick a theme in the header.
   Settings persist on this computer and sync across notebook/native windows.
   Voice preference is still a listening decision, not a benchmark score.

Desktop commands in the notebook are **previews**, not desktop execution. In the
owner's existing installation those previews use Jarvis's deterministic dry-run
path. Other machines may have no command-preview adapter.

## Run and stop

On the development workstation, `./audio/milo/demo/start` launches the two transient
user services. Stop them with:

```sh
systemctl --user stop milo-corner-demo milo-demos
```

No global shortcut or autostart file is installed. Native overlay uses GTK3,
gtk-layer-shell and WebKit2 on Wayland. The browser notebook can run without it.
See [INSTALL.md](../INSTALL.md) for the portable bootstrap, checkup, archive and
foreground launcher. Pass an explicit voice cache; a global `HF_HOME` can belong to
another program.

## Connection configuration

The API adapter uses the [Chat Completions protocol](https://developers.openai.com/api/reference/resources/chat),
chosen for compatible local and remote servers. Set these **in the server process
environment**, then explicitly select API in the notebook:

- `MILO_API_ENABLED=1`
- `MILO_API_BASE_URL`: API base such as `https://api.openai.com/v1` or a loopback base
- `MILO_API_MODEL`: a model identifier available to that provider
- `MILO_API_KEY`: credential, only in the environment; never in source or screenshots
- `MILO_API_TOKEN_FIELD`: `max_completion_tokens` (default), or `max_tokens` for a
  compatible server that requires it

No paid provider is configured by this experiment. Unsupported models/credentials
produce an error and never trigger a fallback paid request. Explicit external API
use can incur provider charges; local speech generation remains local.

## Evidence and limits

`evidence/live.json` retains real silent writes, restore/stale-write behavior, local
spoken document answers and unsent route proposals. `evidence/browser.json` and the
PNG files retain browser layout checks. Theme and conversation probes have their
own records. `evidence/provider-local.json` proves transport against the existing
local compatible endpoint with a synthetic question; it does not prove a cloud
provider account or model. The additional composed HTTP fixture launch was rejected
by automatic review and remains unrun; unit probes are diagnostic evidence.

No microphone was opened during the autonomous checks. Physical microphone quality,
voice fit and other-platform installation still need real use. The local model can
be wrong; the prompt and formatting cleanup do not verify factual truth. Only the
bounded demo folder is connected, not personal documents elsewhere on the machine.

Native hold-to-talk repair (2026-09-12): the running test uses Windows+Alt+Space
through `hold-keybind.lua` and session D-Bus. The menu remains for testing.
The binding is session-only, and leaves the existing Windows+Space shortcut alone.
Native WebKit needs GStreamer Good's `autoaudiosink`, `pulsesrc`, and
`deinterleave`, in addition to the GTK/WebKit bindings. A process-local plug-in
folder can be supplied with `MILO_GST_PLUGIN_DIR`; never assume GI imports prove
that microphone capture or audible output works. Native sentence playback was confirmed audible on the author's machine on 2026-09-12. Opening the menu does not request keyboard focus;
selecting its text field does. The Test voice button bypasses microphone input.

## Voice and model lab — 2026-09-12

Voice Lab offers twelve official presets, a pitch slider (−8 to +8 semitones), a pace slider (0.65× to 1.50×), and sound profiles. FFmpeg Rubber Band processes each sentence before either client receives it, keeping pitch and pace independent and making the browser and corner output consistent. A changed delivery may wait for sentence synthesis before starting. Existing preferences acquire neutral pitch without losing previous selections.

Choosing a brain now offers three test questions at each of simple, moderate and hard levels. Compare the installed GPT OSS, Phi-4 and Qwen models on the same question, in sequence. Responses and elapsed times are real; times include model loading. Stop cancels the current request and skips the remaining models. Download retains the exact comparison. Cloud comparison remains disabled until an explicit provider is configured. No API key is entered into or retained by this page.

Sources: [official voice catalog](https://huggingface.co/kyutai/tts-voices) and the installed Pocket TTS 3.1 preset mapping. Verification: `verify_voice_model_lab.cjs`, `tests/test_speech_audio.py`, `tests/test_model_comparison.py`, and `demo/evidence/voice-model-lab/`.
