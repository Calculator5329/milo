import io,json,os,sys,threading,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from providers import Provider,ProviderError,status

class Response(io.BytesIO):
    pass
class Transport:
    def __init__(self,body):self.body=body;self.requests=[]
    def open(self,request,timeout):
        self.requests.append(request)
        if isinstance(self.body,Exception):raise self.body
        return Response(self.body)
class SequenceTransport:
    def __init__(self,*results):self.results=list(results);self.requests=[]
    def open(self,request,timeout):
        self.requests.append(request)
        result=self.results.pop(0)
        if isinstance(result,Exception):raise result
        return result
class InterruptedResponse:
    def __init__(self,first):self.first=first
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def readline(self,limit):
        if self.first is not None:
            first,self.first=self.first,None
            return first
        raise urllib.error.URLError('synthetic interruption')
def stream(*events):
    return b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events)+b'data: [DONE]\n\n'

class ProviderTests(unittest.TestCase):
    def test_explicit_configuration_and_endpoint_rules(self):
        self.assertIsNone(Provider.configured({}))
        for base in ['https://api.example.test/v1','http://127.0.0.1:11434/v1']:
            self.assertEqual(Provider.configured({'MILO_API_ENABLED':'1','MILO_API_BASE_URL':base,'MILO_API_MODEL':'example'}).base_url,base)
        for base in ['http://api.example.test/v1','https://user:secret@example.test/v1','https://example.test/v1?key=bad','file:///etc/passwd','https://example.test:bad/v1']:
            with self.assertRaises(ProviderError):Provider.configured({'MILO_API_ENABLED':'1','MILO_API_BASE_URL':base,'MILO_API_MODEL':'example'})
    def test_actual_serialized_request_and_tokens(self):
        transport=Transport(stream({'choices':[{'delta':{'content':'Hello '}}]},{'choices':[{'delta':{'content':'there.'},'finish_reason':'stop'}]}))
        with patch.dict(os.environ,{},clear=True),patch('urllib.request.build_opener',return_value=transport):
            self.assertEqual(''.join(Provider('http://127.0.0.1:11434/v1','fixture',True).stream([{'role':'user','content':'Question'}],threading.Event())),'Hello there.')
        payload=json.loads(transport.requests[0].data)
        self.assertEqual(payload['messages'],[{'role':'user','content':'Question'}]);self.assertEqual(payload['max_completion_tokens'],500)
        self.assertNotIn('Authorization',transport.requests[0].headers)
    def test_cancelled_turn_never_opens(self):
        cancelled=threading.Event();cancelled.set();transport=Transport(b'')
        with patch('urllib.request.build_opener',return_value=transport):
            self.assertEqual(list(Provider('http://localhost/v1','fixture',True).stream([],cancelled)),[])
        self.assertFalse(transport.requests)
    def test_error_bodies_are_not_exposed(self):
        raw=urllib.error.HTTPError('https://provider.example/v1',401,'bad',{},io.BytesIO(b'private-fixture-value'))
        with patch.dict(os.environ,{'MILO_API_KEY':'synthetic-test-value'}),patch('urllib.request.build_opener',return_value=Transport(raw)):
            with self.assertRaises(ProviderError) as caught:list(Provider('https://provider.example/v1','fixture',False).stream([],threading.Event()))
        self.assertIn('HTTP 401',str(caught.exception));self.assertNotIn('private-fixture',str(caught.exception));self.assertNotIn('synthetic-test',str(caught.exception))
    def test_incomplete_or_tool_stream_is_rejected(self):
        for body in [b'data: {invalid}\n',b'data: {"choices":[]}\n',stream({'choices':[{'delta':{'tool_calls':[{}]}}]})]:
            with patch('urllib.request.build_opener',return_value=Transport(body)),self.assertRaises(ProviderError):
                list(Provider('http://localhost/v1','fixture',True).stream([],threading.Event()))
    def test_public_status_has_no_credential(self):
        with patch.dict(os.environ,{'MILO_API_ENABLED':'1','MILO_API_BASE_URL':'https://api.example.test/v1','MILO_API_MODEL':'fixture','MILO_API_KEY':'synthetic-test-value'}):
            self.assertNotIn('synthetic-test-value',json.dumps(status()))

    def test_openrouter_defaults_need_no_milo_configuration(self):
        provider=Provider.configured({'OPENROUTER_API_KEY':'synthetic-openrouter-value'})
        self.assertEqual(provider.base_url,'https://openrouter.ai/api/v1')
        self.assertEqual(provider.model,'nvidia/nemotron-3-ultra-550b-a55b:free')
        self.assertEqual(provider.models,(
            'nvidia/nemotron-3-ultra-550b-a55b:free',
            'google/gemma-4-31b-it:free',
            'nvidia/nemotron-3-super-120b-a12b:free',
            'thinkingmachines/inkling:free',
            'google/gemma-4-26b-a4b-it:free',
        ))
        self.assertIsNone(Provider.configured({'OPENROUTER_API_KEY':'synthetic-openrouter-value','MILO_API_ENABLED':'0'}))

    def test_model_chain_parsing_prefers_models_then_model(self):
        base={'OPENROUTER_API_KEY':'synthetic-openrouter-value','MILO_API_MODEL':'ignored'}
        provider=Provider.configured(dict(base,MILO_API_MODELS=' first/model , second/model '))
        self.assertEqual(provider.model,'first/model')
        self.assertEqual(provider.models,('first/model','second/model'))
        provider=Provider.configured(base)
        self.assertEqual(provider.models,('ignored',))

    def test_openrouter_headers_and_milo_key_precedence(self):
        transport=Transport(stream({'choices':[{'delta':{'content':'ready'},'finish_reason':'stop'}]}))
        env={'OPENROUTER_API_KEY':'synthetic-openrouter-value','MILO_API_KEY':'synthetic-milo-value'}
        provider=Provider.configured(env)
        with patch.dict(os.environ,env,clear=True),patch('urllib.request.build_opener',return_value=transport):
            self.assertEqual(''.join(provider.stream([],threading.Event())),'ready')
        request=transport.requests[0]
        self.assertEqual(request.get_header('Authorization'),'Bearer synthetic-milo-value')
        self.assertEqual(request.get_header('Http-referer'),'https://github.com/Calculator5329/milo')
        self.assertEqual(request.get_header('X-title'),'Milo')

    def test_fallback_on_429_before_first_token(self):
        limited=urllib.error.HTTPError('https://openrouter.ai/api/v1/chat/completions',429,'limited',{},io.BytesIO(b'private'))
        transport=SequenceTransport(limited,Response(stream({'choices':[{'delta':{'content':'ready'},'finish_reason':'stop'}]})))
        provider=Provider.configured({'OPENROUTER_API_KEY':'synthetic-openrouter-value','MILO_API_MODELS':'first/model,second/model'})
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'synthetic-openrouter-value'},clear=True),patch('urllib.request.build_opener',return_value=transport):
            self.assertEqual(''.join(provider.stream([],threading.Event())),'ready')
        self.assertEqual([json.loads(request.data)['model'] for request in transport.requests],['first/model','second/model'])
        self.assertEqual(provider.last_model,'second/model')
        self.assertEqual(provider.last_usage['model'],'second/model')

    def test_no_fallback_after_first_token(self):
        event=b'data: '+json.dumps({'choices':[{'delta':{'content':'partial'}}]}).encode()+b'\n\n'
        transport=SequenceTransport(InterruptedResponse(event),Response(stream({'choices':[{'delta':{'content':'wrong'}}]})))
        provider=Provider.configured({'OPENROUTER_API_KEY':'synthetic-openrouter-value','MILO_API_MODELS':'first/model,second/model'})
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'synthetic-openrouter-value'},clear=True),patch('urllib.request.build_opener',return_value=transport):
            iterator=provider.stream([],threading.Event())
            self.assertEqual(next(iterator),'partial')
            with self.assertRaises(ProviderError):next(iterator)
        self.assertEqual(len(transport.requests),1)
        self.assertEqual(provider.last_model,'first/model')

    def test_public_lists_models_without_credentials(self):
        secret='synthetic-openrouter-value'
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':secret},clear=True):
            public=Provider.configured().public()
        self.assertEqual(public['models'][0],'nvidia/nemotron-3-ultra-550b-a55b:free')
        self.assertNotIn(secret,json.dumps(public))
if __name__=='__main__':unittest.main()
