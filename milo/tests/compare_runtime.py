"""Check the production prompt, not just an isolated benchmark prompt."""
import json,sys,time,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import SYSTEM
cases=[
'I have 20 minutes. A report takes 15 minutes and needs a 5 minute booking done first. Can I also finish a 10 minute email?',
'I have 50 dollars. The adapter costs 18, the cable costs 12, and the charger costs 25. Can I get all three? What would you suggest?',
'A jar has twice as many blue tokens as red tokens, and 18 tokens total. I remove 3 blue tokens. How many tokens remain, and how many are red?',
'I want to compare two headphones without spending the whole afternoon researching. What is a useful first step?'
]
results=[]
for model in ['phi4:latest','gpt-oss:20b']:
 for prompt in cases:
  payload={'model':model,'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':prompt}],'stream':True,'keep_alive':'5m','options':{'temperature':0,'num_predict':500,'num_ctx':4096}}
  if model.startswith('gpt-oss'):payload['think']='low'
  start=time.monotonic();answer='';thinking='';first=None
  req=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=180) as response:
   for line in response:
    row=json.loads(line)
    if row.get('error'):raise RuntimeError(row['error'])
    token=row.get('message',{}).get('content','');thinking+=row.get('message',{}).get('thinking','')
    if token and first is None:first=round((time.monotonic()-start)*1000)
    answer+=token
  result={'model':model,'prompt':prompt,'answer':answer,'first_content_ms':first,'total_ms':round((time.monotonic()-start)*1000),'thinking_characters':len(thinking)};results.append(result)
  print(json.dumps(result),flush=True)
  Path('audio/milo/evidence/runtime-comparison.json').write_text(json.dumps(results,indent=2)+'\n')
