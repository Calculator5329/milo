/* One local preference record shared by the browser and native WebKit client. */
import '/themes.js';
export function syncPreferences({client,onUpdated=()=>{}}){
 let applying=false,generation=0,pending=0,chain=Promise.resolve(),stopped=false,lastError='';
 function report(error){if(lastError===error.message)return;lastError=error.message;window.dispatchEvent(new CustomEvent('milo-preferences-error',{detail:{message:error.message}}));}
 async function api(body){const response=await fetch('/api/demo/preferences',body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const data=await response.json();if(!response.ok)throw Error(data.error||'Preferences could not be saved.');lastError='';return data;}
 function apply(value){applying=true;try{client.voice=value.voice;client.mode=value.mode;if(value.model)client.model=value.model;client.setSoundProfile(value.sound);client.setPlaybackRate(value.rate);client.setPitch(value.pitch??0);if(window.miloThemes&&window.miloThemes.current!==value.theme)window.miloThemes.setTheme(value.theme,{announce:false});onUpdated(value);}finally{applying=false;}}
 async function load(){const before=generation;if(stopped||pending)return;try{const value=await api();if(!stopped&&!pending&&before===generation)apply(value);}catch(error){report(error);}}
 function save(patch){if(applying||stopped)return Promise.resolve();const at=++generation;pending++;chain=chain.catch(()=>{}).then(()=>api(patch)).then(value=>{if(at===generation&&!stopped)apply(value);}).catch(report).finally(()=>pending--);return chain;}
 const voice=event=>{if(applying)return;const values=event.detail||{},patch={};for(const key of ['voice','mode','sound','rate','pitch','model'])if(values[key]!==undefined)patch[key]=['rate','pitch'].includes(key)?Number(values[key]):values[key];if(Object.keys(patch).length)void save(patch);};
 const theme=event=>{if(!applying)void save({theme:event.detail.id});};
 const visibility=()=>{if(!document.hidden)void load();};
 window.addEventListener('milo-preference-change',voice);window.addEventListener('milo-theme-change',theme);document.addEventListener('visibilitychange',visibility);
 const timer=setInterval(()=>{if(!document.hidden)void load();},5000);void load();
 return {save,load,destroy(){stopped=true;clearInterval(timer);window.removeEventListener('milo-preference-change',voice);window.removeEventListener('milo-theme-change',theme);document.removeEventListener('visibilitychange',visibility);}};
}
