#!/usr/bin/env python3
"""Milo v3 loopback server for the notebook UI, overlay API and voice engine."""
from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
from contextlib import nullcontext
from urllib.parse import parse_qs, urlsplit
from http.server import ThreadingHTTPServer

# Set cache configuration before libraries can snapshot their environment.
os.environ.setdefault('HF_HOME',os.environ.get('MILO_VOICE_DIR',str(Path.home()/'.cache/tmp/milo-models')))
os.environ['HF_HUB_OFFLINE']='1'

from server import Engine, Turn, handler_for, validate_payload, validate_options, ROOT, MAX_BODY, MODEL, MODELS, OLLAMA, THOUGHT_RULE, unfiltered_model
from server import PREFETCH_TTL
from demo_documents import DocumentWorkspace, DocumentError
from demo_intents import decide, api_proposal
from providers import Provider, ProviderError, status as provider_status
from careful import wants_careful, cleaned as careful_cleaned
from drafting import draft, DraftError
from preferences import Preferences, DEFAULTS
from voice_catalog import VOICE_PRESETS
from turn_ledger import TurnRecorder
from model_comparison import catalog as comparison_catalog, run as compare_model
from speech_audio import settings as voice_settings, stream as stream_voice
from conversation import PROFILES, REFERENCE_PREFIX, build_messages, ollama_model_options, split_spoken_sentence, _context_units
import hearing
import reminders

DEFAULT_ROOT=Path(os.environ.get('MILO_DOCUMENTS_DIR',str(Path.home()/'.local/share/milo/documents')))


def jarvis_preview(text):
    env=dict(os.environ,JARVIS_LLM='off')
    try:
        response=subprocess.run([str(Path.home()/'.local/bin/jarvis'),'--text',text,'--dry-run'],env=env,capture_output=True,text=True,timeout=6)
        data=json.loads(response.stdout[:40000])
        steps=data.get('steps',[])
        if not isinstance(steps,list):steps=[]
        return {'status':'preview','title':'Desktop command preview',
                'detail':' · '.join(str(step.get('say',step.get('error','Unresolved step'))) for step in steps) or 'The installed grammar did not resolve this command.',
                'steps':steps,'executed':False,'grammar':data.get('source','unknown')}
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return {'status':'unavailable','title':'Desktop preview unavailable','detail':'No compatible desktop command preview is available on this installation.','executed':False}


