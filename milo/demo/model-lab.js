/* Real text comparisons, one model at a time; never substitute sample answers. */
export async function mountModelLab(root){
 root.innerHTML=`<div class="model-lab"><div class="model-lab-controls"><div class="level-picker" role="group" aria-label="Question difficulty"><button data-level="simple" aria-pressed="true">01 · Simple</button><button data-level="moderate" aria-pressed="false">02 · Moderate</button><button data-level="hard" aria-pressed="false">03 · Hard</button></div><label class="model-question-label">Try a test question<select data-question aria-label="Test question"></select></label><textarea data-prompt aria-label="Comparison question" maxlength="2000" rows="4"></textarea><p data-guidance class="small-note"></p><div data-models class="model-choices"></div><label class="model-style">Answer style <select data-mode><option value="conversational">Conversational</option><option value="precise">Precise</option><option value="brainstorm">Brainstorm</option></select></label><div class="model-run-actions"><button class="primary" data-run disabled>Compare answers</button><button data-stop disabled>Stop</button><span data-status role="status">Loading installed models…</span></div><p class="small-note">Runs selected models in sequence. Times include loading and waiting; they are not quality scores. Cloud receives only this question when selected.</p></div><div data-results hidden><div class="comparison-heading"><h3>Compared question</h3><button data-export>Download this comparison</button></div><p data-tested></p><div data-cards class="model-results"></div></div></div>`;
 const $=s=>root.querySelector(s),modelsBox=$('[data-models]'),question=$('[data-question]'),prompt=$('[data-prompt]'),status=$('[data-status]');
 let catalog,level='simple',running=false,currentId=null,stopRequested=false,receipt=null;
 async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const value=await r.json();if(!r.ok)throw Error(value.error||'Comparison unavailable.');return value;}
 function samples(){question.replaceChildren(...catalog.questions[level].map((row,i)=>new Option(row.question,String(i))));selectQuestion();}
 function selectQuestion(){const row=catalog.questions[level][Number(question.value)];prompt.value=row.question;$('[data-guidance]').textContent='Look for: '+row.look_for;}
 function setBusy(value){running=value;$('[data-run]').disabled=value;$('[data-stop]').disabled=!value;for(const element of root.querySelectorAll('[data-level], [data-question], [data-prompt], [data-mode], [data-models] input'))element.disabled=value||element.dataset.unavailable==='true';}
 for(const button of root.querySelectorAll('[data-level]'))button.onclick=()=>{if(!catalog)return;level=button.dataset.level;for(const b of root.querySelectorAll('[data-level]'))b.setAttribute('aria-pressed',String(b===button));samples();};
 question.onchange=selectQuestion;prompt.oninput=()=>{$('[data-guidance]').textContent='Custom question · compare correctness, useful detail, and uncertainty.';};
 try{
  catalog=await api('/api/demo/comparison');
  for(const model of catalog.models){const label=document.createElement('label'),input=document.createElement('input'),text=document.createElement('span'),title=document.createElement('strong'),detail=document.createElement('small');input.type='checkbox';input.value=model.id;input.checked=model.available&&model.kind==='local';input.disabled=!model.available;input.dataset.unavailable=String(!model.available);title.textContent=model.label;detail.textContent=model.available?(model.kind==='local'?'Installed locally':'Cloud · provider charges may apply'):(model.detail||'Not installed');text.append(title,detail);label.append(input,text);modelsBox.append(label);}
  samples();$('[data-run]').disabled=false;status.textContent=catalog.error||'Choose a question and compare real answers.';
 }catch(error){status.textContent=error.message;return;}
 async function stop(){stopRequested=true;if(currentId)await api('/api/cancel',{id:currentId}).catch(()=>{});status.textContent='Stopping the current model…';}
 $('[data-stop]').onclick=stop;
 window.addEventListener('pagehide',()=>{stopRequested=true;if(currentId)fetch('/api/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:currentId}),keepalive:true}).catch(()=>{});});
 $('[data-run]').onclick=async()=>{
  if(running)return;const ids=[...modelsBox.querySelectorAll('input:checked')].map(i=>i.value),text=prompt.value.trim();
  if(!text||!ids.length){status.textContent='Enter a question and select at least one available model.';return;}
  stopRequested=false;setBusy(true);receipt={question:text,level,mode:$('[data-mode]').value,started_at:new Date().toISOString(),results:[]};
  $('[data-results]').hidden=false;$('[data-tested]').textContent=text;$('[data-cards]').replaceChildren();
  const cards=ids.map(id=>{const model=catalog.models.find(m=>m.id===id),card=document.createElement('article'),heading=document.createElement('h4'),meta=document.createElement('p'),answer=document.createElement('div');heading.textContent=model.label;meta.className='model-meta';meta.textContent='Waiting';answer.className='model-answer';card.append(heading,meta,answer);$('[data-cards]').append(card);return {id,model,card,meta,answer};});
  try{for(const entry of cards){
   if(stopRequested){entry.meta.textContent='Not run · stopped';continue;}
   currentId=crypto.randomUUID();status.textContent='Asking '+entry.model.label+'…';entry.meta.textContent='Generating…';entry.card.dataset.state='running';
   try{const result=await api('/api/demo/compare',{id:currentId,question:text,model:entry.id,mode:receipt.mode});receipt.results.push(result);entry.card.dataset.state=result.status;entry.answer.textContent=result.answer||result.error||'No answer returned.';entry.meta.textContent=result.status==='complete'?(result.elapsed_ms/1000).toFixed(1)+' seconds · completed':result.status==='cancelled'?'Cancelled · partial answer':'Failed · no completed answer';if(result.status==='cancelled')stopRequested=true;}
   catch(error){entry.card.dataset.state='failed';entry.meta.textContent='Failed';entry.answer.textContent=error.message;receipt.results.push({model:entry.id,status:'failed',error:error.message});}
   finally{currentId=null;}
  }}finally{setBusy(false);status.textContent=stopRequested?'Comparison stopped. Completed answers remain visible.':'Comparison complete. Which answer would you want from Milo?';receipt.finished_at=new Date().toISOString();try{localStorage.setItem('milo-last-model-comparison',JSON.stringify(receipt));}catch{}}
 };
 $('[data-export]').onclick=()=>{if(!receipt)return;const url=URL.createObjectURL(new Blob([JSON.stringify(receipt,null,2)+'\n'],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='milo-model-comparison.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
}
