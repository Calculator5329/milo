const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const audio=[],revoked=[],frames=new Map();let frameId=0;
class Media{constructor(url){this.currentTime=0;this.paused=false;this.url=url;audio.push(this);}play(){this.started=true;this.onplaying?.();return Promise.resolve();}pause(){this.paused=true;}removeAttribute(){}load(){}}
const box={requestAnimationFrame:fn=>{frames.set(++frameId,fn);return frameId;},cancelAnimationFrame:id=>frames.delete(id),setTimeout,clearTimeout,Audio:Media,Blob,URL:{createObjectURL:()=>String(audio.length),revokeObjectURL:u=>revoked.push(u)},fetch:()=>Promise.resolve({})};
vm.createContext(box);vm.runInContext(fs.readFileSync(__dirname+'/client.js','utf8').replace('export class MiloClient','class MiloClient')+';globalThis.Client=MiloClient;',box);
(async()=>{
 const events=[],client=new box.Client(e=>events.push(e));client.ctx={currentTime:1};
 const turn={playing:new Set(),timers:new Set(),sentences:new Map(),nativeQueue:Promise.resolve(),rate:1,done:true,abort:{abort(){}},id:'test',audition:true};
 client.active=turn;
 for(let i=0;i<2;i++){turn.sentences.set(i,{parts:[Float32Array.from({length:960},(_,j)=>j<480?0:.1)],rate:24000,text:'Sentence '+i,complete:true,end:Infinity});client.nativeSentence(turn,i);}
 await new Promise(r=>setImmediate(r));assert(audio[0].started);assert(!audio[1].started,'Sentences must not overlap');
 assert.equal(events.filter(e=>e.type==='mouth').at(-1).level,0,'Silence closes mouth');audio[0].currentTime=.025;const [id,tick]=frames.entries().next().value;frames.delete(id);tick();assert(events.filter(e=>e.type==='mouth').at(-1).level>.5,'Playback position in voiced PCM opens mouth');
 audio[0].onended();await new Promise(r=>setImmediate(r));assert(audio[1].started);assert.equal(events.filter(e=>e.type==='caption').length,2);
 client.stop();assert(audio[1].paused);assert.equal(client.active,null);assert.equal(revoked.length,2);assert.equal(frames.size,0);assert.equal(events.filter(e=>e.type==='mouth').at(-1).level,0);
 console.log('PASS: native playback order, mouth follows voiced/silent PCM at playback position, and Stop cancels mouth frames');
})().catch(e=>{console.error(e);process.exitCode=1;});