class DemoEngine(Engine):
    def __init__(self,voice_dir,documents,preferences=None):
        self.documents=documents
        self.preferences=preferences
        self.last_draft=None
        self.action_receipts=[]
        self.partial_lock=threading.Lock()
        self.prefetch={}
        super().__init__(voice_dir)

    def start_prefetch(self, turn_id, text):
        """Speculate on lookup only, keyed by the recording's final turn UUID.

        Each worker owns its entry, so an old partial cannot replace a newer one.
        Generation is deliberately deferred until the final transcript arrives.
        """
        started = time.monotonic()
        speculative_turn = Turn(turn_id)
        entry = {'text': text, 'plan': None, 'sources': [], 'failure': None,
                 'started': started, 'done': threading.Event(), 'turn': speculative_turn,
                 'events': [], 'metrics': {}, 'lookup_ms': 0}
        with self.turn_lock:
            for key, old in list(self.prefetch.items()):
                if key == turn_id or started - old['started'] > PREFETCH_TTL:
                    old['turn'].cancelled.set()
                    del self.prefetch[key]
            while len(self.prefetch) >= 4:
                old = self.prefetch.pop(next(iter(self.prefetch)))
                old['turn'].cancelled.set()
            self.prefetch[turn_id] = entry

        def run():
            try:
                plan = self.router.decide(text, False)
                entry['plan'] = plan
                if plan['query'] and not speculative_turn.cancelled.is_set():
                    before = time.monotonic()
                    entry['sources'], entry['failure'] = self.lookup(
                        speculative_turn, plan['query'], entry['events'].append,
                        plan=plan)
                    entry['metrics'] = dict(speculative_turn.metrics)
                    entry['lookup_ms'] = round((time.monotonic() - before) * 1000)
            except Exception:
                # Speculation must never replace a final turn's normal retry/error path.
                entry['error'] = True
            finally:
                entry['done'].set()

        threading.Thread(target=run, daemon=True).start()
        return entry

    def audio_chunks(self, turn, sentence):
        yield from stream_voice(super().audio_chunks(turn, sentence), self.tts.sample_rate,
                                turn.options, turn.cancelled.is_set)

    def stream_spoken(self,turn,sentence,send):
        """Speak one fixed sentence through the same serialized voice path as an answer."""
        acquired=False
        try:
            while not turn.cancelled.is_set():
                acquired=self.lock.acquire(timeout=.1)
                if acquired:break
            if not acquired or turn.cancelled.is_set():return
            send({'type':'sentence','index':0,'text':sentence})
            for pcm in self.audio_chunks(turn,sentence):
                if turn.cancelled.is_set():return
                send({'type':'audio','index':0,'sample_rate':self.tts.sample_rate,
                      'pcm':base64.b64encode(pcm).decode()})
                turn.mark('first_audio_chunk_sent')
                turn.metrics.setdefault('first_audio_ms',turn.metrics['first_audio_chunk_sent'])
                turn.mark('last_audio_sent',repeat=True)
            send({'type':'sentence_end','index':0})
            if not turn.cancelled.is_set():
                send({'type':'done','metrics':turn.metrics,'sentences':1})
        finally:
            if acquired:self.lock.release()

    def publish_draft(self,turn,proposal):
        with self.turn_lock:
            if turn.cancelled.is_set():return False
            self.last_draft=dict(proposal)
            return True

    def draft_state(self):
        with self.turn_lock:
            draft=self.last_draft
            return dict(draft) if draft and not draft['saved'] else None

    def mark_draft_saved(self,draft_id):
        with self.turn_lock:
            draft=self.last_draft
            if draft and draft_id==draft['id']:
                self.last_draft={**draft,'saved':True}

    def remember_action(self,result):
        receipt={key:result[key] for key in ('status','title','detail','executed') if key in result}
        if result.get('matches'):
            receipt['matches']=[{key:item[key] for key in ('name','line','excerpt')} for item in result['matches'][:4]]
            receipt['matches_truncated']=len(result['matches'])>4
        if result.get('document'):
            receipt['document']={key:result['document'][key] for key in ('name','revision')}
        with self.turn_lock:self.action_receipts=(self.action_receipts+[receipt])[-8:]

    def prepare_messages(self,turn,text,messages,sources):
        if turn.options.get('backend')=='api':
            return [{'role':'user','content':text}]
        with self.turn_lock:receipts=list(self.action_receipts)
        summary={}
        notes=self.memory_message(turn)
        reserve=_context_units([notes]) if notes else 0
        packed=build_messages(text,mode=turn.options.get('mode','conversational'),owner_name=os.environ.get('MILO_OWNER_NAME'),history=messages,
                              web_sources=sources,selected_document=turn.options.get('selected_document'),action_receipts=receipts,model=turn.options['model'],
                              num_ctx=4096-reserve,context_summary=summary,unfiltered=unfiltered_model(turn.options['model']))
        packed[0]['content'] = packed[0]['content'].replace(
            "Offline; 'look up ...' reads the library.",
            'Milo uses the web when asked, for live facts, and when the library has nothing useful.')
        if sources:
            label=self.source_label(turn,sources)
            for message in packed:
                if message['content'].startswith(REFERENCE_PREFIX):
                    message['content']=label+message['content'][len(REFERENCE_PREFIX):]
                    break
        packed[0]['content'] += '\n' + THOUGHT_RULE
        if notes:packed.insert(1,notes)
        references=summary['references']
        document=references.get('selected_document')
        selected=turn.options.get('selected_document')
        turn.options['context_summary']={
            'type':'context_summary',
            'document':{'name':selected['name'],'characters':len(document['content']) if document else 0,
                        'truncated':not document or document.get('truncated',False)} if selected else None,
            'history_omitted':summary['history_omitted'],
            'sources_included':len(references.get('web_sources',[])),
            'sources_omitted':max(0,len(sources)-len(references.get('web_sources',[]))),
        }
        return packed

    def model_options(self,turn):
        return ollama_model_options(turn.options['model'],turn.options.get('mode','conversational'))

    def sentence_split(self,buffer,final=False,first=False):
        return split_spoken_sentence(buffer,final=final,max_chars=110)

    def generate_text(self,turn,messages):
        if turn.options.get('fixed_reply'):
            turn.put(('sentence',turn.options['fixed_reply']))
            turn.put(('end',None))
            return
        if turn.options.get('backend')=='api':
            try:
                provider=Provider.configured()
                if provider is None:raise ProviderError('No API provider is configured.')
                # Fresh provider turn: never forward local history, document excerpts or search snippets.
                current=next(message['content'] for message in reversed(messages) if message['role']=='user')
                length='up to five short spoken sentences' if turn.options.get('careful') else 'one to three short spoken sentences'
                remote_messages=[{'role':'system','content':'You are Milo, a thoughtful conversational assistant. Answer the current question directly in '+length+'. Admit uncertainty. You have no tools, file access, or previous conversation. Do not claim actions or web lookup. '+THOUGHT_RULE}, {'role':'user','content':current}]
                buffer=''
                first=True
                for token in provider.stream(remote_messages,turn.cancelled):
                    if 'first_token_ms' not in turn.metrics:turn.metrics['first_token_ms']=turn.ms()
                    buffer+=token
                    buffer,first=self.queue_generated_text(turn,buffer,first=first)
                buffer,first=self.queue_generated_text(turn,buffer,final=True,first=first)
                if getattr(provider,'last_model',None):turn.metrics['provider_model']=provider.last_model
                turn.put(('end',None))
            except ProviderError as error:
                local=turn.options.get('careful_local')
                if local is None or 'first_token_ms' in turn.metrics:
                    turn.put(('error',str(error)));return
                turn.metrics['careful_fallback']=str(error)
                turn.options['backend']='local'
                super().generate_text(turn,self.prepare_messages(turn,local['text'],local['history'],[]))
            return
        super().generate_text(turn,messages)

    def stream(self,turn,text,wav,messages,send):
        delegated=False
        try:
            if turn.options.get('audition'):
                delegated=True
                return super().stream(turn,text,wav,messages,send)
            if wav is not None:
                start=time.monotonic()
                # The final waits for an already running partial. New partials skip.
                with getattr(self, 'partial_lock', nullcontext()):
                    text=self.transcribe(wav)
                turn.metrics['transcribe_ms']=round((time.monotonic()-start)*1000)
            if turn.cancelled.is_set():return
            heard=hearing.corrections(text)
            if heard:
                turn.metrics['heard']=[f'{was} -> {now}' for was,now in heard][:6]
                text=hearing.correct(text)
            send({'type':'transcript','text':text})
            reminder=reminders.handle(text)
            if reminder is not None:
                turn.metrics['reminder']=reminder.get('receipt')
                self.stream_spoken(turn,reminder['spoken'],send)
                return
            start=time.monotonic();plan=decide(text)
            if turn.options.get('web'):
                plan={'kind':'web','delivery':'spoken','reason':'Search web selected'}
            turn.metrics['route_ms']=round((time.monotonic()-start)*1000)
            send({'type':'route','plan':plan})
            selected=turn.options.get('document')
            if plan['kind'].startswith('document_') and 'name' in plan:
                selected=plan['name']
            if turn.cancelled.is_set():return
            if plan['delivery']=='silent':
                if plan['kind']=='document_draft':
                    # Drafts have no write operation; the UI offers a separate Apply action.
                    existing=next((item for item in self.documents.list_documents() if item['name']==selected),None)
                    context=self.documents.read(turn.options['document']) if turn.options.get('document') else None
                    # The HTTP request is already a worker. Drafting does not use shared
                    # TTS state, so it must not hold the speech lock during a network read.
                    if turn.cancelled.is_set():return
                    send({'type':'drafting','name':selected})
                    content=draft(plan['instruction'],model=turn.options['model'],ollama_url=OLLAMA,cancelled=turn.cancelled,context=context)
                    if content is None:return
                    proposal={'id':turn.id,'name':selected,'content':content,'revision':existing['revision'] if existing else None,'saved':False,'source':'local model'}
                    if not self.publish_draft(turn,proposal):return
                    send({'type':'draft','proposal':proposal})
                    send({'type':'done','metrics':turn.metrics,'sentences':0})
                    return
                elif plan['kind']=='document_search':
                    results=self.documents.search(plan['query'])
                    result={'status':'completed','title':'Notebook search','detail':str(len(results))+' matching notes','matches':results,'executed':True}
                elif plan['kind']=='document_create':
                    doc=self.documents.create(selected,plan['content']+'\n')
                    result={'status':'completed','title':'Note created','detail':selected,'document':doc,'executed':True}
                elif plan['kind']=='document_append':
                    before=self.documents.read(selected)
                    doc=self.documents.append(selected,'\n'+plan['content']+'\n',before['revision'])
                    result={'status':'completed','title':'Note updated','detail':selected,'document':doc,'executed':True}
                elif plan['kind']=='document_open':
                    doc=self.documents.read(selected)
                    result={'status':'completed','title':'Note opened','detail':selected,'document':doc,'executed':True}
                else:
                    result=jarvis_preview(text)
                self.remember_action(result)
                if turn.cancelled.is_set():
                    # A completed atomic write remains visible in the document list/history.
                    return
                send({'type':'action','result':result})
                send({'type':'done','metrics':turn.metrics,'sentences':0})
                return
            if turn.options.get('backend')!='api' and plan['delivery']!='silent' and os.environ.get('MILO_CAREFUL','1')!='0':
                reason=wants_careful(text)
                if reason and Provider.configured() is not None:
                    # A hard question goes to the stronger cloud model; the local answer stays
                    # ready as the fallback when the provider fails before its first token.
                    turn.options['backend']='api';turn.options['careful']=reason
                    turn.options['careful_local']={'text':text,'history':list(messages)}
                    turn.metrics['careful']=reason
                    text=careful_cleaned(text) or text
                    send({'type':'caption','text':'Asking the cloud model'})
                elif reason:
                    turn.metrics['careful_skipped']='no provider'
            if plan['kind']=='api_preview' and turn.options.get('backend')!='api':
                send({'type':'api_proposal','proposal':api_proposal(text,bool(selected))})
                turn.options['fixed_reply']='That is a candidate for a stronger model. Select an API connection in the notebook to use one. This turn stayed local, and no API request was sent.'
            if selected and turn.options.get('backend')!='api':
                doc=self.documents.read(selected)
                excerpt=doc['content'][:6000]
                turn.options['selected_document']={'name':selected,'content':excerpt,'revision':doc['revision'],'truncated':len(excerpt)<len(doc['content'])}
                send({'type':'document_context','stage':'loaded_before_context_budget','name':selected,'revision':doc['revision'],'characters':len(excerpt),'truncated':len(excerpt)<len(doc['content'])})
            if turn.options.get('backend')=='api':
                provider=Provider.configured()
                if provider is None:raise ProviderError('No API provider is configured. Choose Local.')
                # Explicit cloud selection does not run local web search or forward prior sources.
                turn.options['web']=False
                turn.options['sources']=[]
                send({'type':'provider','provider':provider.public()})
            delegated=True
            def forward(event):
                summary=turn.options.pop('context_summary',None)
                if summary:send(summary)
                if event['type']!='transcript':send(event)
            return super().stream(turn,text,None,messages,forward)
        except (DocumentError,ValueError) as error:
            # The base engine marks the turn cancelled on its way out, so the failure is
            # reported regardless; a superseded turn only reaches a closed pipe.
            send({'type':'action','result':{'status':'failed','title':'Nothing changed','detail':str(error),'executed':False}})
            send({'type':'done','metrics':turn.metrics,'sentences':0})
        finally:
            if not delegated:
                turn.cancelled.set();self.forget(turn)


