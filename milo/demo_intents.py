"""Deterministic demo routing. It plans delivery; it never executes generated commands."""
from __future__ import annotations
import re


POLITE = re.compile(r'^(?:hey milo[,!]?\s+)?(?:please\s+)?(?:(?:can|could|would|will) you\s+)?(?:please\s+)?',re.I)


def note_name(title):
    title=title.strip().strip('"\'').strip()
    if '/' in title or '\\' in title or '..' in title:
        raise ValueError('Use a note title, not a path.')
    if title.lower().endswith(('.md','.txt')):
        return title.lower()
    slug=re.sub(r'[^a-z0-9]+','-',title.lower()).strip('-')
    if not slug or len(slug)>70:
        raise ValueError('Use a short note title.')
    return slug+'.md'


def decide(text):
    """Return a plan from explicit wording. Ambiguity never becomes a write."""
    raw=text.strip()
    command=POLITE.sub('',raw).strip()
    if not command:
        return {'kind':'question','delivery':'spoken','reason':'A conversation turn'}
    match=re.fullmatch(r'draft (?:a )?(?:note|document) (?:called |named )?(.{1,80}?):\s*(.+)',command,re.I|re.S)
    if match:
        return {'kind':'document_draft','delivery':'silent','name':note_name(match[1]),'instruction':match[2],'reason':'Prepare a local draft for review; do not save yet'}
    match=re.fullmatch(r'(?:find|search) (?:my |the )?(?:notes|documents) (?:about |for |containing )?(.{1,180})',command,re.I|re.S)
    if match:
        return {'kind':'document_search','delivery':'silent','query':match[1].strip(),'reason':'Search the bounded demo notebook'}
    match=re.fullmatch(r'(?:create|write|make) (?:a )?(?:note|document) (?:called |named )?(.{1,80}?):\s*(.+)',command,re.I|re.S)
    if match:
        return {'kind':'document_create','delivery':'silent','name':note_name(match[1]),'content':match[2], 'reason':'Explicit note creation'}
    match=re.fullmatch(r'(?:append|add) to (?:the )?(?:note |document )?(.{1,80}?):\s*(.+)',command,re.I|re.S)
    if match:
        return {'kind':'document_append','delivery':'silent','name':note_name(match[1]),'content':match[2], 'reason':'Explicit note update'}
    command=command.rstrip('?.!')
    match=re.fullmatch(r'(?:open|show) (?:the )?(?:note|document) (?:called |named )?(.{1,80})',command,re.I)
    if match:
        return {'kind':'document_open','delivery':'silent','name':note_name(match[1]),'reason':'Open the requested note'}
    match=re.fullmatch(r'(?:summarize|summarise|explain) (?:the )?(?:note|document) (?:called |named )?(.{1,80})',command,re.I)
    if match:
        return {'kind':'document_question','delivery':'spoken','name':note_name(match[1]),'reason':'An information request about a note'}
    if re.match(r'^(?:use|ask|send (?:this|it) to) (?:a |the )?(?:stronger|higher|frontier|cloud) (?:model|api)\b',command,re.I):
        return {'kind':'api_preview','delivery':'spoken','reason':'You explicitly requested a stronger model','remote_enabled':False}
    # Information questions win over embedded action verbs: 'how do I open...' is not an open command.
    if re.match(r'^(?:what|why|how|when|where|who|which|is|are|does|do|should|could|would|can|explain|tell me|help me understand)\b',command,re.I):
        return {'kind':'question','delivery':'spoken','reason':'An information request'}
    if re.match(r'^(?:look up|search (?:the )?(?:web|internet)|search for|check online for)\b',command,re.I):
        return {'kind':'web','delivery':'spoken','reason':'An explicit web information request'}
    if re.match(r'^(?:open|focus|move|go to|switch to|launch|start|pause|resume|next|previous|mute|unmute|close|stop|play|fullscreen|stash|hide)\b',command,re.I):
        return {'kind':'action_preview','delivery':'silent','reason':'A desktop action request; preview uses installed Jarvis'}
    return {'kind':'question','delivery':'spoken','reason':'Conversation; no explicit write or desktop command'}


def api_proposal(text, selected_document=False):
    """A visible proposal, not automatic confidence scoring or an enabled transport."""
    explicit=decide(text)['kind']=='api_preview'
    complex_request=bool(re.search(r'\b(?:architecture|trade.?offs|compare multiple|deep research|long document|prove|debug)\b',text,re.I))
    return {'suggested':explicit or complex_request,'reason':('Explicit higher-model request' if explicit else 'This wording suggests a more demanding reasoning task' if complex_request else 'Local model is the proposed first stop'),
            'method':'POST','destination':'Not configured','model':'Owner choice pending',
            'input':text,'document_included':False,'document_available_locally':selected_document,
            'api_calls_enabled':False,'request_sent':False,'spend_incurred':False,
            'note':'Rule-based demo proposal, not a confidence measurement. Document content would require a separate sharing choice.'}
