"""The local models Milo offers, and which of them answer without refusals."""
import json
import os

DEFAULT_MODEL = 'gemma4:12b'
DEFAULT_MODELS = {
    'gemma4:12b': 'Quick · Gemma 4',
    'gpt-oss:20b': 'Careful · GPT OSS',
    'phi4:latest': 'Balanced · Phi-4',
    'gemma3-abliterated:12b-q4': 'Unfiltered · Gemma 3 abliterated',
}


def models_from_env(env=None):
    """Read a tag-to-label JSON object, falling back safely on invalid input."""
    env = os.environ if env is None else env
    raw = env.get('MILO_MODELS')
    if raw is None:
        return dict(DEFAULT_MODELS)
    try:
        models = json.loads(raw)
    except (TypeError, ValueError):
        return dict(DEFAULT_MODELS)
    if not isinstance(models, dict) or not models or not all(
            isinstance(tag, str) and isinstance(label, str) for tag, label in models.items()):
        return dict(DEFAULT_MODELS)
    return models


MODEL = os.environ.get('MILO_MODEL', DEFAULT_MODEL).strip() or DEFAULT_MODEL
MODELS = models_from_env()
# Models whose refusals were removed on purpose: the prompt drops disclaimers when one is selected.
UNFILTERED_MODELS = ('gemma3-abliterated',)


def unfiltered_model(model):
    return isinstance(model, str) and model.startswith(UNFILTERED_MODELS)
