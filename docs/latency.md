# Latency: what was done and what it measures

A voice companion lives or dies on the silence between the end of your sentence and the
start of its reply. Milo's target is under half a second on a machine where the model fits
in VRAM. This page lists each change that got it there, the number it moved, and how to
re-measure on your own hardware.

## Reference reading

Fresh setup on 2026-09-12, RTX 5070 Ti 16 GB, `gemma4:12b`, whisper.cpp `large-v3-turbo`
on the GPU, Pocket TTS on two CPU threads. Four synthetic turns of each kind, milliseconds
from the moment the server received the request. Full file:
`docs/evidence/latency-20260912-rtx5070ti-gemma4-12b.json`.

| Stage | Text turn p50 | Audio turn p50 |
|---|---|---|
| transcription done | n/a | 75 |
| first token from Ollama | 114 | 186 |
| first sentence chunk ready | 234 | 336 |
| first audio chunk at the client | 304 | 404 |
| generation done | 474 | 894 |
| last audio sent | 2181 | 3230 |

An audio turn is a spoken question of about three seconds. The 404 ms is what you feel as the
gap. The "last audio sent" numbers are long only because the reply is being spoken.

## The changes, in the order they mattered

1. **Sentence-at-a-time synthesis, concurrent with generation.** The model streams tokens
   into a buffer; each completed sentence is handed to Pocket TTS and its PCM is sent to the
   browser while the next sentence is still being generated. Nothing waits for the whole
   reply. This is the difference between "speaks after 3 s" and "speaks after 0.3 s".
2. **Clause-first first chunk** (`split_sentence` in `milo/server.py`). Only for the first
   chunk of a reply, a comma, semicolon, colon, or " and " counts as a boundary once 40
   characters have streamed in (never inside the first 20). Speech starts on a short clause
   instead of waiting for the first full stop. Moved first audio from about 520 ms to about
   300 ms on text turns.
3. **A model that fits.** The bake-off (`docs/models.md`) replaced `gpt-oss:20b`, which
   spilled to CPU beside Whisper, with `gemma4:12b`, which stays resident. First token went
   from 524 ms to 118 ms p50. No code change; the biggest single win.
4. **No thinking pass for chat models.** Ollama gets `think: false` for every tag except
   `gpt-oss`, and a 320-token reply budget. Reasoning models otherwise spend the budget on
   thoughts and either answer late or not at all.
5. **Tight prompt, short replies.** The system prompt asks for one to three spoken sentences
   under about 85 words. Fewer tokens is less time, and long spoken answers are unpleasant
   anyway.
6. **350 ms end-of-turn silence gate.** The page ends a turn after 350 ms of quiet (was 500).
   Configurable per session in Settings and via `silence_ms`. Measured with synthetic turns
   only; if your natural pauses trip it, raise it.
7. **Warm everything at startup.** The TTS model, the default voice, and the Ollama model
   are loaded and exercised once before the Start button enables, with `keep_alive: 2h` so
   the model does not unload between turns.
8. **Whisper stays loaded.** The whisper.cpp server keeps the model on the GPU between
   requests; transcription of a 3 s clip is about 75 ms. Milo sends raw 16 kHz PCM so no
   transcoding happens on either side.
9. **Interruption by turn id.** Speaking over Milo cancels the running turn on the server
   between audio chunks and drops queued sentences, so the stop is felt within one chunk.
   Only sentences whose playback completed enter the history, so Milo never "remembers"
   saying something you cut off.

## Measure your own machine

```sh
python milo.py --port 8767          # test server, so the one you talk to is untouched
python milo.py probe --turns 6 --label before
# change a model, a voice, the silence gate, whatever
python milo.py probe --turns 6 --label after
```

Compare `first_token_ms` (model fit), `first_audio_chunk_sent` (server side pipeline) and
`client_first_audio_ms` (what you hear) between the two JSON files in `evidence/local/`.
Every `done` event in the page's "Connection and measured timing" panel carries the same
stage marks for real conversations.

Things the probe cannot tell you: whether the sound reached your ears (an app stream muted
in the OS mixer looks identical), whether echo from speakers interrupts replies, and whether
the silence gate cuts off your natural pauses. Those need a person with headphones.
