# Which model to run

Milo streams the reply sentence by sentence into speech, so what you feel is time to the
first token, not tokens per second. A model that fits entirely in VRAM beside Whisper answers
in about a tenth of a second; the same model spilling a few layers to system RAM takes three
to five times longer before the first word. Pick by memory first, quality second.

`python milo.py doctor` reads your VRAM and RAM and prints a `suggest` line using this table.

## Tiers

| Tag | Ollama download | Resident on GPU | Needs free VRAM (beside Whisper large-v3-turbo) | When |
|---|---|---|---|---|
| `gemma4:12b` | 7.6 GB | about 8.1 GB | 10 GB or more (12 GB card and up) | Default. Correct on the constraint prompts in the bake-off with no thinking pass, 118 ms p50 first token on a 16 GB card. |
| `gpt-oss:20b` | 13 GB | about 13 GB | 16 GB card, and it still spills a little | "Careful" entry. It reasons before answering (`think: low`) and gets a 1600-token budget. Slower first word, best on tricky constraint questions. |
| `phi4:latest` | 9.1 GB | about 9.1 GB | 11 GB or more | "Balanced". Fine conversationally; failed one arithmetic time-budget case in the bake-off. |
| `gemma4:4b` | about 3 GB | about 3.3 GB | 4.5 GB or more (6 GB and 8 GB cards) | The right choice for small GPUs and for CPU-only machines with 16 GB RAM. |
| `qwen2.5:3b` | 1.9 GB | about 2 GB | 3 GB, or CPU only | Smallest. Fast, chatty, weak on anything with numbers. |

Whisper `large-v3-turbo` takes roughly 1.6 GB of VRAM when the whisper.cpp server is a GPU
build. On a small card, switch it to `ggml-base.en.bin` (about 0.2 GB) to free room for a
bigger language model; transcription gets a little less accurate on names and accents.

## How the numbers were chosen

The default came from a bake-off on 2026-09-12 with the real pipeline (not a benchmark
harness): each candidate answered six ordinary conversational prompts plus four constraint
prompts that have one right answer, with thinking disabled, through Milo's own system prompt.
First-token time and correctness were recorded per model.

- Gemma 4 12B matched GPT OSS 20B on the constraint prompts and was the largest model that
  stayed entirely on a 16 GB GPU beside Whisper. It became the default.
- GPT OSS 20B was correct but spilled to CPU on a 16 GB card, so it stays as the slow option.
- Qwen 3.5 9B and 4B were dropped: with thinking off they got the math or the year wrong; with
  thinking on they spent the whole token budget thinking and produced no answer.
- Phi-4 got one time-budget question wrong and stays as a middle option.

Your card is not this card. Run the probe (`python milo.py probe --turns 6 --label <tag>`)
after switching models and compare `first_token_ms` and `client_first_audio_ms` p50 between
labels. If the first token takes more than about 400 ms on a GPU machine, the model does not
fit and a smaller tier will feel better even if it is a little less clever.

## Changing the menu

`models` in `milo.config.json` is the Settings menu. Keys are Ollama tags, values are the
labels people see. Any pulled Ollama model can go in; Milo sends `think: low` only to tags
starting with `gpt-oss` and gives every other model a 320-token reply budget, which keeps
spoken answers short. Set `model` to the entry the page should start on.
