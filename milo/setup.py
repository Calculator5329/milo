"""One-time online setup: cache official preset states; runtime stays offline for speech."""
import os
from pathlib import Path
os.environ.setdefault('HF_HOME',str(Path.home()/'.cache/tmp/milo-models'))
import torch
from pocket_tts import TTSModel
from pocket_tts.models.model_state import export_model_state
from scipy.io.wavfile import write

torch.set_num_threads(2)
root=Path(os.environ['HF_HOME']);root.mkdir(parents=True,exist_ok=True)
model=TTSModel.load_model()
from voice_catalog import VOICE_PRESETS
for name in VOICE_PRESETS:
    voice=model.get_state_for_audio_prompt(name)
    export_model_state(voice,root/(name+'.safetensors'))
    print('Cached preset:',name,flush=True)
# Synthetic fixture only; ordinary user speech is never saved.
voice=model.get_state_for_audio_prompt(str(root/'alba.safetensors'))
write(str(root/'milo-hello.wav'),model.sample_rate,model.generate_audio(voice,'Hi there. I am Milo. What would you like to talk about?').numpy())
