"""One-time online step: cache the Pocket TTS model and the five preset voices into models_dir.

After this runs, Milo starts with Hugging Face offline mode on; speech never touches the network.
Run it with the project interpreter: `python milo.py setup-voices` (docs/setup.md).
It writes only into models_dir and never records a microphone.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from milo import config  # noqa: E402

settings = config.load()
root = Path(settings['models_dir'])
root.mkdir(parents=True, exist_ok=True)
os.environ['HF_HOME'] = str(root)

import torch  # noqa: E402
from pocket_tts import TTSModel  # noqa: E402
from pocket_tts.models.model_state import export_model_state  # noqa: E402
from scipy.io.wavfile import write  # noqa: E402

torch.set_num_threads(int(settings['tts_threads']))
model = TTSModel.load_model()
for name in settings['voices']:
    target = root / (name + '.safetensors')
    if target.is_file():
        print('Already cached:', name, flush=True)
        continue
    export_model_state(model.get_state_for_audio_prompt(name), target)
    print('Cached voice:', name, flush=True)

# A synthetic greeting used by tests/latency_probe.py as the "microphone" input. Not a recording.
fixture = root / 'milo-hello.wav'
if not fixture.is_file():
    voice = model.get_state_for_audio_prompt(str(root / (settings['voice'] + '.safetensors')))
    who = settings['user_name'].strip()
    line = (f'Hi {who}. ' if who else 'Hi. ') + 'I am Milo. What would you like to talk about?'
    write(str(fixture), model.sample_rate, model.generate_audio(voice, line).numpy())
    print('Wrote fixture:', fixture, flush=True)
print('Voices ready in', root)
