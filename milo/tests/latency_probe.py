"""Repeatable stage-latency probe: N synthetic text and audio turns, p50/p95 per stage, JSON evidence.

Targets MILO_URL (default http://127.0.0.1:8767, a test server, never the live 8766 unit).
Uses the synthetic milo-hello.wav fixture from setup-voices; never opens a microphone.
    python milo.py probe --turns 8 --label baseline
"""
import argparse
import base64
import datetime
import io
import json
import os
import struct
import subprocess
import time
import urllib.request
import wave
from pathlib import Path

BASE = os.environ.get('MILO_URL', 'http://127.0.0.1:8767')
FIXTURE = Path(os.environ.get(
    'MILO_FIXTURE',
    str(Path(os.environ.get('MILO_VOICE_DIR', Path.home() / '.cache/tmp/milo-models')) / 'milo-hello.wav'),
))
STAGES = ['request_received', 'transcription_done', 'generation_started', 'first_token_ms', 'first_sentence_ready',
          'first_audio_chunk_sent', 'generation_done', 'last_audio_sent', 'client_first_audio_ms', 'client_done_ms']
PROMPTS = ['Say hello in one short sentence.',
           'I have 20 minutes. A report takes 15 minutes and needs a 5 minute booking done first. Can I also finish a 10 minute email?',
           'What is a good way to start a Saturday morning?',
           'Give me one reason to keep a notebook.',
           'Is it worth learning to touch type?',
           'What should I check before a long drive?']


def fixture_wav16k():
    """Read the float32 or PCM16 fixture without numpy and resample to mono 16 kHz PCM16."""
    raw = FIXTURE.read_bytes()
    assert raw[:4] == b'RIFF' and raw[8:12] == b'WAVE', FIXTURE
    at, fmt, data = 12, None, b''
    while at + 8 <= len(raw):
        tag, size = raw[at:at + 4], struct.unpack('<I', raw[at + 4:at + 8])[0]
        body = raw[at + 8:at + 8 + size]
        if tag == b'fmt ':
            fmt = struct.unpack('<HHIIHH', body[:16])
        elif tag == b'data':
            data = body
        at += 8 + size + (size & 1)
    kind, channels, rate, _, _, bits = fmt
    if kind == 3:
        samples = list(struct.unpack('<%df' % (len(data) // 4), data))
    else:
        samples = [x / 32768 for x in struct.unpack('<%dh' % (len(data) // 2), data)]
    samples = samples[::channels]
    ratio = rate / 16000
    out = []
    for i in range(int(len(samples) / ratio)):
        pos = i * ratio
        lo = int(pos)
        hi = min(lo + 1, len(samples) - 1)
        x = samples[lo] + (samples[hi] - samples[lo]) * (pos - lo)
        out.append(int(max(-1, min(1, x)) * 32767))
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(struct.pack('<%dh' % len(out), *out))
    return base64.b64encode(buf.getvalue()).decode()


def turn(payload):
    began = time.monotonic(); counts = {}; metrics = {}; sentences = []
    req = urllib.request.Request(BASE + '/api/turn', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as r:
        for line in r:
            row = json.loads(line); kind = row['type']; counts[kind] = counts.get(kind, 0) + 1
            assert kind != 'error', row
            if kind == 'sentence':
                sentences.append(row['text'])
            if kind == 'audio' and 'client_first_audio_ms' not in metrics:
                metrics['client_first_audio_ms'] = round((time.monotonic() - began) * 1000)
            if kind == 'done':
                metrics.update(row['metrics'])
                metrics['client_done_ms'] = round((time.monotonic() - began) * 1000)
    assert counts.get('done') == 1 and counts.get('audio', 0) > 0, counts
    return {'events': counts, 'sentences': sentences, 'metrics': metrics}


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo))


def summarize(runs):
    table = {}
    for stage in STAGES:
        values = [r['metrics'][stage] for r in runs if isinstance(r['metrics'].get(stage), (int, float))]
        if values:
            table[stage] = {'n': len(values), 'p50': percentile(values, .5), 'p95': percentile(values, .95),
                            'min': min(values), 'max': max(values)}
    return table


def shell(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception as exc:  # GPU tools are optional evidence, not a requirement.
        return 'unavailable: ' + type(exc).__name__


def gpu_state():
    try:
        ollama_url = os.environ.get('MILO_OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
        with urllib.request.urlopen(ollama_url + '/api/ps', timeout=5) as r:
            ps = json.load(r)
    except Exception as exc:
        ps = {'error': type(exc).__name__}
    for model in ps.get('models', []):
        if model.get('size'):
            model['vram_fraction'] = round(model.get('size_vram', 0) / model['size'], 3)
    return {'ollama_ps': ps,
            'nvidia_smi': shell(['nvidia-smi', '--query-gpu=name,memory.used,memory.total,utilization.gpu', '--format=csv,noheader']),
            'nvidia_smi_apps': shell(['nvidia-smi', '--query-compute-apps=pid,process_name,used_memory', '--format=csv,noheader'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--turns', type=int, default=6, help='turns per input kind (text and audio)')
    parser.add_argument('--label', default='baseline')
    parser.add_argument('--model', default=None, help='model id from /api/health models; default is the server default')
    parser.add_argument('--voice', default='marius')
    args = parser.parse_args()
    with urllib.request.urlopen(BASE + '/api/health', timeout=5) as r:
        health = json.load(r)
    assert health.get('ready'), health
    model = args.model or health['model']
    audio = fixture_wav16k()
    runs = {'text': [], 'audio': []}
    for i in range(args.turns):
        for kind in ('text', 'audio'):
            payload = {'id': f'latency-{kind}-{i}', 'model': model, 'voice': args.voice}
            if kind == 'text':
                payload['text'] = PROMPTS[i % len(PROMPTS)]
            else:
                payload['audio'] = audio
            result = turn(payload)
            runs[kind].append(result)
            print(kind, i, {k: result['metrics'].get(k) for k in ('transcription_done', 'first_token_ms', 'first_sentence_ready', 'first_audio_chunk_sent', 'client_done_ms')}, flush=True)
    stamp = datetime.datetime.now().astimezone()
    out = {'label': args.label, 'recorded_at': stamp.isoformat(timespec='seconds'), 'url': BASE, 'model': model,
           'voice': args.voice, 'turns_per_kind': args.turns, 'health': health, 'gpu': gpu_state(),
           'units': 'milliseconds from server turn start (handler entry); client_* are measured by this probe from request send',
           'summary': {kind: summarize(rows) for kind, rows in runs.items()}, 'runs': runs}
    path = Path(__file__).resolve().parents[1] / 'evidence' / f'latency-{stamp:%Y%m%d}-{args.label}.json'
    path.write_text(json.dumps(out, indent=2) + '\n')
    for kind, table in out['summary'].items():
        print(f'\n{kind} (n={args.turns})')
        for stage, row in table.items():
            print(f'  {stage:24} p50 {row["p50"]:>7}  p95 {row["p95"]:>7}')
    print('\nwrote', path)


if __name__ == '__main__':
    main()
