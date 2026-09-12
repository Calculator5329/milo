# Milo: instructions for the agent setting this up

You are reading this because someone cloned Milo and asked you to set it up on their machine.
Milo is a local voice companion: whisper.cpp transcribes, Ollama answers, Pocket TTS speaks,
and a small browser page animates the robot. Everything runs on this computer. The web is
used only when the person explicitly asks Milo to look something up.

Your job is to get `python milo.py doctor` to print `READY`, then start Milo and prove one
spoken turn works. Read `docs/setup.md` for the platform commands and `docs/models.md` for
which model fits this machine. Do not skip the doctor; it is the checklist.

## The procedure

1. **Measure first.** Run `python milo.py doctor` (any Python 3 works for this first run).
   It reports Python, the venv, installed packages, Ollama, whisper.cpp, GPU VRAM, RAM, and a
   suggested default model. Every later step is fixing one `MISSING` line.
2. **Python 3.11 to 3.13 in `.venv`.** If no suitable interpreter exists, install one the way
   `docs/setup.md` describes for this OS. Then create `.venv` in the repo root, install CPU
   torch with the pinned command, then `requirements.txt`. Never install CUDA torch; speech
   synthesis is CPU by design so the GPU stays free for the language model.
3. **Ollama.** Install it if missing, make sure it is serving on 11434, and pull the model the
   doctor suggested with `ollama pull <tag>`. Pulling is a multi-gigabyte download; tell the
   person before starting it and show progress. Pull only the suggested default unless they ask
   for more menu entries. Remove unpulled entries from `models` in `milo.config.json` so the page
   never offers a model that would cold-download mid-conversation.
4. **whisper.cpp server** on 8178 with a ggml model. `docs/setup.md` has the package or
   release-zip route per OS and the exact start command. If the person already runs a
   whisper.cpp server on another port, point `whisper_url` in the config at it instead of
   starting a second one.
5. **Configure.** Copy `milo.config.example.json` to `milo.config.json` and set `model` to
   the doctor's suggestion. Ask the person one question: what name Milo should call them
   (`user_name`, may stay empty). Leave the voice as `marius`; they can audition others in the
   page's Settings.
6. **Cache the voices.** `python milo.py setup-voices`. This is the one step that downloads
   from Hugging Face (about 250 MB) and writes into `models/`. After it, speech is offline.
7. **Verify.** `python milo.py doctor` must print `READY`. Then `python milo.py test` (unit
   tests, seconds). Then start `python milo.py --port 8767` in the background, wait for
   `Milo ready` on stdout, and run `python milo.py probe --turns 4 --label setup`. The probe
   sends synthetic text and audio turns and prints p50 first-audio latency; it never opens a
   microphone. A first-audio p50 under about 600 ms on a GPU machine, or under 3 s on CPU
   only, means the pipeline is healthy. Stop the 8767 server afterwards.
8. **Hand over.** Start `python milo.py` and tell the person: open http://127.0.0.1:8766,
   click Start conversation, allow the microphone, use headphones for the first try. Give
   them the one-line restart command and where the config lives. Offer the autostart option
   in `docs/setup.md` only if they ask for it.

## Rules while you do this

- Do not run anything with elevated privileges without saying what it installs and why.
  Package installs and the Ollama installer are the only steps that may need it.
- Do not change ports, firewall rules, or other services on the machine. Milo binds
  127.0.0.1 only; nothing here should be exposed to the network.
- Do not modify files under `milo/`, `web/`, or `tests/` to make setup pass. Configuration
  belongs in `milo.config.json` or `MILO_*` environment variables. If the code needs a change
  for this machine, say so and stop; that is a bug report, not a local patch.
- Do not record audio, keep transcripts, or add analytics. The page holds the conversation in
  tab memory only.
- Report measurements as measurements. "The probe measured 310 ms p50 first audio" is a
  fact; "it should be fast" is not. If a step could not be verified, say that.
- If the machine has no NVIDIA GPU, say plainly that replies will be slower, pick the small
  model, and still finish the setup. CPU-only Milo works; it just thinks for longer.

## What Milo deliberately does not do

No desktop actions, file access, shell, email, calendar, or memory between sessions. No paid
API calls. Those are plug-in points for later, not gaps to fill during setup.
