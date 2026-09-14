import {VOICE_PRESETS} from '/voice-catalog.js';
const VOICES=Object.entries(VOICE_PRESETS).map(([id,label])=>[id,label,'Official Pocket TTS preset']);
const MODES = [
  ['conversational', 'Conversational', 'Short, natural discussion'],
  ['precise', 'Precise', 'Conclusion, constraint, then detail'],
  ['brainstorm', 'Brainstorm', 'Distinct ideas and a recommendation'],
];
const SOUNDS = [
  ['natural', 'Natural', 'Unfiltered voice'],
  ['clear', 'Clear', 'Light presence for small speakers'],
  ['small', 'Small speaker', 'Narrower, more mechanical color'],
];
export const VOICE_LAB_AUDITION_TEXT = 'Hey there. I am Milo. Give me something to figure out. Big questions, small robot. We can make that work.';

const STORAGE = {
  voice: 'milo-demo-voice',
  mode: 'milo-demo-mode',
  sound: 'milo-demo-sound',
  rate: 'milo-demo-rate',
  pitch: 'milo-demo-pitch',
};

function choices(name, rows) {
  return rows.map(([value, label, note]) => `<label class="voice-choice">
    <input type="radio" name="${name}" value="${value}">
    <span><strong>${label}</strong><small>${note}</small></span>
  </label>`).join('');
}

