"""Exercise voice presets, local model speech, and real public search without a microphone."""
import base64
import json
import os
import struct
import time
import urllib.request
from pathlib import Path
BASE=os.environ.get('MILO_URL','http://127.0.0.1:8767')

def turn(payload):
    began=time.monotonic();counts={};sentences=[];metrics={};peak=0;frames=0;sources=[]
    req=urllib.request.Request(BASE+'/api/turn',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=90) as r:
        for line in r:
            row=json.loads(line);kind=row['type'];counts[kind]=counts.get(kind,0)+1
            assert kind!='error', row
            if kind=='sentence':sentences.append(row['text'])
            if kind=='audio':
                if 'first_audio_received_ms' not in metrics:metrics['first_audio_received_ms']=round((time.monotonic()-began)*1000)
                data=base64.b64decode(row['pcm']);samples=struct.unpack('<'+'f'*(len(data)//4),data)
                peak=max(peak,max(abs(x) for x in samples));frames+=len(samples)
            if kind=='sources':sources=row['sources']
            if kind=='done':metrics.update(row['metrics'])
    assert counts.get('done')==1 and peak>.001,counts
    return {'events':counts,'sentences':sentences,'sources':sources,'timings':metrics,'audio_peak':round(peak,4),'audio_seconds':round(frames/24000,2)}

result={}
for voice in ['marius','javert','bill_boerst','stuart_bell','alba']:
    result[voice]=turn({'id':'voice-'+voice,'audition':True,'voice':voice})
result['reasoning']=turn({'id':'new-model','text':'I have 20 minutes. A report takes 15 minutes and needs a 5 minute booking done first. Can I also finish a 10 minute email?','voice':'marius'})
result['web']=turn({'id':'web-query','text':'Look up the official Pocket TTS project. What does it do?','voice':'marius'})
assert result['web']['sources']
result['followup']=turn({'id':'web-followup','text':'Does that need a GPU?','sources':result['web']['sources'],'messages':[{'role':'user','content':'Look up the official Pocket TTS project. What does it do?'},{'role':'assistant','content':' '.join(result['web']['sentences'])}],'voice':'marius'})
assert 'searching' not in result['followup']['events']
Path('audio/milo/evidence/live-v2.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({key:{'timings':value['timings'],'peak':value['audio_peak'],'seconds':value['audio_seconds'],'sentences':value['sentences'],'source_count':len(value['sources'])} for key,value in result.items()},indent=2))
