#!/usr/bin/env python3
"""Parse Milo emotion markers and render a small local listening comparison."""
from __future__ import annotations

import argparse
import array
import base64
import html
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import wave

from speech_audio import process


TAGS = ("calm", "warm", "excited", "serious", "playful", "sorry")
PROMPT_HINT = (
    "For clear feeling, start each sentence with [calm], [warm], [excited], "
    "[serious], [playful], or [sorry]; otherwise use no tag."
)
BASELINE_OPTIONS = {"pitch": 3, "rate": 1.65, "sound": "natural"}
VOICE = "peter_yearsley"
RATE_RANGE = (.65, 2)
PITCH_RANGE = (-8, 8)
SILENCE_SECONDS = .14

_DELTAS = {
    "calm": {"pitch": -1, "rate": -.15},
    "warm": {"pitch": -.5, "rate": 0},
    "excited": {"pitch": 1.5, "rate": .15},
    "serious": {"pitch": 0, "rate": -.1},
    "playful": {"pitch": 1.25, "rate": .05},
    "sorry": {"pitch": -1.5, "rate": -.15},
}
_MARKER = re.compile(
    r"^\s*(?:\[([A-Za-z]+)\]|\(([A-Za-z]+)\)|\*([A-Za-z]+)\*)\s*"
)
_SAMPLE_SENTENCES = (
    (
        ("warm", "The rain should ease by late afternoon."),
        ("calm", "We can wait ten minutes and take the quieter path."),
    ),
    (
        ("excited", "The test suite passed on the first run!"),
        ("playful", "Even the suspicious edge case behaved itself."),
    ),
    (
        ("serious", "The backup did not finish before the deadline."),
        ("sorry", "I should have flagged the timeout sooner."),
        ("calm", "The original files are still intact."),
    ),
    (
        ("playful", "That tiny robot has excellent timing."),
        ("warm", "It waited by the door until everyone arrived."),
        ("excited", "Then it led the parade."),
    ),
    (
        ("calm", "Start with the smaller pan."),
        ("serious", "Keep the handle turned away from the edge."),
        ("warm", "Dinner will come together just fine."),
    ),
)


def strip(sentence: str) -> tuple[str | None, str]:
    """Remove one leading marker and return its recognized tag, if any."""

    if not isinstance(sentence, str):
        raise ValueError("Sentence must be text.")
    match = _MARKER.match(sentence)
    if not match:
        return None, sentence.strip()
    word = next(group for group in match.groups() if group is not None).lower()
    tag = word if word in TAGS else None
    return tag, sentence[match.end():].strip()


def adjust(options: dict, tag: str | None) -> dict:
    """Return copied voice options with a small, range-safe emotion nudge."""

    if not isinstance(options, dict):
        raise ValueError("Voice options must be a dictionary.")
    result = dict(options)
    if tag is None:
        return result
    if not isinstance(tag, str) or tag.lower() not in _DELTAS:
        return result
    delta = _DELTAS[tag.lower()]
    pitch = _finite_number(options.get("pitch", 0), "pitch") + delta["pitch"]
    rate = _finite_number(options.get("rate", 1), "rate") + delta["rate"]
    result["pitch"] = _clamp_round(pitch, *PITCH_RANGE)
    result["rate"] = _clamp_round(rate, *RATE_RANGE)
    return result


def plan_samples() -> dict:
    """Return the complete render plan without importing or loading TTS."""

    samples = []
    for index, source in enumerate(_SAMPLE_SENTENCES, start=1):
        sample_id = f"reply-{index}"
        sentences = []
        for tag, text in source:
            sentences.append({
                "text": text,
                "tag": tag,
                "options": {
                    "untagged": dict(BASELINE_OPTIONS),
                    "tagged": adjust(BASELINE_OPTIONS, tag),
                },
                "durations": {"untagged": None, "tagged": None},
            })
        samples.append({
            "id": sample_id,
            "tagged_text": " ".join(f"[{row['tag']}] {row['text']}" for row in sentences),
            "untagged_text": " ".join(row["text"] for row in sentences),
            "tags": [row["tag"] for row in sentences],
            "sentences": sentences,
            "audio": {
                "untagged": {
                    "wav": f"{sample_id}-untagged.wav",
                    "ogg": f"{sample_id}-untagged.ogg",
                },
                "tagged": {
                    "wav": f"{sample_id}-tagged.wav",
                    "ogg": f"{sample_id}-tagged.ogg",
                },
            },
            "durations": {"untagged": None, "tagged": None},
        })
    return {
        "version": 1,
        "voice": VOICE,
        "baseline_options": dict(BASELINE_OPTIONS),
        "samples": samples,
    }


