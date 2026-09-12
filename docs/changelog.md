# Changelog

## 2026-09-12

Standalone Milo, extracted from the author's local-ai-lab experiment for distribution.

- One-folder layout with a cross-platform launcher (`milo.py`) that finds `.venv` itself.
- Settings moved out of the code into `milo.config.json` and `MILO_*` variables: user name,
  model menu, voice, ports, service URLs, models directory, silence gate, TTS threads.
- `scripts/doctor.py`: measures Python, packages, voices, Ollama, whisper.cpp, VRAM and RAM,
  suggests a model tier, exits non-zero until the machine can run Milo.
- `CLAUDE.md` setup contract so a coding agent can do the install from a clone.
- Setup, model tier, and latency documentation with a reference probe reading
  (RTX 5070 Ti, gemma4:12b: 304 ms p50 first audio on text turns, 404 ms on audio turns).
- Offline-library lookup removed from this build (it depended on a private sibling package);
  web lookup stays. Personal names and machine paths removed from prompts and defaults.
- Verified on the author's CachyOS machine from a fresh venv: doctor READY, 18 unit tests,
  probe against a fresh server on port 8767.
