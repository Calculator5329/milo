"""Run one text-only model comparison; no document or conversation context."""
import json
import time
import urllib.request
from datetime import datetime, timezone
from conversation import PROFILES, ollama_model_options
from providers import Provider, ProviderError

QUESTIONS = {
    'simple': [
        {'question': 'Why does a cold glass get water droplets on the outside?', 'look_for': 'A short, correct explanation that distinguishes condensation from a leaking glass.'},
        {'question': 'I have 18 seedlings and trays with 6 spaces each. How many trays do I need?', 'look_for': 'Correct arithmetic without an unnecessarily long answer.'},
        {'question': 'Give me a friendly one-sentence reminder to take a short break.', 'look_for': 'A natural tone and respect for the one-sentence request.'},
    ],
    'moderate': [
        {'question': 'I have 45 minutes to cook dinner, answer one important email, and tidy my desk. Dinner takes 25 minutes, the email 15, and tidying 10. Suggest a realistic plan and explain what you would cut.', 'look_for': 'Notice that the tasks take 50 minutes; make a clear trade-off rather than pretending everything fits.'},
        {'question': 'Should a small community seed exchange use a shared spreadsheet or build an app? Compare the options and recommend a first step.', 'look_for': 'Practical trade-offs, a recommendation, and sensible uncertainty.'},
        {'question': 'A service gets slower only after running for several hours. Restarting fixes it temporarily. What are three plausible causes and the first measurement you would take?', 'look_for': 'Distinct hypotheses and a useful diagnostic rather than an unsupported diagnosis.'},
    ],
    'hard': [
        {'question': 'Design a voice assistant that can draft changes to files but saves them only after approval. The file may change while the draft is being generated. Explain how you would handle that race, cancellation, and recovery after a crash.', 'look_for': 'Explicit state and version handling, bounded authority, and recovery without silently overwriting newer content.'},
        {'question': 'A test correctly flags 95% of faulty items and incorrectly flags 5% of good items. Only 1% of items are faulty. If an item is flagged, roughly how likely is it to be faulty? Explain using 10,000 items.', 'look_for': 'Base-rate reasoning: about 95 faulty and 495 good items are flagged, so about 16% of flags are truly faulty.'},
        {'question': 'A local model is fast and private but sometimes wrong. A cloud model is stronger but has variable latency and costs money. Propose a routing policy that does not rely on an invented confidence score, and explain how you would evaluate it.', 'look_for': 'Observable routing signals, explicit data-sharing choices, measurable evaluation, and failure handling.'},
    ],
}


def catalog(models, ollama):
    available = set()
    error = None
    try:
        with urllib.request.urlopen(ollama+'/api/tags', timeout=4) as response:
            available = {item['name'] for item in json.load(response).get('models', [])}
    except (OSError, ValueError, KeyError):
        error = 'Could not read installed local models.'
    rows = [{'id': model, 'label': label.split(' · ')[-1], 'available': model in available,
             'kind': 'local'} for model, label in models.items()]
    provider = Provider.configured()
    rows.append({'id': 'api', 'label': provider.model if provider else 'OpenRouter',
                 'available': provider is not None, 'kind': 'cloud',
                 'detail': 'Configured provider · question only' if provider else 'Waiting for your connection'})
    return {'models': rows, 'questions': QUESTIONS, 'error': error}


def run(engine, turn, question, model, mode, models, ollama):
    if model not in models and model != 'api': raise ValueError('Unknown comparison model.')
    if mode not in PROFILES: raise ValueError('Unknown conversation style.')
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise ValueError('Use a question between 1 and 2,000 characters.')
    started = time.monotonic()
    result = {'model': model, 'question': question, 'mode': mode, 'status': 'failed',
              'answer': '', 'created_at': datetime.now(timezone.utc).isoformat()}
    acquired = False
    try:
        while not turn.cancelled.is_set():
            acquired = engine.lock.acquire(timeout=.1)
            if acquired: break
        if not acquired or turn.cancelled.is_set():
            result['status'] = 'cancelled'
            return result
        profile = {'conversational': 'Answer naturally and concisely.',
                   'precise': 'Be precise. State the conclusion, constraints, then supporting detail.',
                   'brainstorm': 'Explore distinct ideas, trade-offs, and a recommendation.'}[mode]
        messages = [{'role': 'system', 'content': 'You are Milo. '+profile+' Answer the question directly. You have no tools or access to files. Do not claim actions. Keep the answer under 250 words.'},
                    {'role': 'user', 'content': question}]
        if model == 'api':
            provider = Provider.configured()
            if provider is None: raise ProviderError('OpenRouter is not connected yet.')
            result['model'] = provider.model
            for text in provider.stream(messages, turn.cancelled, max_tokens=650):
                result['answer'] += text
        else:
            body = {'model': model, 'messages': messages, 'stream': True,
                    **ollama_model_options(model, mode)}
            request = urllib.request.Request(ollama+'/api/chat', data=json.dumps(body).encode(),
                                              headers={'Content-Type': 'application/json'})
            completed = False
            with urllib.request.urlopen(request, timeout=45) as response:
                for raw in response:
                    if turn.cancelled.is_set(): break
                    if time.monotonic()-started > 120: raise ValueError('This comparison exceeded two minutes.')
                    row = json.loads(raw)
                    if row.get('error'): raise ValueError('The local model returned an error.')
                    result['answer'] += row.get('message', {}).get('content', '')
                    if len(result['answer']) > 24000: raise ValueError('The reply exceeded the comparison limit.')
                    if row.get('done'):
                        completed = True
                        if isinstance(row.get('eval_count'), int): result['generated_tokens'] = row['eval_count']
                        break
            if not completed and not turn.cancelled.is_set(): raise ValueError('The model stream ended early.')
        if turn.cancelled.is_set(): result['status'] = 'cancelled'
        elif result['answer'].strip(): result['status'] = 'complete'
        else: raise ValueError('The model returned no visible answer.')
    except ProviderError as error:
        result['error'] = str(error)
    except (OSError, ValueError, TypeError, KeyError):
        result['error'] = 'The model did not finish this comparison. Check that it is available and try again.'
    finally:
        if acquired: engine.lock.release()
        result['elapsed_ms'] = round((time.monotonic()-started)*1000)
        engine.forget(turn)
    return result