def render_samples(out_dir: Path, page_path: Path, voice_dir: Path) -> dict:
    """Render WAV and Opus evidence, write its manifest, and build the page."""

    os.environ.setdefault("HF_HOME", str(voice_dir))
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        import torch
        from pocket_tts import TTSModel
    except ImportError as exc:
        raise RuntimeError(f"Pocket TTS import failed: {type(exc).__name__}: {exc}") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    page_path.parent.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)
    model = TTSModel.load_model()
    voice_path = voice_dir / f"{VOICE}.safetensors"
    if not voice_path.is_file():
        raise RuntimeError(f"Voice state not found: {voice_path}")
    state = model.get_state_for_audio_prompt(str(voice_path))
    sample_rate = int(model.sample_rate)
    manifest = plan_samples()
    manifest["sample_rate"] = sample_rate

    for sample in manifest["samples"]:
        rendered = {"untagged": [], "tagged": []}
        for sentence in sample["sentences"]:
            raw = b"".join(
                chunk.detach().cpu().numpy().astype("<f4").tobytes()
                for chunk in model.generate_audio_stream(state, sentence["text"])
            )
            for variant in ("untagged", "tagged"):
                pcm = process(raw, sample_rate, sentence["options"][variant])
                sentence["durations"][variant] = _duration(pcm, sample_rate)
                rendered[variant].append(pcm)
        silence = b"\0" * (round(sample_rate * SILENCE_SECONDS) * 4)
        for variant in ("untagged", "tagged"):
            combined = silence.join(rendered[variant])
            wav_path = out_dir / sample["audio"][variant]["wav"]
            ogg_path = out_dir / sample["audio"][variant]["ogg"]
            _write_wav(wav_path, combined, sample_rate)
            _encode_opus(wav_path, ogg_path)
            sample["durations"][variant] = _duration(combined, sample_rate)

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _write_feedback_page(page_path, manifest, out_dir)
    return manifest


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Unsupported voice {name}.")
    return float(value)


def _clamp_round(value: float, lower: float, upper: float) -> int | float:
    rounded = round(min(upper, max(lower, value)), 2)
    return int(rounded) if rounded.is_integer() else rounded


def _duration(pcm: bytes, sample_rate: int) -> float:
    return round(len(pcm) / 4 / sample_rate, 3)


def _write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    floats = array.array("f")
    floats.frombytes(pcm)
    if sys.byteorder != "little":
        floats.byteswap()
    samples = array.array("h", (
        max(-32768, min(32767, round((value if math.isfinite(value) else 0) * 32767)))
        for value in floats
    ))
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples.tobytes())


def _encode_opus(wav_path: Path, ogg_path: Path) -> None:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav_path),
        "-c:a", "libopus", "-b:a", "48k", str(ogg_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True)
    except FileNotFoundError:
        raise RuntimeError("FFmpeg was not found.") from None
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Opus encoding failed: {detail}") from exc


