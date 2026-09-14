# Setting up Milo

Milo needs Python 3.11 to 3.13 in a virtual environment, Ollama with one
model pulled, a whisper.cpp server, and the cached Pocket TTS voices. This page
has commands for Windows 11 and for Arch-based Linux, including CachyOS.

Run `python milo.py doctor` at any point to see what is missing. Optional
features below never block the doctor's `READY` result.

## 1. Python and the virtual environment

Milo's launcher (`milo.py`) looks for `.venv` in the repository root and uses it
automatically.

**Windows 11**

```powershell
winget install --id Python.Python.3.12 -e
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python -m pip install -r requirements.txt
```

**CachyOS / Arch**

Check `python3 --version`. If it is 3.14 or newer, install a supported side
interpreter such as `python312`, or use `uv python install 3.12` if uv is
already installed.

```sh
python3.12 -m venv .venv          # or python3.13, or python3 if it is 3.11 to 3.13
.venv/bin/pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r requirements.txt
```

CPU torch is deliberate. Pocket TTS uses the CPU so the GPU stays available to
the language model.

## 2. Ollama and a model

**Windows 11**: `winget install --id Ollama.Ollama -e`, or download the
installer from https://ollama.com/download/OllamaSetup.exe. Then, in a new
terminal:

```powershell
ollama pull gemma4:12b
```

**CachyOS / Arch**: install the Ollama package matching the hardware, then
start its service and pull the model:

```sh
sudo pacman -S ollama-cuda       # use ollama-rocm on AMD, or ollama for CPU only
sudo systemctl enable --now ollama
ollama pull gemma4:12b
```

Use `docs/models.md` or the doctor's `suggest` line to choose a tag that fits.
Pulling a model is a multi-gigabyte download, so confirm the choice first.

## 3. whisper.cpp server

Milo posts 16 kHz mono WAV to whisper.cpp's `/inference` endpoint and reads the
JSON reply. `ggml-large-v3-turbo.bin` is the tuned model; `ggml-base.en.bin` is
the smaller fallback.

**Windows 11**: download a prebuilt zip from
https://github.com/ggml-org/whisper.cpp/releases, put the model next to the
extracted binary, and run:

```powershell
.\Release\whisper-server.exe -m .\ggml-large-v3-turbo.bin --host 127.0.0.1 --port 8178 -t 4
```

**CachyOS / Arch**

```sh
sudo pacman -S whisper-cpp
mkdir -p ~/.local/share/whisper
curl -L -o ~/.local/share/whisper/ggml-large-v3-turbo.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
whisper-server -m ~/.local/share/whisper/ggml-large-v3-turbo.bin --host 127.0.0.1 --port 8178 -t 4
```

If whisper.cpp already runs on another port, set `whisper_url` in the config
instead of starting a second server.

## 4. Configure

Copy the example from the repository root:

```sh
cp milo.config.example.json milo.config.json
```

On Windows, use `Copy-Item milo.config.example.json milo.config.json` in
PowerShell. Set `model` to a tag that is already pulled, trim `models` to the
tags that are present, and set `user_name` to the name Milo should use, or leave
it empty.

The supported config keys are:

```text
user_name, model, models, voice, port, ollama_url, whisper_url, models_dir,
silence_ms, documents_dir, settings_dir, memory_path, reminders_path,
turn_log, freshness_db, kiwix_url, workspace_root, personal_book
```

Environment variables with the `MILO_` prefix can override config values for one
run. `kiwix_url`, `workspace_root`, and `personal_book` are empty by default.
`models_dir` holds the cached models and voices, `silence_ms` controls when a
spoken turn ends, `documents_dir` and `settings_dir` select local storage
folders, and `turn_log` selects the local JSONL turn ledger.

## 5. Cache the voices, then verify

```sh
python milo.py setup-voices
python milo.py doctor
python milo.py test
```

The voice step downloads the Pocket TTS cache once. The doctor must end with
`READY`.

## 6. Run it

```sh
python milo.py
```

Open http://127.0.0.1:8766, click **Start conversation**, allow the microphone,
and use headphones the first time. Typing works without a microphone. Escape
stops speech.

On Windows 11, the same command is:

```powershell
python milo.py
```

Open the same loopback URL in the browser.

## Offline library, optional

The optional library is a local Kiwix server. Install `kiwix-tools` on
CachyOS/Arch:

```sh
sudo pacman -S kiwix-tools
```

On Windows 11, download the current `kiwix-tools` zip from
https://download.kiwix.org/release/kiwix-tools/ and put `kiwix-serve.exe` on a
known path.

Download ZIM files from https://download.kiwix.org/zim/. The listing changes,
so use the versioned filename it currently provides. The checked listing showed
these reference sizes:

- `wikipedia_en_top_maxi`: about 7.8 GB.
- `wikipedia_en_100`: about 318 MB. Older listings may call this family
  `wikipedia_en_100_maxi`.
- `wikipedia_en_all_maxi`: about 119 GB for the current full English archive.
- `wikibooks_en_all_maxi`: about 5.8 GiB in the current mirror listing.
- `wikivoyage_en_all_maxi`: about 1.1 GiB in the current mirror listing.
- `wiktionary_en_all_maxi`, `archlinux_en_all_maxi`, and
  `unix.stackexchange.com_en_all`: check the listing for the current size.