function stored(key, allowed, fallback) {
  try {
    const value = localStorage.getItem(STORAGE[key]);
    return allowed.includes(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

function persist(key, value) {
  try { localStorage.setItem(STORAGE[key], value); } catch {}
}

export function mountVoiceLab(root, client) {
  if (!(root instanceof Element)) throw new TypeError('Voice Lab needs a mount element.');
  if (!client || typeof client.submit !== 'function' || typeof client.stop !== 'function') {
    throw new TypeError('Voice Lab needs a Milo client.');
  }

  root.innerHTML = `<div class="voice-lab-shell">
    <div class="voice-lab-stage">
      <div class="voice-lab-robot" aria-hidden="true"><img src="/robot.svg" alt=""></div>
      <p class="eyebrow">SAME WORDS · DIFFERENT DELIVERY</p>
      <blockquote>${VOICE_LAB_AUDITION_TEXT}</blockquote>
      <div class="voice-lab-actions">
        <button type="button" class="primary" data-audition>Hear this combination</button>
        <button type="button" data-stop disabled>Stop</button>
      </div>
      <p class="voice-lab-status" role="status" aria-live="polite">Ready for a human listen.</p>
      <div class="listen-for" aria-label="Audition guidance">
        <strong>Listen for fit, intelligibility, and fatigue.</strong>
        <span>This preference is saved on this computer and shared by Milo windows. It does not produce a voice score.</span>
      </div>
    </div>
    <div class="voice-lab-controls">
      <fieldset class="voice-lab-voices"><legend>Voice · ${VOICES.length} options</legend>${choices('voice-lab-voice', VOICES)}</fieldset>
      <div class="voice-lab-small-controls">
        <fieldset><legend>Conversation style</legend>${choices('voice-lab-mode', MODES)}</fieldset>
        <fieldset><legend>Sound</legend>${choices('voice-lab-sound', SOUNDS)}</fieldset>
        <fieldset class="voice-slider"><legend>Pace <output data-value="rate">1.00×</output></legend><input aria-label="Speaking pace" type="range" name="voice-lab-rate" min="0.65" max="2" step="0.01" value="1"><div class="slider-ends"><span>Unhurried · 0.65×</span><span>Fast · 2.00×</span></div></fieldset>
        <fieldset class="voice-slider"><legend>Pitch <output data-value="pitch">0 semitones</output></legend><input aria-label="Voice pitch" type="range" name="voice-lab-pitch" min="-8" max="8" step="0.5" value="0"><div class="slider-ends"><span>Lower · −8</span><span>Higher · +8</span></div><button type="button" data-reset-delivery>Reset pitch & pace</button></fieldset>
      </div>
    </div>
  </div>`;

  const audition = root.querySelector('[data-audition]');
  const stop = root.querySelector('[data-stop]');
  const status = root.querySelector('.voice-lab-status');
  let auditioning = false;

  const selected = {
    voice: stored('voice', VOICES.map(row => row[0]), client.voice || 'marius'),
    mode: stored('mode', MODES.map(row => row[0]), client.mode || 'conversational'),
    sound: stored('sound', SOUNDS.map(row => row[0]), client.soundProfile || 'natural'),
    rate: String(client.playbackRate || 1),
    pitch: String(client.pitch || 0),
  };

  function setStatus(text) { status.textContent = text; }
  function setBusy(busy) {
    audition.disabled = busy;
    stop.disabled = !busy;
    root.dataset.state = busy ? 'playing' : 'ready';
  }
  function apply(key, value, save = true) {
    if (auditioning) client.stop();
    auditioning = false;
    setBusy(false);
    selected[key] = value;
    if (key === 'voice') client.voice = value;
    if (key === 'mode') client.mode = value;
    if (key === 'sound') client.setSoundProfile(value);
    if (key === 'rate') client.setPlaybackRate(Number(value));
    if (key === 'pitch') client.setPitch(Number(value));
    if (['rate','pitch'].includes(key)){const slider=root.querySelector(`[name="voice-lab-${key}"]`);slider.value=value;showSlider(key,value);}
    if (save) persist(key, value);
    const input = root.querySelector(`input[name="voice-lab-${key}"][value="${CSS.escape(value)}"]`);
    if (input) input.checked = true;
    setStatus('Ready. Hear the same line with this combination.');
    root.dispatchEvent(new CustomEvent('milo-preference-change', {
      bubbles: true,
      detail: {...selected},
    }));
  }

  for (const [key, rows] of Object.entries({voice: VOICES, mode: MODES, sound: SOUNDS})) {
    for (const input of root.querySelectorAll(`input[name="voice-lab-${key}"]`)) {
      input.addEventListener('change', () => {
        if (input.checked && rows.some(row => row[0] === input.value)) apply(key, input.value);
      });
    }
  }

  function showSlider(key,value){root.querySelector(`[data-value="${key}"]`).textContent=key==='rate'?Number(value).toFixed(2)+'×':(Number(value)>0?'+':'')+Number(value)+' semitones';}
  for(const key of ['rate','pitch']){
    const slider=root.querySelector(`[name="voice-lab-${key}"]`);
    slider.addEventListener('input',()=>showSlider(key,slider.value));
    slider.addEventListener('change',()=>apply(key,slider.value));
  }
  root.querySelector('[data-reset-delivery]').onclick=()=>{apply('rate','1');apply('pitch','0');};

  audition.addEventListener('click', async () => {
    if (auditioning) return;
    auditioning = true;
    setBusy(true);
    setStatus('Preparing the fixed audition line…');
    try {
      await client.submit('', {audition: true});
    } catch (error) {
      auditioning = false;
      setBusy(false);
      setStatus(error?.message || 'The audition could not start.');
    }
  });
  stop.addEventListener('click', () => {
    client.stop();
    auditioning = false;
    setBusy(false);
    setStatus('Stopped. Change a choice or listen again.');
  });

  for (const key of ['voice', 'mode', 'sound', 'rate', 'pitch']) apply(key, selected[key], false);

  return {
    setPreferences(values){for(const key of ['voice','mode','sound','rate','pitch']){if(values[key]!==undefined&&String(values[key])!==selected[key])apply(key,String(values[key]),false);}},
    handleEvent(event) {
      if (!auditioning) return;
      if (event.type === 'working') setStatus('Preparing the fixed audition line…');
      if (event.type === 'speaking' || event.type === 'caption') setStatus('Playing the fixed audition line…');
      if (event.type === 'idle') {
        auditioning = false;
        setBusy(false);
        setStatus('Finished. Your choice is retained locally.');
      }
      if (event.type === 'stopped') {
        auditioning = false;
        setBusy(false);
        setStatus('Stopped. Change a choice or listen again.');
      }
      if (event.type === 'error') {
        auditioning = false;
        setBusy(false);
        setStatus(event.message || 'The audition failed.');
      }
    },
    values: selected,
  };
}
