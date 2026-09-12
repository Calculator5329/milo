# Setting up Milo

Milo needs four things on the machine: Python 3.11 to 3.13 in a virtual environment, Ollama
with one model pulled, a whisper.cpp server, and the cached Pocket TTS voices. This page has
the exact commands for Windows 11 and for Arch-based Linux (CachyOS, Arch, EndeavourOS).
Other Linux distributions differ only in the package manager lines.

If an agent is doing this for you, it follows `CLAUDE.md` and uses this page for commands.
Run `python milo.py doctor` at any point to see what is still missing.

Facts about package names, release assets and sizes below were checked on 2026-09-12.

## 1. Python and the virtual environment

Milo's launcher (`milo.py`) looks for `.venv` in the repo root and uses it automatically.

**Windows 11**

```powershell
winget install --id Python.Python.3.12 -e
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python -m pip install -r requirements.txt
```

**CachyOS / Arch**

Arch ships the newest Python, which is often ahead of what torch supports. Check `python3
--version`; if it is 3.14 or newer, install a side interpreter with `sudo pacman -S python312`
(from the AUR when not in the repos) or `uv python install 3.12` if you use uv.

```sh
python3.12 -m venv .venv          # or python3.13, or python3 if it is 3.11 to 3.13
.venv/bin/pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r requirements.txt
```

CPU torch is deliberate. Pocket TTS runs fine on two CPU threads and this keeps the whole
GPU for the language model. The CPU wheel is about 200 MB; the CUDA one is over 2 GB and would
also compete with Ollama for VRAM.

## 2. Ollama and a model

**Windows 11**: `winget install --id Ollama.Ollama -e`, or download
https://ollama.com/download/OllamaSetup.exe. Ollama registers as a login item and serves on
http://127.0.0.1:11434. Then, in a new terminal:

```powershell
ollama pull gemma4:12b
```

**CachyOS / Arch**: `sudo pacman -S ollama-cuda` on NVIDIA (`ollama-rocm` on AMD, `ollama`
for CPU only), then `sudo systemctl enable --now ollama` and `ollama pull gemma4:12b`.

Pick the tag from `docs/models.md` or from the doctor's `suggest` line, not blindly. A 12B
model is about 8 GB to download and needs about 10 GB of VRAM beside Whisper.

## 3. whisper.cpp server

Milo posts 16 kHz mono WAV to the server's `/inference` endpoint and reads the JSON reply.
The model file decides accuracy and VRAM: `ggml-large-v3-turbo.bin` (1.6 GB) is what Milo was
tuned with; `ggml-base.en.bin` (148 MB) is the fallback for machines without a GPU.

Model downloads (Hugging Face, `ggerganov/whisper.cpp`):

- https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
- https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin

**Windows 11**: download a prebuilt zip from the whisper.cpp releases page
(https://github.com/ggml-org/whisper.cpp/releases; binaries hang off the numbered `bNNNN`
tags). `whisper-cublas-12.4.0-bin-x64.zip` (about 675 MB, bundles the CUDA runtime, needs only
the NVIDIA driver) for NVIDIA GPUs; `whisper-bin-x64.zip` (about 9 MB) for CPU. Unzip; the
binaries are under `Release\`. Put the model file next to them and start:

```powershell
.\Release\whisper-server.exe -m .\ggml-large-v3-turbo.bin --host 127.0.0.1 --port 8178 -t 4
```

**CachyOS / Arch**: `sudo pacman -S whisper-cpp` installs the CPU build with
`/usr/bin/whisper-server`. For the GPU build use the AUR package `whisper.cpp-cuda-bin`
(check the binary name with `pacman -Ql` afterwards). Then:

```sh
mkdir -p ~/.local/share/whisper
curl -L -o ~/.local/share/whisper/ggml-large-v3-turbo.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
whisper-server -m ~/.local/share/whisper/ggml-large-v3-turbo.bin --host 127.0.0.1 --port 8178 -t 4
```

Keep that process running while Milo runs (a second terminal, or the autostart section
below). If you already have a whisper.cpp server on another port, set `whisper_url` in
`milo.config.json` instead of starting a second one.

## 4. Configure

```sh
cp milo.config.example.json milo.config.json      # copy on Windows
```

Set `model` to the tag you pulled, `user_name` to what Milo should call you (or leave it
empty), and trim `models` to the tags you have actually pulled. Every key is documented in
`milo/config.py`. `MILO_PORT=8767` style environment variables override the file for one run.

## 5. Cache the voices, then verify

```sh
python milo.py setup-voices    # one-time, downloads ~250 MB into models/
python milo.py doctor          # must end with READY
python milo.py test            # unit tests, a few seconds
```

Then a real pipeline check. In one terminal:

```sh
python milo.py --port 8767
```

Wait for `Milo ready`. In another:

```sh
python milo.py probe --turns 4 --label setup
```

The probe sends synthetic text and audio turns and prints p50/p95 for every stage; the file
lands in `evidence/local/`. `client_first_audio_ms` is the number that matters: when speech
begins after you stop talking. `docs/latency.md` shows what a healthy machine looks like.

## 6. Run it

```sh
python milo.py
```

Open http://127.0.0.1:8766, click **Start conversation**, allow the microphone. Use headphones
the first time; speaker echo can interrupt Milo mid-sentence until you tune the silence gate
in Settings. Escape stops speech. Typing works without a microphone.

## Autostart (optional)

**Linux, user services** (no root). Two units, one for Whisper and one for Milo:

```ini
# ~/.config/systemd/user/whisper-server.service
[Unit]
Description=whisper.cpp server for Milo
[Service]
ExecStart=/usr/bin/whisper-server -m %h/.local/share/whisper/ggml-large-v3-turbo.bin --host 127.0.0.1 --port 8178 -t 4
Restart=on-failure
[Install]
WantedBy=default.target
```

```ini
# ~/.config/systemd/user/milo.service
[Unit]
Description=Milo local voice companion
After=whisper-server.service
[Service]
WorkingDirectory=%h/milo
ExecStart=%h/milo/.venv/bin/python %h/milo/milo.py
Restart=on-failure
[Install]
WantedBy=default.target
```

`systemctl --user daemon-reload && systemctl --user enable --now whisper-server milo`.
Adjust the paths to where you cloned the repo.

**Windows**: the simplest reliable option is a shortcut in `shell:startup` that runs
`whisper-server.exe` and one that runs `.venv\Scripts\pythonw.exe milo.py` from the repo
folder. Task Scheduler works too. Ollama already starts itself.

## When something is off

- `doctor` says whisper unreachable: the server is not running or is on another port.
- Replies but no sound: check the browser tab's own volume in the OS mixer; a muted app
  stream looks exactly like a working one from the page's side.
- Turns end mid-sentence: raise the silence slider in Settings toward 500 ms, or set
  `silence_ms` in the config.
- Long pause before the first word: run the probe. If `first_token_ms` is high, the model is
  spilling to CPU; pick a smaller tier in `docs/models.md`.
