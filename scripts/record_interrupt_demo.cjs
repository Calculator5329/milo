// Record a real interrupted turn on the main page of a running test server.
//
//   python milo.py --port 8767
//   PLAYWRIGHT_MODULE=/path/to/node_modules/playwright node scripts/record_interrupt_demo.cjs
//
// Headless Chromium, no microphone. The question is typed into the page's text box and the
// Stop button is clicked while Milo is speaking, which posts /api/cancel exactly as a person
// would. The robot, status line and captions are the page's own. The only addition is a strip
// along the bottom naming the scripted action, so the silent video can be followed.
// Output: a .webm video and timing.json in OUT (default evidence/local/demo-capture).
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path');
const URL=process.env.MILO_URL||'http://127.0.0.1:8767';
const OUT=process.env.OUT||path.join(__dirname,'..','evidence','local','demo-capture');
const QUESTION='Tell me a slow story about a lighthouse keeper and a lost ship, in about eight sentences.';
const FOLLOW='What was the last thing you told me?';

(async()=>{
  fs.mkdirSync(OUT,{recursive:true});
  const browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required']});
  const context=await browser.newContext({viewport:{width:1000,height:780},recordVideo:{dir:OUT,size:{width:1000,height:780}}});
  const page=await context.newPage();
  const t0=Date.now(),marks=[];
  const mark=async(label)=>{marks.push({ms:Date.now()-t0,label,status:await page.locator('#statusText').textContent(),caption:await page.locator('#caption').textContent()});};
  const strip=(text)=>page.evaluate(t=>{let s=document.getElementById('scripted-strip');if(!s){s=document.createElement('div');s.id='scripted-strip';s.style.cssText='position:fixed;left:0;right:0;bottom:0;padding:6px 12px;font:14px/1.4 monospace;background:#111;color:#eee;z-index:99999';document.body.appendChild(s);}s.textContent='scripted: '+t;},text);
  await page.goto(URL+'/');
  await page.waitForFunction(()=>!document.querySelector('#send').disabled,null,{timeout:60000}).catch(()=>{});
  await strip('page loaded, microphone off, text input only');await mark('loaded');
  await page.waitForTimeout(2500);
  await strip('typing a question');
  await page.locator('#text').pressSequentially(QUESTION,{delay:18});
  await page.locator('#send').click();await mark('sent');
  await strip('question sent');
  await page.locator('#statusText').filter({hasText:'Speaking'}).waitFor({timeout:30000});await mark('speaking');
  await strip('Milo is speaking (audio not captured)');
  await page.waitForTimeout(4500);
  await strip('Stop pressed mid-sentence');
  await page.locator('#stop').click();await mark('stop clicked');
  await page.waitForTimeout(250);await mark('250 ms after stop');
  await page.waitForTimeout(2500);
  await strip('follow-up: only the heard sentences stay in history');
  await page.locator('#text').pressSequentially(FOLLOW,{delay:18});
  await page.locator('#send').click();await mark('follow-up sent');
  await page.waitForFunction(()=>!/Ready/.test(document.querySelector('#statusText').textContent),null,{timeout:10000}).catch(()=>{});
  await page.waitForFunction(()=>document.querySelector('#stop').disabled&&/Ready/.test(document.querySelector('#statusText').textContent),null,{timeout:45000}).catch(()=>{});
  await mark('follow-up finished');
  await page.waitForTimeout(4000);
  const metrics=await page.locator('#metrics').textContent().catch(()=>null);
  const video=page.video();await context.close();await browser.close();
  const file=await video.path();
  fs.writeFileSync(path.join(OUT,'timing.json'),JSON.stringify({url:URL,video:path.basename(file),question:QUESTION,follow_up:FOLLOW,marks,metrics_panel:metrics},null,2));
  console.log(file);
})().catch(e=>{console.error(e);process.exit(1)});
