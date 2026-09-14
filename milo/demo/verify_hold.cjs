const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const sandbox={setTimeout,clearTimeout,AudioContext:function(){},navigator:{mediaDevices:{}}};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync(__dirname+'/client.js','utf8').replace('export class MiloClient','class MiloClient')+';globalThis.Client=MiloClient;',sandbox);
(async()=>{
 let unlock,calls=0,events=[];
 const client=new sandbox.Client(e=>events.push(e));
 client.audio=()=>new Promise(resolve=>unlock=resolve);
 client.ctx={audioWorklet:{addModule:async()=>{}},sampleRate:48000};
 sandbox.navigator.mediaDevices.getUserMedia=()=>{calls++;throw Error('unexpected microphone open');};
 const recording=client.record();client.finishRecording();unlock();await recording;
 assert.equal(calls,0,'Release before audio initialization must prevent mic acquisition');
 let reject;
 client.audio=async()=>{};
 sandbox.navigator.mediaDevices.getUserMedia=()=>new Promise((_,r)=>reject=r);
 const pending=client.record();await new Promise(resolve=>setImmediate(resolve));client.finishRecording();reject(Error('late permission failure'));await pending;
 assert.equal(events.filter(e=>e.type==='error').length,0,'Cancelled acquisition must not report a stale error');
 console.log('PASS: early release prevents microphone acquisition; late rejection stays cancelled');
})().catch(e=>{console.error(e);process.exitCode=1;});
