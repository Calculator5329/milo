"""Bounded loopback acceptance probe; retains its sample note and JSON evidence."""
import collections,json,time,urllib.request,urllib.error
from pathlib import Path
BASE='http://127.0.0.1:8776'

def request(path,data=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','Origin':BASE})
    return urllib.request.urlopen(req,timeout=150)
def api(path,data=None):
    with request(path,data) as r:return json.load(r)
def turn(text,document=None):
    start=time.monotonic()
    with request('/api/demo/turn',{'id':'verify-'+str(time.time_ns()),'text':text,'document':document}) as r:
        rows=[json.loads(line) for line in r]
    counts=dict(collections.Counter(row['type'] for row in rows))
    assert counts.get('done')==1 and not counts.get('error'),counts
    return rows,{'events':counts,'elapsed_s':round(time.monotonic()-start,2),'sentences':[r['text'] for r in rows if r['type']=='sentence']}

def main():
    assert api('/api/demo/state')['ready']
    evidence={}
    name='verification-note.md'
    try:doc=api('/api/demo/document?name='+name)
    except urllib.error.HTTPError:
        rows,evidence['create']=turn('Create a note verification-note: First retained version.')
        assert not evidence['create']['events'].get('audio')
        doc=api('/api/demo/document?name='+name)
    original=doc['content'];revision=doc['revision']
    rows,evidence['append']=turn('Append to verification-note: Second retained version.')
    assert not evidence['append']['events'].get('audio')
    updated=api('/api/demo/document?name='+name)
    assert 'Second retained version.' in updated['content']
    try:
        api('/api/demo/document',{'operation':'replace','name':name,'content':'Stale overwrite','revision':revision})
        raise AssertionError('stale write accepted')
    except urllib.error.HTTPError as error:assert error.code==400
    restored=api('/api/demo/document',{'operation':'restore','name':name,'revision':updated['revision'],'restore_revision':revision})['document']
    assert restored['content']==original
    evidence['restore_and_stale_write']='passed'
    rows,evidence['desktop_preview']=turn('Open Firefox')
    action=next(r['result'] for r in rows if r['type']=='action')
    assert action['executed'] is False and not evidence['desktop_preview']['events'].get('audio')
    rows,evidence['document_question']=turn('Summarize the note project-brief')
    assert evidence['document_question']['events'].get('audio',0)>0
    assert any(r['type']=='document_context' for r in rows)
    context=next(r for r in rows if r['type']=='context_summary')
    assert context['document']['name']=='project-brief.md'
    assert 0 < context['document']['characters'] <= len(api('/api/demo/document?name=project-brief.md')['content'])
    evidence['packed_document_context']=context
    rows,evidence['higher_model']=turn('Use a stronger model to compare architecture tradeoffs')
    proposal=next(r['proposal'] for r in rows if r['type']=='api_proposal')
    assert not proposal['request_sent'] and not proposal['document_included']
    evidence['api_proposal']=proposal
    prior=next((item for item in api('/api/demo/state')['documents'] if item['name']=='verification-draft.md'),None)
    rows,evidence['draft']=turn('Draft note verification-draft: Write a short two-step welcome checklist for the seed-sharing project.',document='project-brief.md')
    proposal=next(row['proposal'] for row in rows if row['type']=='draft')
    assert not proposal['saved'] and not evidence['draft']['events'].get('audio')
    current=next((item for item in api('/api/demo/state')['documents'] if item['name']=='verification-draft.md'),None)
    assert current==prior, 'Draft generation unexpectedly changed a document'
    assert api('/api/demo/state')['draft']['id']==proposal['id']
    applied=api('/api/demo/document',{'operation':'replace' if prior else 'create','name':proposal['name'],'revision':proposal['revision'],'content':proposal['content'],'draft_id':proposal['id']})
    assert applied['document']['content']==proposal['content']
    evidence['draft_apply']={'saved_after_explicit_apply':True,'name':proposal['name'],'characters':len(proposal['content']),'content':proposal['content']}
    rows,evidence['note_search']=turn('Find notes about seeds')
    matches=next(row['result']['matches'] for row in rows if row['type']=='action')
    assert any(item['name']=='project-brief.md' for item in matches)
    rows,evidence['save_receipt_followup']=turn('Did you save the verification draft?')
    path=Path(__file__).parent/'evidence'/'live.json';path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(evidence,indent=2))
if __name__=='__main__':main()
