import {MiloClient} from '/demo-client.js';
import {syncPreferences} from '/preferences.js';
const $=s=>document.querySelector(s);function bridge(action,extra={}){if(window.webkit?.messageHandlers?.milo)window.webkit.messageHandlers.milo.postMessage(JSON.stringify({action,...extra}));}
let nodeTimer,thoughtTimer;
const thought=$('#thought');
function thoughtRect(){const r=thought.getBoundingClientRect();return {x:Math.floor(r.left),y:Math.floor(r.top),w:Math.ceil(r.width),h:Math.ceil(r.height)};}
function armThoughtTimer(){clearTimeout(thoughtTimer);thoughtTimer=setTimeout(hideThought,45000);}
function hideThought(){if(thought.hidden)return;clearTimeout(thoughtTimer);thought.hidden=true;thought.replaceChildren();bridge('thought-close');}
function showThought(event){
 const row=document.createElement('div'),value=document.createElement('span'),state=document.createElement('small');
 row.className='thought-row';row.dataset.kind=event.kind;value.className='thought-text';value.textContent=event.text;state.className='copy-state';state.textContent='copy';row.append(value,state);
 row.onclick=click=>{click.stopPropagation();bridge('copy',{text:event.text});state.textContent='copied';row.dataset.copied='true';armThoughtTimer();setTimeout(()=>{state.textContent='copy';delete row.dataset.copied;},1500);};
 thought.append(row);thought.hidden=false;requestAnimationFrame(()=>bridge('thought-open',{rect:thoughtRect()}));armThoughtTimer();
}
const client=new MiloClient(event=>{if(event.type==='mouth'){$('#robot').style.setProperty('--mouth-open',Math.max(.05,event.level));return;}if(event.type!=='speaking'||document.body.dataset.state!=='speaking')bridge('diagnostic',{kind:event.type});if(event.type==='working'){$('#reply').textContent='One moment…';document.body.dataset.state='waiting';}if(event.type==='drafting')$('#reply').textContent='Drafting locally…';if(event.type==='draft'){$('#reply').textContent='Draft ready: '+event.proposal.name+'. Open Demos → Notebook to review and apply it. Nothing saved yet.';$('#route').textContent='Draft kept until the demo server restarts or another draft replaces it.';}if(event.type==='transcript'){hideThought();$('#route').textContent='You: '+event.text;}if(event.type==='thought')showThought(event);if(event.type==='caption')$('#reply').textContent=event.text;if(event.type==='speaking')document.body.dataset.state='speaking';if(event.type==='listening'){$('#reply').textContent='Listening while you hold…';document.body.dataset.state='listening';}if(event.type==='searching')$('#reply').textContent='Looking it up…';if(event.type==='action'){const result=event.result;$('#reply').textContent=(result.status==='preview'?'Preview only: ':'')+result.title+'. '+result.detail;$('#route').textContent=result.executed?'Completed in the demo notebook.':'No desktop action executed.';document.body.dataset.state=result.status==='failed'?'idle':'done';clearTimeout(nodeTimer);nodeTimer=setTimeout(()=>document.body.dataset.state='idle',1300);}if(event.type==='route')$('#notice').textContent=event.plan.delivery==='silent'?'Silent action · documents save in the demo folder; desktop commands are previews.':'Spoken answer · local model and voice.';if(event.type==='error'){if(event.message.startsWith('Native voice playback failed:'))bridge('playback-error',{detail:event.message});$('#reply').textContent=event.message;document.body.dataset.state='idle';}if(event.type==='idle'||event.type==='stopped'){if(document.body.dataset.state!=='done')document.body.dataset.state='idle';}});
const preferenceSync=syncPreferences({client});
window.addEventListener('milo-preferences-error',event=>$('#notice').textContent='Preferences: '+event.detail.message);
function expand(){bridge('expand');}
window.miloExpanded=()=>{document.body.dataset.expanded='true';};
window.miloCollapse=()=>{client.cancelRecording();client.stop();bridge('microphone-stop');document.activeElement?.blur();document.body.dataset.expanded='false';document.body.dataset.state='idle';};
function collapse(){window.miloCollapse();bridge('collapse');}
$('#robot').onclick=()=>{if(!thought.hidden){hideThought();return;}document.body.dataset.expanded==='true'?collapse():expand();};$('#minimize').onclick=collapse;$('#demos').onclick=()=>{if(window.webkit?.messageHandlers?.milo)bridge('open-demos');else window.open('/demo','_blank','noopener');};
$('#form').onsubmit=event=>{event.preventDefault();const text=$('#text').value.trim();if(text){$('#text').value='';client.submit(text);}};$('#stop').onclick=()=>{client.cancelRecording();client.stop();bridge('microphone-stop');$('#reply').textContent='Stopped.';};
function hold(){bridge('microphone-start');client.record();}function release(){client.finishRecording();bridge('microphone-stop');}
window.miloHoldPress=hold;window.miloHoldRelease=release;
$('#hold').onpointerdown=event=>{event.preventDefault();$('#hold').setPointerCapture(event.pointerId);hold();};$('#hold').onpointerup=release;$('#hold').onpointercancel=()=>{client.cancelRecording();bridge('microphone-stop');};
window.addEventListener('keydown',event=>{if(event.key==='Escape'){if(!thought.hidden)hideThought();else collapse();}if(event.code==='Space'&&!event.repeat&&document.body.dataset.expanded==='true'&&document.hasFocus()&&!/INPUT|TEXTAREA|BUTTON/.test(event.target.tagName)){event.preventDefault();hold();}});window.addEventListener('keyup',event=>{if(event.code==='Space'&&client.recordWanted){event.preventDefault();release();}});
window.addEventListener('pagehide',()=>{client.cancelRecording();client.stop();bridge('microphone-stop');});document.addEventListener('visibilitychange',()=>{if(document.hidden){client.cancelRecording();client.stop();bridge('microphone-stop');}});
async function health(){try{const response=await fetch('/api/demo/state'),state=await response.json();$('#send').disabled=$('#hold').disabled=!state.ready;$('#notice').textContent=state.ready?'Ready · microphone off until you hold. Desktop shortcuts stay separate.':state.error?'Local speech failed to load. Open demos for the notebook.':'Warming up local speech…';if(!state.ready)setTimeout(health,1500);}catch{$('#notice').textContent='Demo server unavailable.';}}health();

