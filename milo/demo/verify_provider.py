"""Real loopback protocol probe with a deterministic local provider, no paid calls."""
import json,os,sys,tempfile,threading,urllib.request,struct
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from demo_server import DemoEngine,demo_handler
from demo_documents import DocumentWorkspace

requests=[]
class ProviderHandler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append(data)
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        for content in ('A fixture answer.',' Only the current question arrived.'):
            self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{'content':content}}]})+'\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')
class Chunk:
    def detach(self):return self
    def cpu(self):return self
    def numpy(self):return self
    def astype(self,_):return self
    def tobytes(self):return struct.pack('<f',0)*240
class Voice:
    sample_rate=24000
    def generate_audio_stream(self,*_):yield Chunk()
class NoSearch:
    def search(self,*_,**__):raise AssertionError('API mode unexpectedly searched')
class FixtureEngine(DemoEngine):
    def load(self):
        self.tts=Voice();self.voices={'marius':None};self.search=NoSearch();self.ready.set()

def serve(server):
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();return thread

def main():
    provider=ThreadingHTTPServer(('127.0.0.1',0),ProviderHandler);serve(provider)
    os.environ.update(MILO_API_ENABLED='1',MILO_API_BASE_URL=f'http://127.0.0.1:{provider.server_port}/v1',MILO_API_MODEL='protocol-fixture')
    folder=Path(tempfile.mkdtemp(prefix='milo-provider-',dir=Path.home()/'.cache/tmp'))
    docs=DocumentWorkspace(folder);docs.create('private-fixture.md','DO_NOT_SHARE_DOCUMENT')
    engine=FixtureEngine(folder,docs);engine.ready.wait(3)
    server=ThreadingHTTPServer(('127.0.0.1',0),BaseHTTPRequestHandler);server.RequestHandlerClass=demo_handler(engine,server.server_port);serve(server)
    base=f'http://127.0.0.1:{server.server_port}'
    payload={'id':'fixture-api','text':'Look up how API history works','backend':'api','document':'private-fixture.md','messages':[{'role':'user','content':'DO_NOT_SHARE_HISTORY'}]}
    request=urllib.request.Request(base+'/api/demo/turn',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Origin':base})
    with urllib.request.urlopen(request,timeout=10) as response:rows=[json.loads(line) for line in response]
    assert not any(row['type']=='error' for row in rows),rows
    assert any(row['type']=='provider' for row in rows),rows
    assert any(row['type']=='audio' for row in rows),rows
    assert len(requests)==1,requests
    outgoing=json.dumps(requests[0]);assert 'DO_NOT_SHARE_' not in outgoing
    assert len(requests[0]['messages'])==2
    assert requests[0]['messages'][-1]['content']==payload['text']
    result={'passed':True,'transport':'real HTTP on loopback','provider':'deterministic fixture, not a cloud model','outgoing_message_count':2,'history_and_document_excluded':True,'local_search_skipped':True,'events':[r['type'] for r in rows],'paid_requests':0,'retained_fixture':str(folder)}
    (Path(__file__).parent/'evidence'/'provider.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    server.shutdown();provider.shutdown()
if __name__=='__main__':main()
