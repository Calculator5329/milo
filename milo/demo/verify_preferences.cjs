// Existing local demo only. Separate browser contexts model independent clients.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
(async()=>{
 const browser=await chromium.launch({headless:true});
 const a=await browser.newContext({viewport:{width:1320,height:1000}}),b=await browser.newContext({viewport:{width:400,height:560}});
 const base='http://127.0.0.1:8776',original=await (await a.request.get(base+'/api/demo/preferences')).json();
 const errors=[];let page;
 try{
  page=await a.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/demo?tab=voice');
  await page.locator('[name="voice-lab-voice"][value="'+original.voice+'"]').locator('..').click();
  await page.locator('[name="voice-lab-voice"][value="javert"]').locator('..').click();
  await page.locator('[name="voice-lab-mode"][value="precise"]').locator('..').click();
  await page.locator('[name="voice-lab-sound"][value="clear"]').locator('..').click();
  await page.locator('[name="voice-lab-rate"][value="1.04"]').locator('..').click();
  await page.locator('.milo-theme-picker summary').click();
  await page.locator('[data-theme-choice="moonroom"]').click();
  await page.waitForFunction(async()=>{const p=await(await fetch('/api/demo/preferences')).json();return p.voice==='javert'&&p.mode==='precise'&&p.sound==='clear'&&p.rate===1.04&&p.theme==='moonroom';});
  const other=await b.newPage();other.on('pageerror',e=>errors.push(e.message));await other.goto(base+'/overlay');
  await other.waitForFunction(()=>document.documentElement.dataset.miloTheme==='moonroom');
  await other.locator('#robot').click();await other.locator('.milo-theme-picker summary').click();await other.locator('[data-theme-choice="library"]').click();
  await page.waitForFunction(()=>document.documentElement.dataset.miloTheme==='library',{},{timeout:10000});
  await page.locator('#voice').screenshot({path:path.join(__dirname,'evidence','voice-shared.png')});
  await page.getByRole('button',{name:'02 · A working notebook'}).click();
  const name='browser-import-'+Date.now()+'.md',content='# Browser import\n\nSynthetic transfer verification.\n';
  await page.locator('#fileTransfer input[type=file]').setInputFiles({name,mimeType:'text/markdown',buffer:Buffer.from(content)});
  await page.locator('.file-transfer-status').filter({hasText:'Imported '+name}).waitFor();
  assert.equal(await page.locator('#documentEditor').inputValue(),content);
  const downloadPromise=page.waitForEvent('download');await page.locator('.file-transfer-download').click();const download=await downloadPromise;
  assert.equal(download.suggestedFilename(),name);assert.equal(fs.readFileSync(await download.path(),'utf8'),content);
  await page.locator('#workbench').screenshot({path:path.join(__dirname,'evidence','notebook-transfer.png')});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(__dirname,'evidence','preferences-browser.json'),JSON.stringify({passed:true,separate_browser_contexts:true,server_preferences_loaded_by_new_client:true,bidirectional_theme_sync:true,voice_style_sound_rate_saved:true,utf8_import_download_equal:true,synthetic_note:name,errors},null,2));
  console.log('Shared preferences and file transfer passed.');
 }finally{await a.request.post(base+'/api/demo/preferences',{data:original});await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