$('#text').addEventListener('pointerdown',()=>bridge('keyboard-focus'));$('#text').addEventListener('blur',()=>bridge('keyboard-release'));

window.miloTestVoice=()=>client.submit('',{audition:true});$('#test-voice').onclick=window.miloTestVoice;

async function sayFromReminder(text){
 client.stop();const epoch=client.epoch;await client.audio();if(epoch!==client.epoch)return;
 const turn={id:crypto.randomUUID(),abort:new AbortController(),playing:new Set(),timers:new Set(),sentences:new Map(),next:client.ctx.currentTime,done:false,rate:1,audition:false,native:!!globalThis.webkit?.messageHandlers?.milo,nativeQueue:Promise.resolve(),streaming:!!globalThis.miloPcmStreaming};
 client.active=turn;
 if(turn.streaming){globalThis.miloNativeAudio=event=>client.nativeProgress(event);turn.nativeSource={stop:()=>{client.pcm(turn,'pcm-stop');client.emit({type:'mouth',level:0});},disconnect(){}};turn.playing.add(turn.nativeSource);client.pcm(turn,'pcm-start');}
 client.emit({type:'working'});
 try{
  const response=await fetch('/api/demo/say',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:turn.id,text,voice:client.voice,pitch:client.pitch,rate:client.playbackRate,sound:client.soundProfile}),signal:turn.abort.signal});
  if(!response.ok){const data=await response.json();throw Error(data.error||'Reminder speech failed.');}
  const reader=response.body.getReader(),decoder=new TextDecoder();let pending='';
  while(true){const row=await reader.read();if(client.active!==turn){reader.cancel();return;}if(row.done)break;pending+=decoder.decode(row.value,{stream:true});let nl;while((nl=pending.indexOf('\n'))>=0){const line=pending.slice(0,nl);pending=pending.slice(nl+1);if(!line)continue;const event=JSON.parse(line);if(client.active!==turn||event.id!==turn.id)return;if(event.type==='sentence')turn.sentences.set(event.index,{text:event.text,end:Infinity,complete:false,caption:false});if(event.type==='audio'){client.chunk(turn,event);continue;}if(event.type==='sentence_end'){const sentence=turn.sentences.get(event.index);if(sentence){sentence.complete=true;if(turn.streaming)client.pcm(turn,'pcm-sentence-end',{index:event.index});else if(turn.native)client.nativeSentence(turn,event.index);}}if(event.type==='error')throw Error(event.message);if(event.type==='done'){turn.done=true;if(turn.streaming)client.pcm(turn,'pcm-end');}client.emit(event);if(turn.done)client.complete(turn);}}
  if(client.active===turn&&!turn.done)throw Error('Reminder speech ended early.');
 }catch(error){if(error.name==='AbortError'||client.active!==turn)return;client.stop();client.emit({type:'error',message:error.message});}
}
window.miloSay=text=>sayFromReminder(String(text||''));
window.miloListening=on=>{if(on){client.stop();document.body.dataset.state='listening';}else if(document.body.dataset.state==='listening'){document.body.dataset.state='idle';}};
// Ask: a question handed over from the hotkey assistant becomes a normal typed turn (history, sources and memory apply).
window.miloAsk=text=>{const question=String(text||'').trim();if(!question)return;$('#route').textContent='You (hotkey): '+question;client.submit(question);};
