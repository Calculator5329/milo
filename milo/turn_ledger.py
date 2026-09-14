"""Append one JSON line per Milo turn so weak answers can be found and fixed later.

Text stays local (default ~/.local/state/milo/turns.jsonl, override MILO_TURN_LOG,
empty string disables). Review: ``python3 turn_ledger.py --last 20`` or ``--flag``.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

DEFAULT_PATH = Path.home() / '.local' / 'state' / 'milo' / 'turns.jsonl'
KEEP_OPTIONS = ('model', 'voice', 'mode', 'pitch', 'rate', 'sound', 'backend', 'web', 'document')
SLOW_FIRST_AUDIO_MS = 1500
# Refusals seen in the ledger when a factual ask was routed to chat and the model would not answer.
MISSED_LOOKUP_PHRASES = ("not allowed to guess", "outside knowledge", "don't have any information",
                         "don't have information", "no information about", "in my database",
                         "i'm not able to provide", "i cannot provide information", "unable to provide information")
DEFAULT = object()  # sentinel: use the configured path; pass None to disable


def ledger_path():
    raw = os.environ.get('MILO_TURN_LOG')
    if raw is None:
        return DEFAULT_PATH
    return Path(raw).expanduser() if raw.strip() else None


class TurnRecorder:
    """Wraps a stream ``send`` so the turn's text, sources and metrics are kept."""

    def __init__(self, turn, text, app, path=DEFAULT):
        self.turn, self.app = turn, app
        self.path = ledger_path() if path is DEFAULT else path
        self.row = {'at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'app': app,
                    'id': turn.id, 'question': text, 'input': 'text' if text else 'voice',
                    'options': {k: turn.options.get(k) for k in KEEP_OPTIONS if turn.options.get(k) not in (None, False, '')},
                    'route': None, 'router': None, 'lookup': None, 'sources': [], 'answer': [], 'events': [], 'metrics': {}}

    def wrap(self, send):
        def recording_send(event):
            self.note(event)
            send(event)
        return recording_send

    def note(self, event):
        kind = event.get('type')
        if kind == 'transcript':
            self.row['question'] = event.get('text')
        elif kind == 'route':
            self.row['route'] = event.get('plan')
        elif kind == 'router':
            self.row['router'] = event.get('plan')
        elif kind == 'searching':
            self.row['lookup'] = {'query': event.get('query'), 'origin': event.get('origin')}
        elif kind == 'sources':
            self.row['sources'] = [{'title': s.get('title'), 'book': s.get('book'), 'origin': s.get('origin')} for s in event.get('sources', [])]
        elif kind == 'sentence':
            self.row['answer'].append(event.get('text', ''))
        elif kind == 'done':
            self.row['metrics'] = dict(event.get('metrics') or {})
        elif kind in ('error', 'search_failed', 'action', 'draft', 'api_proposal', 'provider', 'tool'):
            self.row['events'].append({k: v for k, v in event.items() if k != 'id'})

    def close(self):
        self.row['cancelled'] = self.turn.cancelled.is_set() and not self.row['metrics']
        self.row['answer'] = ' '.join(part.strip() for part in self.row['answer'] if part).strip()
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(self.row, ensure_ascii=False) + '\n')
        except OSError:
            pass


def flags(row):
    """Heuristics for a turn worth a second look."""
    out = []
    metrics = row.get('metrics') or {}
    if row.get('cancelled'):
        out.append('cancelled')
    if not row.get('answer') and not row.get('events'):
        out.append('no answer')
    if metrics.get('generation_finish') == 'length':
        out.append('cut off by token budget')
    first_audio = metrics.get('first_audio_chunk_sent') or metrics.get('first_audio_ms')
    if first_audio and first_audio > SLOW_FIRST_AUDIO_MS:
        out.append(f'slow first audio {first_audio} ms')
    router = row.get('router') or {}
    automatic = router.get('route') in ('library', 'web') and router.get('band') != 'explicit'
    if row.get('lookup') and not row.get('sources'):
        out.append('auto lookup found nothing' if automatic else 'lookup found nothing')
    for event in row.get('events', []):
        if event.get('type') in ('error', 'search_failed'):
            out.append(event.get('message', event['type']))
        elif event.get('type') == 'action' and event.get('result', {}).get('status') == 'failed':
            out.append('action failed: ' + str(event['result'].get('detail')))
    answer = (row.get('answer') or '').lower()
    for phrase in ("i cannot access", "i can't access", "i don't have access", "no access", "real-time information"):
        if phrase in answer:
            out.append('denied access')
            break
    if router.get('route') == 'chat':
        for phrase in MISSED_LOOKUP_PHRASES:
            if phrase in answer:
                out.append('possibly missed lookup')
                break
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description='Review recent Milo turns.')
    parser.add_argument('--last', type=int, default=20)
    parser.add_argument('--flag', action='store_true', help='only turns with a flag')
    parser.add_argument('--path', type=Path, default=None)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    path = args.path or ledger_path()
    if path is None or not path.exists():
        print('no turns recorded yet' + (f' at {path}' if path else ''))
        return 0
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    rows = rows[-args.last:]
    for row in rows:
        marks = flags(row)
        if args.flag and not marks:
            continue
        if args.json:
            print(json.dumps({**row, 'flags': marks}, ensure_ascii=False))
            continue
        metrics = row.get('metrics') or {}
        first_audio = metrics.get('first_audio_chunk_sent') or metrics.get('first_audio_ms')
        head = f"{row['at'][11:19]} {row['app']} {row['options'].get('model', '?')}"
        if metrics.get('first_token_ms') is not None:
            head += f"  first token {metrics['first_token_ms']} ms, first audio {first_audio} ms"
        print(head)
        print('  Q:', row.get('question'))
        print('  A:', row.get('answer') or '(none)')
        if row.get('router'):
            r = row['router']
            print('  router:', r.get('route'), r.get('band'), '-', r.get('reason'), f"({r.get('ms')} ms)")
        if row.get('lookup'):
            print('  lookup:', row['lookup']['origin'], repr(row['lookup']['query']), '->', [s['title'] for s in row['sources']] or 'nothing')
        for event in row.get('events', []):
            print('  event:', json.dumps(event, ensure_ascii=False)[:200])
        if marks:
            print('  FLAG:', '; '.join(marks))
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
