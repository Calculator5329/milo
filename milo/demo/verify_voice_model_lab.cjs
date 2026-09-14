const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1320,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 // Isolate preference writes so QA does not overwrite the owner's selections.
 let preferences={voice:'marius',mode:'conversational',sound:'natural',rate:1,pitch:0,theme:'hifi',revision:'qa'};
 await page.route('**/api/demo/preferences',async route=>{if(route.request().method()==='POST')preferences={...preferences,...route.request().postDataJSON()};await route.fulfill({json:preferences});});
 const dir=path.join(__dirname,'evidence','voice-model-lab');fs.mkdirSync(dir,{recursive:true});
 await page.goto('http://127.0.0.1:8776/demo?tab=voice&theme=hifi');
 await page.locator('[name="voice-lab-voice"]').last().waitFor();assert.equal(await page.locator('[name="voice-lab-voice"]').count(),12);
 await page.locator('[name="voice-lab-rate"]').fill('1.17');await page.locator('[name="voice-lab-rate"]').dispatchEvent('change');
 await page.locator('[name="voice-lab-pitch"]').fill('-2.5');await page.locator('[name="voice-lab-pitch"]').dispatchEvent('change');
 await page.waitForFunction(()=>document.querySelector('[data-value="pitch"]').textContent.includes('-2.5'));
 await page.waitForTimeout(250);assert.equal(preferences.rate,1.17);assert.equal(preferences.pitch,-2.5);
 await page.screenshot({path:path.join(dir,'voice-desktop.png'),fullPage:true});
 await page.goto('http://127.0.0.1:8776/demo?tab=routing&theme=hifi');await page.locator('[data-run]').waitFor();
 await page.waitForFunction(()=>document.querySelectorAll('[data-models] input').length===4);
 assert.equal(await page.locator('[data-models] input:checked').count(),3);assert(await page.locator('[data-models] input[value=api]').isDisabled());
 for(const level of ['simple','moderate','hard']){await page.locator('[data-level='+level+']').click();assert.equal(await page.locator('[data-question] option').count(),3);}
 await page.locator('[data-level=simple]').click();await page.locator('[data-question]').selectOption('1');await page.locator('[data-run]').click();
 await page.waitForFunction(()=>document.querySelector('[data-status]').textContent.startsWith('Comparison complete.'),{},{timeout:240000});
 const results=JSON.parse(await page.evaluate(()=>localStorage.getItem('milo-last-model-comparison')));assert.equal(results.results.length,3);assert(results.results.every(r=>r.status==='complete'),JSON.stringify(results));
 fs.writeFileSync(path.join(dir,'model-responses.json'),JSON.stringify(results,null,2)+'\n');
 await page.screenshot({path:path.join(dir,'models-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(dir,'models-mobile.png'),fullPage:true});
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);assert.equal(overflow,false,'Model page overflow');
 await page.goto('http://127.0.0.1:8776/demo?tab=voice&theme=hifi');await page.locator('[name="voice-lab-pitch"]').waitFor();await page.screenshot({path:path.join(dir,'voice-mobile.png'),fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Voice page overflow');
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(dir,'browser.json'),JSON.stringify({passed:true,voices:12,independentSavedControls:preferences,modelResponses:results.results.map(r=>({model:r.model,status:r.status,elapsed_ms:r.elapsed_ms})),mobileOverflow:overflow,errors},null,2)+'\n');
 await browser.close();console.log('PASS: 12 voices, pitch and pace controls, three real model responses, mobile layouts');
})().catch(e=>{console.error(e);process.exitCode=1;});
