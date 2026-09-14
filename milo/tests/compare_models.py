"""Small reproducible conversational sample; timings are measurements, answers need review."""
import json
import time
import urllib.request
from pathlib import Path

CASES = [
 ('reasoning','Three boxes are labeled apples, oranges, and mixed. Every label is wrong. You can draw one fruit from one box. Which box do you draw from, and how does that identify all three? Explain briefly.'),
 ('correction','I have 20 minutes before a meeting and three jobs: a 15-minute report, a 10-minute email, and a 5-minute booking. The report needs the booking done first. I was going to do the report and email. What should I do?'),
 ('useful','I keep opening tabs to research an idea and then losing the thread. Give me one practical thing I can try today, and tell me why it might work.'),
 ('grounding','What happened in the news this morning? If you do not have current sources, say so rather than guessing.'),
 ('followup','Can you make that more concrete for someone comparing two headphones?')
]
SYSTEM='You are Milo, a thoughtful local voice companion. Answer directly and naturally in one to three short sentences, at most 85 words. No markdown or stage directions. You have no current web sources in this test. Do not invent facts or claim actions. Be useful and specific, and correct flawed premises when needed.'
results=[]
for model in ['qwen2.5:3b','phi4:latest','qwen2.5-coder:14b']:
    messages=[{'role':'system','content':SYSTEM}]
    # Warm load is measured separately from per-case response.
    t=time.monotonic()
    payload={'model':model,'prompt':'','keep_alive':'10m','options':{'num_ctx':4096}}
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:11434/api/generate',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'}),timeout=180) as r:r.read()
    load_ms=round((time.monotonic()-t)*1000)
    for name,prompt in CASES:
        context=messages if name=='followup' else [{'role':'system','content':SYSTEM}]
        if name=='followup':
            useful=next(x for x in results if x['model']==model and x['case']=='useful')
            context=[{'role':'system','content':SYSTEM},{'role':'user','content':dict(CASES)['useful']},{'role':'assistant','content':useful['answer']}]
        payload={'model':model,'messages':context+[{'role':'user','content':prompt}],'stream':True,'keep_alive':'10m','options':{'temperature':0,'num_predict':180,'num_ctx':4096}}
        t=time.monotonic();first=None;answer='';done={}
        req=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=180) as r:
            for line in r:
                row=json.loads(line)
                if row.get('error'): raise RuntimeError(row['error'])
                token=row.get('message',{}).get('content','')
                if token and first is None:first=round((time.monotonic()-t)*1000)
                answer+=token
                if row.get('done'):done=row
        result={'model':model,'case':name,'prompt':prompt,'answer':answer,'first_token_ms':first,'total_ms':round((time.monotonic()-t)*1000),'model_warm_load_ms':load_ms,'output_tokens':done.get('eval_count')}
        results.append(result)
        Path('audio/milo/evidence/model-comparison.json').write_text(json.dumps(results,indent=2)+'\n')
        print(model,name,first,result['total_ms'],flush=True)
