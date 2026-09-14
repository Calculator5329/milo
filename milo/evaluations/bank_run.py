"""Run the question bank through the demo server silently and keep every text event.

    python3 -m evaluations.bank_run --label before          # writes evidence/bank-<label>.jsonl
    python3 -m evaluations.bank_run --label after --only command,link
    python3 -m evaluations.bank_run --label x --ids fact-01,fact-02

Each turn posts silent:true, so nothing is spoken and the ledger row carries app eval. Results
are appended one JSON object per line as they arrive, so a stopped run resumes with --resume.
"""
import argparse
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
BANK = HERE / 'bank.jsonl'
EVIDENCE = HERE.parent / 'evidence'
URL = 'http://127.0.0.1:8776/api/demo/turn'
HEADERS = {'Content-Type': 'application/json', 'Origin': 'http://127.0.0.1:8776',
           'Referer': 'http://127.0.0.1:8776/overlay'}


def load_bank(path=BANK):
    rows = []
    with Path(path).open(encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def post_turn(text, messages, url=URL, timeout=240):
    payload = {'id': uuid.uuid4().hex, 'text': text, 'messages': messages, 'model': 'gemma4:12b',
               'voice': 'peter_yearsley', 'pitch': 3, 'rate': 1.65, 'sound': 'natural',
               'mode': 'conversational', 'silent': True}
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=HEADERS, method='POST')
    out = {'router': None, 'sources': [], 'source_label': None, 'thoughts': [], 'sentences': [],
           'captions': [], 'actions': [], 'metrics': {}, 'errors': [], 'other': []}
    start = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            if not raw.strip():
                continue
            event = json.loads(raw)
            kind = event.get('type')
            if kind == 'router':
                out['router'] = event.get('plan')
            elif kind == 'sources':
                out['sources'] += [{'title': s.get('title'), 'url': s.get('url')} for s in (event.get('sources') or [])]
                out['source_label'] = event.get('label') or out['source_label']
            elif kind == 'thought':
                out['thoughts'].append({k: v for k, v in event.items() if k not in ('type', 'id')})
            elif kind == 'sentence':
                out['sentences'].append(event.get('text', ''))
            elif kind == 'caption':
                out['captions'].append(event.get('text'))
            elif kind in ('tool', 'action', 'api_proposal', 'document_context'):
                out['actions'].append({k: v for k, v in event.items() if k != 'id'})
            elif kind == 'done':
                out['metrics'] = event.get('metrics') or {}
            elif kind in ('error', 'search_failed'):
                out['errors'].append(event.get('message', kind))
            elif kind not in ('sentence_end', 'transcript', 'audio'):
                out['other'].append(kind)
    out['answer'] = ' '.join(s for s in out['sentences'] if s)
    out['elapsed_ms'] = round((time.monotonic() - start) * 1000)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True)
    parser.add_argument('--bank', type=Path, default=BANK)
    parser.add_argument('--only', default='', help='comma separated categories')
    parser.add_argument('--ids', default='', help='comma separated ids')
    parser.add_argument('--url', default=URL)
    parser.add_argument('--resume', action='store_true', help='skip ids already in the output file')
    args = parser.parse_args(argv)
    rows = load_bank(args.bank)
    cats = {c for c in args.only.split(',') if c}
    ids = {i for i in args.ids.split(',') if i}
    EVIDENCE.mkdir(exist_ok=True)
    out_path = EVIDENCE / f'bank-{args.label}.jsonl'
    done = {}
    if args.resume and out_path.exists():
        with out_path.open(encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    done[row['id']] = row
    by_id = {row['id']: row for row in rows}
    with out_path.open('a', encoding='utf-8') as handle:
        for row in rows:
            if cats and row['cat'] not in cats:
                continue
            if ids and row['id'] not in ids:
                continue
            if row['id'] in done:
                continue
            messages = []
            prior = done.get(row.get('after'))
            if row.get('after') and prior is None:
                # The first half of a pair always precedes the second in bank order.
                prior_row = by_id.get(row['after'])
                prior = done.get(row['after']) if prior_row else None
            if prior:
                messages = [{'role': 'user', 'content': prior['q']},
                            {'role': 'assistant', 'content': prior['answer']}]
            try:
                result = post_turn(row['q'], messages, url=args.url)
            except Exception as error:
                result = {'router': None, 'sources': [], 'thoughts': [], 'sentences': [], 'answer': '',
                          'metrics': {}, 'errors': [f'request failed: {error}'], 'elapsed_ms': None}
            record = {**row, **result, 'history': messages}
            done[row['id']] = record
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
            handle.flush()
            plan = record.get('router') or {}
            print(f"{row['id']:<14} {plan.get('route') or '-':<10} {record['elapsed_ms'] or '-':>6}  {record['answer'][:90]}",
                  flush=True)
    print('wrote', out_path, file=sys.stderr)


if __name__ == '__main__':
    main()