The practical starter set is the smaller English Wikipedia choice, Wiktionary,
the Arch wiki, Unix Stack Exchange, Wikibooks, and Wikivoyage. The full
Wikipedia archive needs roughly 119 GB before the other books. Do not start a
large download until the person has chosen the set and checked free disk space.

**CachyOS / Arch**

```sh
kiwix-serve --port 8891 /path/to/*.zim
```

**Windows 11**

```powershell
.\kiwix-serve.exe --port 8891 C:\path\to\*.zim
```

If PowerShell does not expand the wildcard for the downloaded binary, list the
ZIM paths explicitly. A Windows Task Scheduler action can run `kiwix-serve.exe`
with arguments `--port 8891 C:\path\to\book-one.zim C:\path\to\book-two.zim`
at logon, with the ZIM folder as the working directory.

For a Linux user unit, install a file such as
`~/.config/systemd/user/kiwix.service`, replacing the ZIM paths:

```ini
[Unit]
Description=Local Kiwix library for Milo
After=network.target

[Service]
ExecStart=/usr/bin/kiwix-serve --port 8891 /path/to/book-one.zim /path/to/book-two.zim
Restart=on-failure

[Install]
WantedBy=default.target
```

Then run `systemctl --user daemon-reload` and
`systemctl --user enable --now kiwix.service`. Add this to `milo.config.json`:

```json
"kiwix_url": "http://127.0.0.1:8891"
```

An empty `kiwix_url` keeps the library off. When enabled, Milo checks the local
library before web search, cites the local passages, and uses Wiktionary for
definition questions. The router's title probe is bounded to about 30 ms in the
middle band. Its catalog probe recognizes book names beginning with
`wikipedia`, `wiktionary`, `wikibooks`, `wikivoyage`, `archlinux`, or
`unix.stackexchange`, matching `PROBE_BOOKS_RE` in `milo/router.py`.

The ZIM files and lookup text stay on the machine. Only a later explicit web
lookup leaves it.

## News index, optional

The freshness index reads `milo/freshness/feeds.json` and stores a rolling local
SQLite index. It answers questions such as “what's in the news” and “anything
new lately”.

**CachyOS / Arch**

```sh
python milo.py freshness
```

To refresh nightly, copy `milo/freshness/milo-freshness.service` and
`milo/freshness/milo-freshness.timer` into `~/.config/systemd/user/`. The unit
uses `WorkingDirectory=%h/milo/milo`; edit it, and the interpreter path if
needed, when the repository is elsewhere. Enable it with:

```sh
systemctl --user daemon-reload
systemctl --user enable --now milo-freshness.timer
```

**Windows 11**

```powershell
schtasks /Create /SC DAILY /TN MiloFreshness /TR "\"C:\path\to\.venv\Scripts\python.exe\" \"C:\path\to\milo.py\" freshness" /ST 03:30 /F
```

The feed URLs and fetched article text leave the machine only during the
ingest. The config key is `freshness_db`.

## Reminders

Phrase reminders in the bounded grammar, for example:

```text
remind me in 20 minutes to stretch
remind me to call Sam in 2 hours
remind me at 7 pm to take the bins out
wake me up at 6:30 am
remind me tomorrow morning to check the oven
remind me on Friday to submit the form
```

The parser also accepts `tonight`, tomorrow afternoon, tomorrow evening,
tomorrow night, and weekday names. Milo creates a transient systemd user timer,
then speaks the result through the overlay or the page. The config key is
`reminders_path`. Reminders are Linux only for now because their timer backend
is systemd user units.

## Corner overlay and hotkey

The overlay is Linux only for now, and it requires Wayland. On CachyOS/Arch,
install:

```sh
sudo pacman -S python-gobject gtk3 gtk-layer-shell webkit2gtk-4.1 python-cairo
```

`desktop.py` uses GTK 3, gtk-layer-shell and WebKit2GTK 4.1 through GObject
introspection. Start the server first, using the same port that the overlay
will open:

```sh
python milo.py --port 8776
python milo/demo/desktop.py --port 8776
```

The Hyprland Lua binding in `milo/demo/hold-keybind.lua` uses `SUPER + ALT +
SPACE`, calls `org.milo.Companion.Press` on key-down, and calls `Release` on
key-up.

Press and Release must carry the same fresh token for each hold; a token that was
already released is ignored, so a fixed token would work once. A plain
`hyprland.conf` can keep the token in a file:

```ini
bind = SUPER ALT, SPACE, exec, sh -c 'date +%s%N > /tmp/milo-hold; gdbus call --session --dest org.milo.Companion --object-path /org/milo/Companion --method org.milo.Companion.Press "$(cat /tmp/milo-hold)"'
bindr = SUPER ALT, SPACE, exec, sh -c 'gdbus call --session --dest org.milo.Companion --object-path /org/milo/Companion --method org.milo.Companion.Release "$(cat /tmp/milo-hold)"'
```

