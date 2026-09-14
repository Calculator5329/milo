"""Partial transcription and speculative lookup, without listeners or live models."""
import base64
import io
import json
import pathlib
import subprocess
import sys
import threading
import time
import unittest
import wave
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import server
from demo_server import DemoEngine, demo_handler
try:
    from tests.test_tools_wiring import engine_without_init
except ModuleNotFoundError:  # unittest discover -s tests imports test modules top-level
    from test_tools_wiring import engine_without_init


def wav_audio():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\0' * 6400)
    return base64.b64encode(buffer.getvalue()).decode()


def demo_engine():
    engine = DemoEngine.__new__(DemoEngine)
    engine.turn_lock = threading.Lock()
    engine.turns = {}
    engine.prefetch = {}
    engine.partial_lock = threading.Lock()
    engine.ready = threading.Event()
    engine.ready.set()
    engine.transcribe = mock.Mock(return_value='What is gravity?')
    engine.router = mock.Mock()
    engine.router.decide.return_value = {'route': 'library', 'query': 'gravity', 'band': 'lookup'}
    engine.lookup = mock.Mock(return_value=([], None))
    engine.generate_text = mock.Mock(side_effect=AssertionError('Partial invoked model'))
    return engine


def post(engine, data, path='/api/demo/partial'):
    Handler = demo_handler(engine, 8776)
    handler = Handler.__new__(Handler)
    body = json.dumps(data).encode()
    handler.path = path
    handler.connection = mock.Mock()
    handler.headers = {'Host': '127.0.0.1:8776', 'Origin': 'http://127.0.0.1:8776',
                       'Content-Type': 'application/json', 'Content-Length': str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler.reply = lambda code, value: (code, value)
    return handler.do_POST()


class MaterialTests(unittest.TestCase):
    def test_identical_ignores_case_and_punctuation(self):
        self.assertTrue(server.materially_same('What IS gravity?', 'what is gravity.'))

    def test_up_to_three_trailing_words(self):
        self.assertTrue(server.materially_same('Explain gravity', 'Explain gravity to a child'))
        self.assertFalse(server.materially_same('Explain gravity', 'Explain gravity to a very young child'))

    def test_ratio_accepts_small_corrections(self):
        self.assertTrue(server.materially_same('Tell me about the planets', 'Tell me about planets'))

    def test_different_subject_and_empty_are_not_matches(self):
        self.assertFalse(server.materially_same('Explain gravity', 'Explain photosynthesis'))
        self.assertFalse(server.materially_same('', 'hello'))
        self.assertFalse(server.materially_same('...', '!!!'))


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine_without_init()
        self.engine.prefetch = {}
        self.engine.recall_memory = lambda *args: ''
        self.engine.generate_text = lambda turn, messages: (turn.put(('sentence', 'An answer.')), turn.put(('end', None)))
        self.engine.lookup = mock.Mock(return_value=([], None))
        self.text = 'look up gravity'
        self.plan = self.engine.router.decide(self.text, False)
        self.sources = [{'id': 1, 'title': 'Gravity', 'url': 'https://example.test/gravity',
                         'excerpt': 'Gravity attracts matter.', 'origin': 'library'}]

    def entry(self, **changes):
        done = threading.Event(); done.set()
        entry = {'text': self.text, 'plan': self.plan, 'sources': self.sources, 'failure': None,
                 'started': time.monotonic(), 'done': done, 'lookup_ms': 37,
                 'events': [{'type': 'searching', 'query': self.plan['query'], 'origin': 'library'},
                            {'type': 'sources', 'query': self.plan['query'], 'origin': 'library', 'sources': self.sources}]}
        entry.update(changes)
        self.engine.prefetch['recording'] = entry
        return entry

    def stream(self, text=None):
        turn = server.Turn('recording'); self.engine.register(turn); events = []
        self.engine.stream(turn, text or self.text, None, [], events.append)
        return turn, events

    def test_hit_replays_sources_without_another_lookup_and_consumes_once(self):
        self.entry()
        with mock.patch.object(self.engine.router, 'decide', wraps=self.engine.router.decide) as decide:
            turn, events = self.stream()
        decide.assert_called_once_with(self.text, False, [])
        self.engine.lookup.assert_not_called()
        self.assertEqual(turn.metrics['prefetch'], 'hit')
        self.assertEqual(turn.metrics['prefetch_saved_ms'], 37)
        self.assertEqual(next(e['sources'] for e in events if e['type'] == 'sources'), self.sources)
        receipt = next(e['plan'] for e in events if e['type'] == 'router')
        self.assertEqual((receipt['route'], receipt['query']), (self.plan['route'], self.plan['query']))
        self.assertNotIn('recording', self.engine.prefetch)
        turn, _ = self.stream()
        self.assertEqual(turn.metrics['prefetch'], 'miss')
        self.engine.lookup.assert_called_once()

    def test_stale_text_runs_a_fresh_lookup(self):
        self.entry(text='Explain photosynthesis')
        turn, _ = self.stream()
        self.assertEqual(turn.metrics['prefetch'], 'stale')
        self.engine.lookup.assert_called_once()
        self.assertNotIn('recording', self.engine.prefetch)

    def test_changed_route_or_query_runs_a_fresh_lookup(self):
        for plan in (dict(self.plan, route='web'), dict(self.plan, query='other')):
            with self.subTest(plan=plan):
                self.engine.lookup.reset_mock(); self.entry(plan=plan)
                turn, _ = self.stream()
                self.assertEqual(turn.metrics['prefetch'], 'stale')
                self.engine.lookup.assert_called_once()

    def test_expired_and_unfinished_entries_are_consumed_without_waiting(self):
        for changes in ({'started': time.monotonic() - 31}, {'done': threading.Event()}):
            with self.subTest(changes=changes):
                self.engine.lookup.reset_mock(); self.entry(**changes)
                turn, _ = self.stream()
                self.assertIn(turn.metrics['prefetch'], ('stale', 'miss'))
                self.engine.lookup.assert_called_once()
                self.assertNotIn('recording', self.engine.prefetch)


class PartialTests(unittest.TestCase):
    def test_endpoint_transcribes_and_prefetches_without_generation(self):
        engine = demo_engine()
        code, result = post(engine, {'id': 'recording', 'audio': wav_audio()})
        self.assertEqual(code, 200)
        self.assertEqual(result['text'], 'What is gravity?')
        self.assertGreaterEqual(result['ms'], 0)
        entry = engine.prefetch['recording']
        self.assertTrue(entry['done'].wait(2))
        engine.router.decide.assert_called_once_with(result['text'], False)
        engine.lookup.assert_called_once()
        self.assertEqual(engine.lookup.call_args.kwargs['plan']['route'], 'library')
        engine.generate_text.assert_not_called()

    def test_endpoint_busy_skips_whisper(self):
        engine = demo_engine()
        with engine.partial_lock:
            code, result = post(engine, {'id': 'recording', 'audio': wav_audio()})
        self.assertEqual((code, result), (200, {'text': None, 'busy': True}))
        engine.transcribe.assert_not_called()

    def test_endpoint_rejects_garbage_wav(self):
        engine = demo_engine()
        for audio in (base64.b64encode(b'garbage').decode(), '???', wav_audio()[:-20]):
            with self.subTest(audio=audio[:12]):
                self.assertEqual(post(engine, {'id': 'bad', 'audio': audio})[0], 400)
        engine.transcribe.assert_not_called()

    def test_endpoint_requires_audio(self):
        self.assertEqual(post(demo_engine(), {'id': 'bad', 'text': 'hello'})[0], 400)

    def test_bounded_cache_evicts_oldest_and_expired_entries(self):
        engine = demo_engine()
        for i in range(5):
            entry = engine.start_prefetch(str(i), 'What is gravity?')
            self.assertTrue(entry['done'].wait(2))
        self.assertEqual(list(engine.prefetch), ['1', '2', '3', '4'])
        engine.prefetch['3']['started'] -= 31
        entry = engine.start_prefetch('5', 'What is gravity?')
        self.assertTrue(entry['done'].wait(2))
        self.assertEqual(list(engine.prefetch), ['1', '2', '4', '5'])

    def test_superseded_worker_cannot_overwrite_new_partial(self):
        engine = demo_engine(); release = threading.Event(); entered = threading.Event()
        def lookup(turn, *args, **kwargs):
            if not entered.is_set():
                entered.set(); release.wait(2)
                return [{'title': 'old'}], None
            return [{'title': 'new'}], None
        engine.lookup = lookup
        old = engine.start_prefetch('same', 'old question')
        self.assertTrue(entered.wait(2))
        new = engine.start_prefetch('same', 'new question')
        try:
            self.assertTrue(new['done'].wait(2))
        finally:
            release.set()
        self.assertTrue(old['done'].wait(2))
        self.assertIs(engine.prefetch['same'], new)
        self.assertEqual(new['sources'], [{'title': 'new'}])


class ClientTests(unittest.TestCase):
    def test_recording_cadence_single_flight_abort_and_shared_id(self):
        script = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
const {MiloClient} = await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync('demo/client.js')).toString('base64'));
const timers=[]; globalThis.setTimeout=(fn,ms)=>{timers.push({fn,ms});return timers.length;};
globalThis.clearTimeout=()=>{};
const node=()=>({connect(){},disconnect(){},gain:{value:0},port:{}});
globalThis.AudioWorkletNode=class {constructor(){return node();}};
Object.defineProperty(globalThis,'navigator',{value:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}}});
let calls=[],pending=[];
globalThis.fetch=(url,options)=>{calls.push({url,...options});return new Promise(resolve=>pending.push(resolve));};
const events=[],client=new MiloClient(e=>events.push(e));
client.ctx={sampleRate:16000,currentTime:0,audioWorklet:{addModule:async()=>{}},createGain:node,createMediaStreamSource:node};
client.audio=async()=>{};
await client.record();
const feed=n=>client.capture.port.onmessage({data:new Float32Array(n)});
feed(23999);assert.equal(calls.length,0);
feed(1);assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/demo/partial');
const id=JSON.parse(calls[0].body).id;assert.ok(id);
feed(19200);assert.equal(calls.length,1);
pending.shift()({ok:true,json:async()=>({text:'partial words'})});
await new Promise(setImmediate);
assert.ok(events.some(e=>e.type==='partial'&&e.text==='partial words'));
feed(19199);assert.equal(calls.length,1);feed(1);assert.equal(calls.length,2);
let submitted;client.submit=(text,extra)=>{submitted=extra;};
client.finishRecording();assert.equal(submitted.id,id);assert.ok(submitted.audio);
assert.equal(calls[1].signal.aborted,true);
const partialCount=events.filter(e=>e.type==='partial').length;
pending.shift()({ok:true,json:async()=>({text:'late words'})});await new Promise(setImmediate);
assert.equal(events.filter(e=>e.type==='partial').length,partialCount);
assert.ok(timers.some(t=>t.ms===29000));
await client.record();feed(100);submitted=null;client.finishRecording();assert.equal(submitted,null);
await client.record();feed(24000);client.cancelRecording();assert.equal(calls.at(-1).signal.aborted,true);
// Exercise the real submit method too: streamed events must match the recording UUID.
const submittedEvents=[],next=new MiloClient(e=>submittedEvents.push(e));next.audio=async()=>{};
next.ctx={currentTime:0};
globalThis.fetch=async(url,options)=>{
 assert.equal(url,'/api/demo/turn');assert.equal(JSON.parse(options.body).id,id);
 return {ok:true,body:new ReadableStream({start(controller){
  controller.enqueue(new TextEncoder().encode(JSON.stringify({id,type:'transcript',text:'final words'})+'\n'+JSON.stringify({id,type:'done'})+'\n'));
  controller.close();
 }})};
};
await next.submit('',{id,audio:submitted?.audio||'encoded'});
assert.ok(submittedEvents.some(e=>e.type==='transcript'&&e.text==='final words'));
assert.ok(submittedEvents.some(e=>e.type==='done'));
"""
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
