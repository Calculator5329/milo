/* Working demo transport: shared by the browser workbench and native corner window. */
export class MiloClient {
 constructor(onEvent=()=>{}){this.onEvent=onEvent;this.history=[];this.sources=[];this.active=null;this.voice='marius';this.model='gemma4:12b';this.document=null;this.backend='local';this.mode='conversational';this.soundProfile='natural';this.playbackRate=1;this.pitch=0;this.recordWanted=false;this.epoch=0;this.micEpoch=0;}
 emit(event){this.onEvent(event);}
 setSoundProfile(profile){if(profile==='small')profile='small-speaker';if(!['natural','clear','small-speaker'].includes(profile))throw Error('Unknown sound profile');this.soundProfile=profile;}
 setPlaybackRate(rate){rate=Number(rate);if(!Number.isFinite(rate)||rate<.65||rate>2)throw Error('Pace must be between 0.65 and 2');this.playbackRate=rate;}
 setPitch(pitch){pitch=Number(pitch);if(!Number.isFinite(pitch)||pitch< -8||pitch>8)throw Error('Pitch must be between -8 and 8 semitones');this.pitch=pitch;}
 async audio(){if(!this.ctx){this.ctx=new AudioContext();this.gain=this.ctx.createGain();this.analyser=this.ctx.createAnalyser();this.analyser.fftSize=256;this.gain.connect(this.analyser);this.analyser.connect(this.ctx.destination);}if(this.ctx.state!=='running')await this.ctx.resume();}
 stop(){this.epoch++;const turn=this.active;this.active=null;if(!turn)return;turn.abort.abort();fetch('/api/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:turn.id}),keepalive:true}).catch(()=>{});this.keepHistory(turn);for(const source of turn.playing){source.onended=null;try{source.stop();}catch{}source.disconnect();}for(const timer of turn.timers)clearTimeout(timer);this.emit({type:'stopped'});}
 keepHistory(turn){if(turn.kept||turn.audition)return;turn.kept=true;const completed=[...turn.sentences.values()].filter(s=>s.complete&&s.end<=this.ctx.currentTime).map(s=>s.text).join(' ');if(completed)this.history.push({role:'assistant',content:completed});if(turn.action)this.history.push({role:'assistant',content:'Visual action result: '+turn.action});this.history=this.history.slice(-10);}
 complete(turn){if(this.active!==turn||!turn.done||turn.playing.size)return;this.keepHistory(turn);this.active=null;this.emit({type:'idle'});}
 pcm(turn,action,extra={}){globalThis.webkit.messageHandlers.milo.postMessage(JSON.stringify({action,id:turn.id,...extra}));}
 nativeProgress(event){const turn=this.active;if(!turn?.streaming||event.id!==turn.id)return;for(const index of event.completed||[]){const sentence=turn.sentences.get(index);if(sentence)sentence.end=this.ctx.currentTime;}if(event.type==='error'){this.stop();this.emit({type:'error',message:event.message});return;}if(event.type==='drained'){turn.playing.delete(turn.nativeSource);this.emit({type:'mouth',level:0});this.complete(turn);return;}if(event.index!=null){const sentence=turn.sentences.get(event.index);if(sentence&&!sentence.caption){sentence.caption=true;this.emit({type:'caption',text:sentence.text});this.emit({type:'speaking'});}}this.emit({type:'mouth',level:event.level||0});}
 chunk(turn,event){if(this.active!==turn)return;if(turn.streaming){this.pcm(turn,'pcm-audio',{index:event.index,sample_rate:event.sample_rate,pcm:event.pcm});return;}const raw=atob(event.pcm),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);const view=new DataView(bytes.buffer),samples=new Float32Array(bytes.length/4);let peak=0;for(let i=0;i<samples.length;i++){samples[i]=view.getFloat32(i*4,true);peak=Math.max(peak,Math.abs(samples[i]));}if(turn.native){const sentence=turn.sentences.get(event.index);(sentence.parts??=[]).push(samples);sentence.rate=event.sample_rate;return;}const buffer=this.ctx.createBuffer(1,samples.length,event.sample_rate);buffer.copyToChannel(samples,0);const source=this.ctx.createBufferSource();source.buffer=buffer;source.playbackRate.value=turn.rate;source.connect(this.gain);const start=Math.max(this.ctx.currentTime+.025,turn.next);turn.next=start+buffer.duration/turn.rate;const sentence=turn.sentences.get(event.index);sentence.end=turn.next;turn.playing.add(source);if(!sentence.caption){sentence.caption=true;const timer=setTimeout(()=>{turn.timers.delete(timer);if(this.active===turn)this.emit({type:'caption',text:sentence.text});},Math.max(0,(start-this.ctx.currentTime)*1000));turn.timers.add(timer);}source.onended=()=>{source.disconnect();turn.playing.delete(source);this.complete(turn);};source.start(start);this.emit({type:'speaking',peak});}
 nativeSentence(turn,index){
  const sentence=turn.sentences.get(index),parts=sentence.parts||[];
  if(!parts.length)return;
  const length=parts.reduce((n,p)=>n+p.length,0),data=new ArrayBuffer(44+length*2),v=new DataView(data);
  const str=(at,s)=>{for(let i=0;i<s.length;i++)v.setUint8(at+i,s.charCodeAt(i));};
  str(0,'RIFF');v.setUint32(4,36+length*2,true);str(8,'WAVE');str(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,sentence.rate,true);v.setUint32(28,sentence.rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);str(36,'data');v.setUint32(40,length*2,true);
  let at=44;for(const part of parts)for(const sample of part){const x=Math.max(-1,Math.min(1,sample));v.setInt16(at,x*(x<0?32768:32767),true);at+=2;}sentence.parts=[];
  const frameSize=Math.max(1,Math.round(sentence.rate*.02)),envelope=[];
  for(let start=0;start<length;start+=frameSize){let energy=0;const end=Math.min(length,start+frameSize);for(let i=start;i<end;i++){const sample=v.getInt16(44+i*2,true)/32768;energy+=sample*sample;}envelope.push(Math.sqrt(energy/(end-start)));}
  const peak=Math.max(.015,...envelope);
  const url=URL.createObjectURL(new Blob([data],{type:'audio/wav'})),media=new Audio(url);
  let mouthFrame;
  const mouth=()=>{if(this.active!==turn)return;const rms=envelope[Math.floor(media.currentTime*sentence.rate/frameSize)]||0;this.emit({type:'mouth',level:media.paused?0:Math.min(1,Math.max(0,(rms-.003)/(peak*.65)))});mouthFrame=requestAnimationFrame(mouth);};
  media.playbackRate=turn.rate;
  let finish=()=>{},released=false;
  const source={stop:()=>{if(mouthFrame!==undefined)cancelAnimationFrame(mouthFrame);this.emit({type:'mouth',level:0});media.onended=null;media.onerror=null;media.onplaying=null;media.pause();media.removeAttribute('src');media.load();URL.revokeObjectURL(url);released=true;finish();},disconnect(){}};
  turn.playing.add(source);
  turn.nativeQueue=turn.nativeQueue.then(()=>new Promise(resolve=>{
   finish=resolve;if(released||this.active!==turn){resolve();return;}
   media.onplaying=()=>{if(this.active===turn){this.emit({type:'caption',text:sentence.text});this.emit({type:'speaking'});if(typeof requestAnimationFrame==='function')mouth();}};
   media.onended=()=>{sentence.end=this.ctx.currentTime;turn.playing.delete(source);source.stop();this.complete(turn);};
   media.onerror=()=>{if(this.active===turn){this.stop();this.emit({type:'error',message:'Native voice playback failed: '+(media.error?.message||'unknown')+' ('+(media.error?.code||0)+')'});}resolve();};
   media.play().catch(error=>{if(this.active===turn){this.stop();this.emit({type:'error',message:'Native voice playback failed: '+error.name+' '+error.message});}resolve();});
  }));
 }
 async submit(text='',extra={}){this.stop();const epoch=this.epoch;await this.audio();if(epoch!==this.epoch)return;const turn={id:extra.id||crypto.randomUUID(),abort:new AbortController(),playing:new Set(),timers:new Set(),sentences:new Map(),next:this.ctx.currentTime,done:false,rate:1,audition:extra.audition,native:!!globalThis.webkit?.messageHandlers?.milo,nativeQueue:Promise.resolve(),streaming:!!globalThis.miloPcmStreaming};this.active=turn;if(turn.streaming){globalThis.miloNativeAudio=event=>this.nativeProgress(event);turn.nativeSource={stop:()=>{this.pcm(turn,'pcm-stop');this.emit({type:'mouth',level:0});},disconnect(){}};turn.playing.add(turn.nativeSource);this.pcm(turn,'pcm-start');}this.emit({type:'working'});try{const response=await fetch('/api/demo/turn',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:turn.id,...(text?{text}:{}),model:this.model,voice:this.voice,messages:this.history,sources:this.sources,document:this.document,backend:this.backend,mode:this.mode,rate:this.playbackRate,pitch:this.pitch,sound:this.soundProfile,...extra}),signal:turn.abort.signal});if(!response.ok){const data=await response.json();throw Error(data.error||'Request failed.');}const reader=response.body.getReader(),decoder=new TextDecoder();let pending='';while(true){const row=await reader.read();if(this.active!==turn){reader.cancel();return;}if(row.done)break;pending+=decoder.decode(row.value,{stream:true});let nl;while((nl=pending.indexOf('\n'))>=0){const line=pending.slice(0,nl);pending=pending.slice(nl+1);if(!line)continue;const event=JSON.parse(line);if(this.active!==turn||event.id!==turn.id)return;if(event.type==='transcript')this.history.push({role:'user',content:event.text});if(event.type==='sentence')turn.sentences.set(event.index,{text:event.text,end:Infinity,complete:false,caption:false});if(event.type==='audio'){this.chunk(turn,event);continue;}if(event.type==='sentence_end'){turn.sentences.get(event.index).complete=true;if(turn.streaming)this.pcm(turn,'pcm-sentence-end',{index:event.index});else if(turn.native)this.nativeSentence(turn,event.index);}if(event.type==='sources')this.sources=event.sources;if(event.type==='searching'||event.type==='search_failed')this.sources=[];if(event.type==='draft')turn.action='Draft prepared for review, not saved: '+event.proposal.name+'. '+event.proposal.content.slice(0,2800);if(event.type==='action')turn.action=event.result.title+': '+event.result.detail;if(event.type==='error')throw Error(event.message);if(event.type==='done'){turn.done=true;if(turn.streaming)this.pcm(turn,'pcm-end');}this.emit(event);if(turn.done)this.complete(turn);}}
 if(this.active===turn&&!turn.done)throw Error('The reply ended early. Try again.');
 }catch(error){if(error.name==='AbortError'||this.active!==turn)return;this.stop();this.emit({type:'error',message:error.message});}}
 async record(){
  this.stop();this.cancelRecording();const epoch=this.micEpoch;
  this.recordWanted=true;this.recordId=crypto.randomUUID();this.parts=[];this.recordSamples=0;
  try{
   await this.audio();await this.ctx.audioWorklet.addModule('/capture.js');
   if(!this.recordWanted||epoch!==this.micEpoch)return;
   const media=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
   if(!this.recordWanted||epoch!==this.micEpoch){media.getTracks().forEach(t=>t.stop());return;}
   this.media=media;this.input=this.ctx.createMediaStreamSource(media);this.capture=new AudioWorkletNode(this.ctx,'milo-capture');
   this.silent=this.ctx.createGain();this.silent.gain.value=0;this.input.connect(this.capture);this.capture.connect(this.silent);this.silent.connect(this.ctx.destination);
   this.nextPartial=this.ctx.sampleRate*1.5;
   this.capture.port.onmessage=event=>{
    if(!this.recordWanted||epoch!==this.micEpoch)return;
    this.parts.push(event.data);this.recordSamples+=event.data.length;
    // Schedule against captured audio, including ticks skipped while a request is busy.
    if(this.recordSamples>=this.nextPartial){
     do{this.nextPartial+=this.ctx.sampleRate*1.2;}while(this.recordSamples>=this.nextPartial);
     this.partialRecording(epoch);
    }
   };
   this.recordTimer=setTimeout(()=>this.finishRecording(),29000);this.emit({type:'listening'});
  }catch(error){if(!this.recordWanted||epoch!==this.micEpoch)return;this.cancelRecording();this.emit({type:'error',message:'Microphone unavailable: '+error.message});}
 }
 async partialRecording(epoch){
  if(!this.recordWanted||epoch!==this.micEpoch||this.partialAbort)return;
  const abort=new AbortController();this.partialAbort=abort;
  try{
   const response=await fetch('/api/demo/partial',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:this.recordId,audio:encodeWav(this.parts,this.ctx.sampleRate)}),signal:abort.signal});
   if(!response.ok)return;
   const result=await response.json();
   if(this.recordWanted&&epoch===this.micEpoch&&typeof result.text==='string')this.emit({type:'partial',text:result.text});
  }catch(error){/* A partial failure leaves the final transcription available. */}
  finally{if(this.partialAbort===abort)this.partialAbort=null;}
 }
 cancelRecording(){this.micEpoch++;clearTimeout(this.recordTimer);this.recordWanted=false;if(this.partialAbort)this.partialAbort.abort();if(this.media)this.media.getTracks().forEach(track=>track.stop());for(const name of ['input','capture','silent']){if(this[name])this[name].disconnect();this[name]=null;}this.media=null;}
 finishRecording(){if(!this.recordWanted)return;const parts=this.parts||[],rate=this.ctx?.sampleRate||48000,id=this.recordId;this.cancelRecording();if(parts.reduce((n,a)=>n+a.length,0)<rate*.18){this.emit({type:'idle'});return;}this.submit('',{id,audio:encodeWav(parts,rate)});}
 clear(){this.stop();this.history=[];this.sources=[];this.document=null;}
}
function encodeWav(parts,rate){const length=parts.reduce((n,a)=>n+a.length,0),joined=new Float32Array(length);let pos=0;for(const p of parts){joined.set(p,pos);pos+=p.length;}const ratio=rate/16000,count=Math.min(480000,Math.floor(length/ratio)),bytes=new Uint8Array(44+count*2),v=new DataView(bytes.buffer);const str=(at,text)=>{for(let i=0;i<text.length;i++)v.setUint8(at+i,text.charCodeAt(i));};str(0,'RIFF');v.setUint32(4,36+count*2,true);str(8,'WAVE');str(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,16000,true);v.setUint32(28,32000,true);v.setUint16(32,2,true);v.setUint16(34,16,true);str(36,'data');v.setUint32(40,count*2,true);for(let i=0;i<count;i++){const lo=Math.floor(i*ratio),hi=Math.min(length,Math.max(lo+1,Math.floor((i+1)*ratio)));let n=0;for(let j=lo;j<hi;j++)n+=joined[j];const x=Math.max(-1,Math.min(1,n/(hi-lo)));v.setInt16(44+2*i,x*(x<0?32768:32767),true);}let encoded='';for(let i=0;i<bytes.length;i+=8192)encoded+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(encoded);}