The Lua binding is the tested path.
The overlay uses the server `port` config key; there is no separate overlay
config key. The server must be running before the overlay or hotkey can work.

## Workspace and personal books, optional

**CachyOS / Arch** and **Windows 11** use the same config-file shape. Set
`workspace_root` to a folder, then leave the other optional paths empty until
you want them enabled:

```json
"workspace_root": "C:/path/to/workspace",
"personal_book": "C:/path/to/personal-book.json"
```

Use `/path/to/workspace` on Linux. The workspace book uses the `workspace.json`
manifest in that folder and indexes each listed repository's `docs/roadmap.md`,
`docs/changelog.md`, `STATUS.md`, `README.md`, `CLAUDE.md`, and Markdown files
under `docs/`. Questions such as “what's the status of X” use those local files.
An empty `workspace_root` turns it off.

`personal_book` points to a JSON file with this exact shape:

```json
{
  "roots": ["/path/to/notes", "/path/to/another-notes-folder"],
  "globs": ["**/*.md", "**/*.txt"]
}
```

The roots may be relative to the config file. Milo builds a local FTS index and
consults it only when the person says “check my notes”, “in my notes”, “from my
notes”, “search my notes for”, or “what did I write about”. An empty
`personal_book` turns it off.

Workspace and personal files, their indexes, and their answers stay on the
machine. They are not sent to the web or the optional cloud model.

## Remembered notes

`memory_path` is a Markdown file that the person edits. Milo reads curated
bullet notes under Markdown level-two headings to recall stable preferences,
projects, identity details, and standing reminders across sessions. It is not a
transcript and it does not record audio. The file and its local index stay on
the machine.

**CachyOS / Arch**: set `memory_path` to a path such as
`~/.local/state/milo/memory.md` and edit that file with any Markdown editor.

**Windows 11**: set `memory_path` to a path such as
`%LOCALAPPDATA%\Milo\memory.md` and edit that file with any Markdown editor.

## Cloud model, optional

The optional OpenAI-compatible transport is configured only through the server
environment:

```text
MILO_API_ENABLED=1
MILO_API_BASE_URL=https://provider.example/v1
MILO_API_MODEL=provider-model
MILO_API_MODELS=provider-model,backup-model
MILO_API_KEY=the-credential-in-the-process-environment
MILO_API_TOKEN_FIELD=max_completion_tokens
```

`MILO_API_TOKEN_FIELD` may be `max_completion_tokens` or `max_tokens`. A local
loopback HTTP base is also allowed. Never put a credential in the config file,
source, or screenshots.

Milo asks this provider only when the person says “think carefully”, “think
hard”, “ask the big model”, “use the cloud model”, “use OpenRouter”, “be
thorough”, or asks for a hard question such as a proof, tradeoff comparison,
programming task, or a long question. Only the current question text goes out,
without local history, documents, search snippets, or audio. If the provider is
unavailable, Milo falls back to its local model.

**CachyOS / Arch**: export those variables in the shell or the user service
environment before starting Milo.

**Windows 11**: set the same names in PowerShell before starting Milo:

```powershell
$env:MILO_API_ENABLED = "1"
$env:MILO_API_BASE_URL = "https://provider.example/v1"
$env:MILO_API_MODEL = "provider-model"
```

## Evaluation bank

With the server running, run the 214-question bank with a label:

```sh
python milo.py bank --label mine
```

It writes `milo/evidence/bank-mine.jsonl`. The bank runner also accepts the
flags implemented by `milo/evaluations/bank_run.py`, including `--only`, `--ids`,
`--url`, and `--resume`. The replay evaluator is:

```sh
cd milo
python3 -m evaluations.replay --url http://127.0.0.1:8776 --path /api/demo/turn --label nightly --compare-latest
```

The supplied `milo-replay.service` and `milo-replay.timer` provide a nightly
Linux run. Their working directory is `%h/milo/milo`; edit it and the Python
path if the repository was cloned elsewhere. The evaluation artifacts stay
local.

The bank command works on Windows 11 as well:

```powershell
python milo.py bank --label mine
```

The supplied nightly replay timer is Linux only for now. On Windows, create a
Task Scheduler task with the repository root as its working directory and this
action: `python milo.py bank --label nightly`. To run `replay.py` manually on
Windows, change into the `milo` package directory first and run the same
`python -m evaluations.replay` command with PowerShell line continuation as
needed.

## Autostart (optional)

**Linux, user services** use no root. Create these files under
`~/.config/systemd/user/` and adjust the paths to your clone:

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

Enable them with:

```sh
systemctl --user daemon-reload
systemctl --user enable --now whisper-server milo
```

**Windows 11**: use a shortcut in `shell:startup`, or a Task Scheduler task,
for `whisper-server.exe` and another for `.venv\Scripts\python.exe milo.py`
with the repository root as the working directory. Ollama starts with Windows.

## When something is off

- `doctor` says whisper unreachable: the server is not running or is on another port.
- Replies but no sound: check the browser tab's volume in the OS mixer.
- Turns end mid-sentence: raise `silence_ms` toward 500 ms.
- Long pause before the first word: run the probe and choose a smaller model if
  the model spills into system RAM.
