const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const sent=[],events=[];
const box={setTimeout,clearTimeout,AbortController,TextDecoder,crypto:{randomUUID:()=> 'turn'},miloPcmStreaming:true,webkit:{messageHandlers:{milo:{postMessage:s=>sent.push(JSON.parse(s))}}}};
const rows=[{type:'sentence',index:0,text:'Hello.'},{type:'audio',index:0,sample_rate:24000,pcm:'AAAAAA=='},{type:'sentence_end',index:0},{type:'done'}];
box.fetch=async()=>({ok:true,body:{getReader(){let count=0;return {async read(){return count++?{done:true}:{value:new TextEncoder().encode(rows.map(r=>JSON.stringify({...r,id:'turn'})).join('\n')+'\n')};}}}}});
vm.createContext(box);vm.runInContext(fs.readFileSync(__dirname+'/client.js','utf8').replace('export class MiloClient','class MiloClient')+';globalThis.Client=MiloClient;',box);
(async()=>{
 const client=new box.Client(e=>events.push(e));client.ctx={currentTime:5};client.audio=async()=>{};
 await client.submit('Hello');
 assert.deepEqual(sent.map(x=>x.action),['pcm-start','pcm-audio','pcm-sentence-end','pcm-end']);
 assert(client.active,'Server done must wait for native audio to drain');
 client.nativeProgress({id:'stale',type:'drained'});assert(client.active);
 client.nativeProgress({id:'turn',type:'position',index:0,level:.7});assert(events.some(e=>e.type==='mouth'&&e.level===.7));
 client.nativeProgress({id:'turn',type:'drained',completed:[0]});assert.equal(client.active,null);assert.equal(client.history.at(-1).content,'Hello.');
 await client.submit('Hello');client.stop();assert.equal(sent.at(-1).action,'pcm-stop');
 client.nativeProgress({id:'turn',type:'drained',completed:[0]});assert.equal(client.active,null);
 console.log('PASS: incremental bridge delivery, native drain controls completion/history, stale events ignored, Stop forwarded');
})().catch(error=>{console.error(error);process.exitCode=1;});
