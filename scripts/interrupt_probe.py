"""Interrupt probe: how long Milo keeps sending audio after you cut it off.

    python milo.py --port 8767                     start a test server first
    python scripts/interrupt_probe.py --turns 6 --label mine

Each turn asks for a long spoken answer, waits until audio is streaming, holds for a short
delay so the cut lands mid-sentence, then posts /api/cancel exactly as the page's Stop button
does. It records, in milliseconds from the cancel request:

    cancel_ack_ms          the cancel request returned
    last_audio_after_ms    the last audio chunk that still arrived (0 when none did)
    stream_closed_ms       the server closed the turn stream

It also counts audio chunks that arrived after the cancel and their playable length. The page
stops its own playback the moment Stop is pressed, so chunks after the cancel are dropped by
the page. This probe measures the server side: how fast generation and synthesis stop.
Stdlib only; never opens a microphone. Results go to evidence/local/.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import statistics
import threading
import time
import urllib.request
from pathlib import Path

BASE = os.environ.get('MILO_URL', 'http://127.0.0.1:8767')
PROMPT = ('Tell me a slow, detailed story about a lighthouse keeper and a lost ship. '
          'Use at least eight sentences.')


def post(path, body, timeout=120):
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
    return urllib.request.urlopen(request, timeout=timeout)


def pcm_seconds(event):
    """Playable length of one audio event, from its base64 float32 payload and sample rate."""
    data = event.get('pcm') or event.get('audio') or event.get('data') or ''
    rate = event.get('sample_rate') or event.get('rate') or 24000
    try:
        return len(base64.b64decode(data)) / 4 / rate  # float32 little-endian samples
    except (ValueError, TypeError):
        return 0.0


def one_turn(index, hold_ms, model, voice):
    turn_id = f'interrupt-{index}-{int(time.time() * 1000)}'
    events, first_audio = [], threading.Event()
    state = {'closed': None}

    def read():
        with post('/api/turn', {'id': turn_id, 'text': PROMPT, 'model': model, 'voice': voice}) as response:
            for line in response:
                if not line.strip():
                    continue
                event = json.loads(line)
                events.append((time.monotonic(), event))
                if event.get('type') == 'audio':
                    first_audio.set()
        state['closed'] = time.monotonic()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    if not first_audio.wait(60):
        raise RuntimeError('no audio within 60 s')
    time.sleep(hold_ms / 1000)
    cancelled_at = time.monotonic()
    with post('/api/cancel', {'id': turn_id}, timeout=10) as response:
        acknowledged = json.load(response)
    ack_at = time.monotonic()
    reader.join(60)
    after = [(t, e) for t, e in events if t >= cancelled_at and e.get('type') == 'audio']
    before = [e for t, e in events if t < cancelled_at and e.get('type') == 'audio']
    ms = lambda t: round((t - cancelled_at) * 1000)
    return {
        'id': turn_id,
        'cancel_response': acknowledged,
        'cancel_ack_ms': ms(ack_at),
        'last_audio_after_ms': ms(after[-1][0]) if after else 0,
        'stream_closed_ms': ms(state['closed']) if state['closed'] else None,
        'audio_chunks_before': len(before),
        'audio_chunks_after': len(after),
        'audio_seconds_after': round(sum(pcm_seconds(e) for _, e in after), 3),
        'event_types': sorted({e.get('type') for _, e in events}),
        'done_event': any(e.get('type') == 'done' for _, e in events),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--turns', type=int, default=6)
    parser.add_argument('--hold-ms', type=int, default=1200, help='wait after first audio before cancelling')
    parser.add_argument('--label', default='interrupt')
    parser.add_argument('--model', default=None)
    parser.add_argument('--voice', default='marius')
    args = parser.parse_args()
    with urllib.request.urlopen(BASE + '/api/health', timeout=5) as response:
        health = json.load(response)
    assert health.get('ready'), health
    model = args.model or health['model']
    runs = []
    for index in range(args.turns):
        row = one_turn(index, args.hold_ms, model, args.voice)
        runs.append(row)
        print(index, {k: row[k] for k in ('cancel_ack_ms', 'last_audio_after_ms', 'stream_closed_ms',
                                          'audio_chunks_after', 'audio_seconds_after')}, flush=True)
        time.sleep(1)
    summary = {}
    for key in ('cancel_ack_ms', 'last_audio_after_ms', 'stream_closed_ms', 'audio_chunks_after', 'audio_seconds_after'):
        values = [r[key] for r in runs if isinstance(r[key], (int, float))]
        if values:
            summary[key] = {'n': len(values), 'p50': statistics.median(values), 'max': max(values)}
    stamp = datetime.datetime.now().astimezone()
    folder = Path(__file__).resolve().parents[1] / 'evidence' / 'local'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'interrupt-{stamp:%Y%m%d}-{args.label}.json'
    path.write_text(json.dumps({'label': args.label, 'recorded_at': stamp.isoformat(timespec='seconds'),
                                'url': BASE, 'model': model, 'voice': args.voice, 'hold_ms': args.hold_ms,
                                'units': 'milliseconds from the /api/cancel request',
                                'summary': summary, 'runs': runs}, indent=2) + '\n')
    for key, row in summary.items():
        print(f'  {key:22} p50 {row["p50"]:>8}  max {row["max"]:>8}')
    print('wrote', path)


if __name__ == '__main__':
    main()