def demo_handler(engine,port):
    Base=handler_for(engine,port)
    assets={'/model-lab.js':('model-lab.js','text/javascript'),'/preferences.js':('preferences.js','text/javascript'),'/file-transfer.js':('file-transfer.js','text/javascript'),'/voice-lab.js':('voice-lab.js','text/javascript'),'/voice-lab.css':('voice-lab.css','text/css'),'/themes.js':('themes.js','text/javascript'),'/themes.css':('themes.css','text/css'),'/demo':('index.html','text/html; charset=utf-8'),'/demo.js':('demo.js','text/javascript'),
            '/demo.css':('demo.css','text/css'),'/demo-client.js':('client.js','text/javascript'),
            '/robot.svg':('robot.svg','image/svg+xml'),'/overlay':('overlay.html','text/html; charset=utf-8'),
            '/overlay.js':('overlay.js','text/javascript'),'/overlay.css':('overlay.css','text/css')}

    class Handler(Base):
        def do_GET(self):
            parsed=urlsplit(self.path);path=parsed.path
            if path not in assets and path != '/voice-catalog.js' and not path.startswith('/api/demo/'):
                return super().do_GET()
            if not self.allowed():return self.reply(403,{'error':'Local demo only.'})
            try:
                if path == '/voice-catalog.js':
                    return self.reply(200, ('export const VOICE_PRESETS='+json.dumps(VOICE_PRESETS)+';').encode(), 'text/javascript')
                if path in assets:
                    name,mime=assets[path]
                    return self.reply(200,(ROOT/'demo'/name).read_bytes(),mime)
                query=parse_qs(parsed.query)
                name=query.get('name',[''])[0]
                if path=='/api/demo/preferences':
                    return self.reply(200,engine.preferences.get() if engine.preferences else {**DEFAULTS,'revision':None})
                if path=='/api/demo/comparison':
                    return self.reply(200,comparison_catalog(MODELS,OLLAMA))
                if path=='/api/demo/state':
                    return self.reply(200,{'documents':engine.documents.list_documents(),'ready':engine.ready.is_set(),
                                          'draft':engine.draft_state(),'provider':provider_status(),'error':engine.loading_error,'model':MODEL,'models':MODELS,'api_enabled':False,
                                          'web':'Asked, live facts, or library fallback','desktop_commands':'preview only','document_workspace':'Milo demo documents'})
                if path=='/api/demo/document':return self.reply(200,engine.documents.read(name))
                if path=='/api/demo/revisions':return self.reply(200,engine.documents.revisions(name))
                if path=='/api/demo/search-documents':
                    results=engine.documents.search(query.get('q',[''])[0])
                    engine.remember_action({'status':'completed','title':'Notebook search','detail':str(len(results))+' matching notes','matches':results,'executed':True})
                    return self.reply(200,results)
                return self.reply(404,{'error':'Not found.'})
            except (ValueError,OSError):
                return self.reply(400,{'error':'The requested demo document is unavailable or invalid.'})

        def do_POST(self):
            if not self.path.startswith('/api/demo/'):
                return super().do_POST()
            self.connection.settimeout(15)
            if not self.allowed() or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                self.close_connection=True
                return self.reply(403,{'error':'Use the local demo page.'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=MAX_BODY:
                    self.close_connection=True
                    return self.reply(413,{'error':'Request too large.'})
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):raise ValueError('Invalid request.')
                if self.path=='/api/demo/partial':
                    turn_id,text,wav,_=validate_payload(data)
                    if wav is None:raise ValueError('Microphone audio required.')
                    if not engine.ready.is_set():return self.reply(503,{'error':'Milo is warming up.'})
                    if not engine.partial_lock.acquire(blocking=False):
                        return self.reply(200,{'text':None,'busy':True})
                    try:
                        with engine.turn_lock:
                            final_started=turn_id in engine.turns
                        if final_started:return self.reply(200,{'text':None,'busy':True})
                        started=time.monotonic()
                        try:
                            text=engine.transcribe(wav)
                        except OSError:
                            return self.reply(503,{'error':'Transcription unavailable.'})
                        engine.start_prefetch(turn_id,text)
                        result={'text':text,'ms':round((time.monotonic()-started)*1000)}
                    finally:
                        engine.partial_lock.release()
                    return self.reply(200,result)
                say_only=False
                if self.path=='/api/demo/compare':
                    comparison_id,question,_,_=validate_payload({'id':data.get('id'),'text':data.get('question')})
                    selected_model=data.get('model')
                    selected_mode=data.get('mode','conversational')
                    if selected_model not in MODELS and selected_model!='api':raise ValueError('Unknown model.')
                    if selected_mode not in PROFILES:raise ValueError('Unknown style.')
                    turn=Turn(comparison_id,{})
                    engine.register(turn)
                    return self.reply(200,compare_model(engine,turn,question,selected_model,selected_mode,MODELS,OLLAMA))
                if self.path=='/api/demo/preferences':
                    if not engine.preferences:raise ValueError('Shared preferences unavailable.')
                    return self.reply(200,engine.preferences.update(data))
                if self.path=='/api/demo/route':
                    text=data.get('text')
                    if not isinstance(text,str) or not 0<len(text)<=2000:raise ValueError('Enter a short request.')
                    return self.reply(200,{'plan':decide(text),'proposal':api_proposal(text,bool(data.get('document')))})
                if self.path=='/api/demo/document':
                    operation=data.get('operation');name=data.get('name')
                    if operation=='create':result=engine.documents.create(name,data.get('content'))
                    elif operation=='replace':result=engine.documents.replace(name,data.get('content'),data.get('revision'))
                    elif operation=='append':result=engine.documents.append(name,data.get('content'),data.get('revision'))
                    elif operation=='restore':result=engine.documents.restore(name,data.get('restore_revision'),data.get('revision'))
                    else:raise ValueError('Unknown document operation.')
                    engine.remember_action({'status':'completed','title':'Note saved','detail':result['name'],'document':result,'executed':True})
                    engine.mark_draft_saved(data.get('draft_id'))
                    return self.reply(200,{'document':result,'delivery':'silent','status':'completed'})
                if self.path=='/api/demo/say':
                    turn_id,text,wav,messages=validate_payload({
                        'id':data.get('id') or secrets.token_hex(16), 'text':data.get('text'),
                    })
                    options=validate_options(data)
                    options.update(voice_settings(data))
                    say_only=True
                else:
                    if self.path!='/api/demo/turn':return self.reply(404,{'error':'Not found.'})
                    turn_id,text,wav,messages=validate_payload(data);options=validate_options(data)
                    document=data.get('document')
                    if document is not None:
                        engine.documents.read(document)
                    options['document']=document
                    backend=data.get('backend','local')
                    if backend not in ('local','api'):raise ValueError('Unknown backend.')
                    if backend=='api' and Provider.configured() is None:raise ValueError('API provider unavailable.')
                    options['backend']=backend
                    mode=data.get('mode','conversational')
                    if mode not in PROFILES:raise ValueError('Unknown conversation mode.')
                    options['mode']=mode
                    options.update(voice_settings(data))
            except (ValueError,TypeError,UnicodeError) as error:
                return self.reply(400,{'error':str(error) if isinstance(error,DocumentError) else 'Invalid demo request.'})
            if not engine.ready.is_set():return self.reply(503,{'error':'Milo is warming up.'})
            turn=Turn(turn_id,options);engine.register(turn)
            self.send_response(200);self.send_header('Content-Type','application/x-ndjson')
            self.send_header('Cache-Control','no-store');self.send_header('Connection','close');self.end_headers();self.close_connection=True
            recorder=None if say_only else TurnRecorder(turn,text,'eval' if options.get('silent') else 'corner')
            def raw_send(event):
                event['id']=turn.id;self.wfile.write(json.dumps(event).encode()+b'\n');self.wfile.flush()
            send=raw_send if recorder is None else recorder.wrap(raw_send)
            try:
                if say_only:engine.stream_spoken(turn,text,send)
                else:engine.stream(turn,text,wav,messages,send)
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception:
                try:send({'type':'error','message':'The demo request failed. Try again.'})
                except OSError:pass
            finally:
                turn.cancelled.set();engine.forget(turn)
                if recorder is not None:recorder.close()
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=int(os.environ.get('MILO_PORT','8766')))
    parser.add_argument('--documents',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--settings',type=Path,default=None)
    parser.add_argument('--voice-dir',type=Path,default=Path(os.environ.get('MILO_VOICE_DIR',str(Path.home()/'.cache/tmp/milo-models'))))
    args=parser.parse_args()
    # A global HF_HOME may belong to another app. Milo uses its explicit voice cache.
    os.environ['HF_HOME']=str(args.voice_dir);os.environ['HF_HUB_OFFLINE']='1'
    documents=DocumentWorkspace(args.documents);documents.seed()
    preferences=Preferences(args.settings or Path(os.environ.get('MILO_SETTINGS_DIR',str(args.documents.parent/'settings'))))
    engine=DemoEngine(args.voice_dir,documents,preferences)
    http=ThreadingHTTPServer(('127.0.0.1',args.port),demo_handler(engine,args.port));http.daemon_threads=True
    print(f'Milo demos: http://127.0.0.1:{args.port}/demo',flush=True);http.serve_forever()

if __name__=='__main__':main()
