"""The local models Milo offers, and which of them answer without refusals."""
MODEL = 'gemma4:12b'
MODELS = {'gemma4:12b': 'Quick · Gemma 4', 'gpt-oss:20b': 'Careful · GPT OSS', 'phi4:latest': 'Balanced · Phi-4',
          'gemma3-abliterated:12b-q4': 'Unfiltered · Gemma 3 abliterated'}
# Models whose refusals were removed on purpose: the prompt drops disclaimers when one is selected.
UNFILTERED_MODELS = ('gemma3-abliterated',)


def unfiltered_model(model):
    return isinstance(model, str) and model.startswith(UNFILTERED_MODELS)