def _write_feedback_page(page_path: Path, manifest: dict, out_dir: Path) -> None:
    cards = []
    sample_ids = []
    for number, sample in enumerate(manifest["samples"], start=1):
        sample_ids.append(sample["id"])
        tagged_text = " ".join(
            f'<span class="tag">[{html.escape(row["tag"])}]</span> {html.escape(row["text"])}'
            for row in sample["sentences"]
        )
        sources = {}
        for variant in ("untagged", "tagged"):
            ogg_path = out_dir / sample["audio"][variant]["ogg"]
            sources[variant] = base64.b64encode(ogg_path.read_bytes()).decode("ascii")
        cards.append(f"""
      <section class="sample" data-sample="{sample['id']}">
        <h2>Reply {number}</h2>
        <p class="reply">{tagged_text}</p>
        <div class="players">
          <div>
            <h3>A: Untagged</h3>
            <audio controls preload="metadata" src="data:audio/ogg;base64,{sources['untagged']}"></audio>
          </div>
          <div>
            <h3>B: Tagged</h3>
            <audio controls preload="metadata" src="data:audio/ogg;base64,{sources['tagged']}"></audio>
          </div>
        </div>
        <fieldset>
          <legend>Which sounds better?</legend>
          <label><input type="radio" name="choice-{sample['id']}" value="untagged"> untagged</label>
          <label><input type="radio" name="choice-{sample['id']}" value="tagged"> tagged</label>
          <label><input type="radio" name="choice-{sample['id']}" value="neither"> neither</label>
        </fieldset>
        <label class="note">Note <input type="text" data-note="{sample['id']}" maxlength="240" placeholder="What stood out?"></label>
      </section>""")

    document = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Milo emotion tags: listening feedback</title>
  <style>
    :root { color-scheme: dark; font-family: ui-sans-serif, system-ui, sans-serif; background: #101214; color: #edf0f2; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #101214; }
    main { width: min(960px, calc(100% - 32px)); margin: 40px auto 80px; }
    h1 { margin: 0 0 8px; font-size: clamp(1.75rem, 4vw, 2.5rem); }
    .intro { color: #b9c0c7; margin: 0 0 28px; max-width: 72ch; }
    .sample { border-top: 1px solid #3d454c; padding: 26px 0 30px; }
    h2 { margin: 0 0 10px; font-size: 1.15rem; }
    h3 { margin: 0 0 8px; color: #c7cdd2; font-size: .8rem; letter-spacing: .08em; text-transform: uppercase; }
    .reply { font-size: 1.05rem; line-height: 1.7; margin: 0 0 18px; }
    .tag { color: #7bd7ff; font-weight: 700; }
    .players { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; margin-bottom: 18px; }
    audio { width: 100%; height: 40px; }
    fieldset { border: 0; padding: 0; margin: 0 0 14px; display: flex; flex-wrap: wrap; gap: 10px 22px; }
    legend { color: #b9c0c7; padding: 0 0 8px; }
    fieldset label { cursor: pointer; }
    input[type="radio"] { accent-color: #49bce9; }
    .note { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: 10px; color: #b9c0c7; }
    input[type="text"], textarea { border: 1px solid #4b555d; background: #181c20; color: #edf0f2; padding: 10px; font: inherit; }
    .feedback { border-top: 2px solid #69747d; padding-top: 26px; }
    button { border: 1px solid #69cef4; background: #153b4b; color: #f5fbfe; padding: 10px 16px; font: inherit; font-weight: 700; cursor: pointer; }
    #status { color: #8bd69c; min-height: 1.5em; display: inline-block; margin-left: 10px; }
    textarea { width: 100%; min-height: 220px; margin-top: 12px; resize: vertical; font-family: ui-monospace, monospace; font-size: .82rem; line-height: 1.45; }
    @media (max-width: 650px) { .players { grid-template-columns: 1fr; gap: 16px; } main { width: min(100% - 20px, 960px); margin-top: 24px; } }
  </style>
</head>
<body>
  <main>
    <h1>Milo emotion tags</h1>
    <p class="intro">Listen to both versions of each reply. A uses the current voice settings throughout. B applies the highlighted tag to each sentence. Choose the better version and leave a short note when useful.</p>
    __CARDS__
    <section class="feedback">
      <button type="button" id="copy">Copy feedback</button><span id="status" role="status"></span>
      <textarea id="mirror" aria-label="Feedback JSON" readonly></textarea>
    </section>
  </main>
  <script>
    const sampleIds = __SAMPLE_IDS__;
    const mirror = document.querySelector('#mirror');
    const status = document.querySelector('#status');
    function collect() {
      const feedback = sampleIds.map(id => {
        const selected = document.querySelector(`input[name="choice-${id}"]:checked`);
        const note = document.querySelector(`[data-note="${id}"]`);
        return { id, choice: selected ? selected.value : null, note: note.value.trim() };
      });
      mirror.value = JSON.stringify({ version: 1, feedback }, null, 2);
      return mirror.value;
    }
    async function copyFeedback() {
      const text = collect();
      try {
        if (!navigator.clipboard) throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(text);
        status.textContent = 'Copied.';
      } catch (error) {
        mirror.focus();
        mirror.select();
        document.execCommand('copy');
        status.textContent = 'Feedback selected. Copy it manually if needed.';
      }
    }
    document.querySelectorAll('input').forEach(input => input.addEventListener('input', collect));
    document.querySelector('#copy').addEventListener('click', copyFeedback);
    collect();
  </script>
</body>
</html>
"""
    document = document.replace("__CARDS__", "\n".join(cards))
    document = document.replace("__SAMPLE_IDS__", json.dumps(sample_ids))
    encoded = document.encode("utf-8")
    if len(encoded) > 4 * 1024 * 1024:
        raise RuntimeError("Feedback page exceeds the 4 MB hard cap.")
    if len(encoded) > 3 * 1024 * 1024:
        raise RuntimeError("Feedback page exceeds the 3 MB target.")
    page_path.write_bytes(encoded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="Render the five listening comparisons.")
    render.add_argument("--out", type=Path, required=True)
    render.add_argument(
        "--page", type=Path,
        default=Path(__file__).resolve().parents[2] / "docs/design/milo-emotion-tags.html",
    )
    render.add_argument(
        "--voice-dir", type=Path,
        default=Path(os.environ.get("MILO_VOICE_DIR", str(Path.home() / ".cache/tmp/milo-models"))),
    )
    args = parser.parse_args(argv)
    if args.command == "render":
        manifest = render_samples(args.out, args.page, args.voice_dir)
        print(json.dumps({
            "samples": len(manifest["samples"]),
            "out": str(args.out),
            "page": str(args.page),
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
