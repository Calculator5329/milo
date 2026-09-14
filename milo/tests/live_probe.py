"""Synthetic loopback integration probe; never opens a physical microphone."""
import base64
import io
import os
import json
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

BASE = os.environ.get('MILO_URL', 'http://127.0.0.1:8766')
def post(data, origin=None):
    headers={'Content-Type':'application/json'}
    if origin: headers['Origin']=origin
    return urllib.request.urlopen(urllib.request.Request(BASE+'/api/turn', data=json.dumps(data).encode(), headers=headers), timeout=60)
def run(data):
    started=time.monotonic(); counts={}; metrics={}
    with post(data) as response:
        for line in response:
            event=json.loads(line); kind=event['type']; counts[kind]=counts.get(kind,0)+1
            if kind=='audio' and 'client_first_audio_ms' not in metrics: metrics['client_first_audio_ms']=round((time.monotonic()-started)*1000)
            assert kind!='error', event
            if kind=='done': metrics.update(event['metrics'])
    assert counts.get('audio',0)>0 and counts.get('done')==1, counts
    return {'events':counts,'timings':metrics}
results={'text':run({'id':'probe-text','text':'Say hello in one short sentence.'})}
import numpy as np
from scipy.io.wavfile import read
rate, samples=read(str(Path.home()/'.cache/tmp/milo-models/milo-hello.wav'))
target=np.interp(np.arange(0,len(samples),rate/16000),np.arange(len(samples)),samples)
if np.issubdtype(samples.dtype,np.floating): target=target*32767
target=np.clip(target,-32768,32767).astype('<i2')
buf=io.BytesIO()
with wave.open(buf,'wb') as output:
    output.setnchannels(1); output.setsampwidth(2);output.setframerate(16000);output.writeframes(target.tobytes())
results['synthetic_audio']=run({'id':'probe-audio','audio':base64.b64encode(buf.getvalue()).decode()})
for name,data,origin,code in [('foreign_origin',{'id':'probe-origin','text':'Hello'},'https://example.com',403),('invalid_audio',{'id':'probe-invalid','audio':'broken'},None,400)]:
    try: post(data,origin); raise AssertionError('Accepted invalid request')
    except urllib.error.HTTPError as e: assert e.code==code;results[name]=e.code
with post({'id':'probe-cancel','text':'Explain how a bicycle works in three sentences.'}) as response:
    for line in response:
        if json.loads(line)['type']=='audio':
            req=urllib.request.Request(BASE+'/api/cancel',data=b'{"id":"probe-cancel"}',headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req) as cancelled: assert json.load(cancelled)['cancelled']
            tail=[json.loads(line)['type'] for line in response]
            assert 'done' not in tail
            results['cancel']={'completed_after_cancel':False};break
print(json.dumps(results,indent=2))
