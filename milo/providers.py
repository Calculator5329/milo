"""Configured Chat Completions transport with optional OpenRouter fallback.

Keys come from the process environment only at send time. Public status never
returns keys. The adapter sends only the caller-provided messages, follows no
redirects, and never executes model output. It works with HTTPS providers or
loopback HTTP servers.
"""
from dataclasses import dataclass, field
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit


OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'
OPENROUTER_MODELS = (
    'nvidia/nemotron-3-ultra-550b-a55b:free',
    'google/gemma-4-31b-it:free',
    'nvidia/nemotron-3-super-120b-a12b:free',
    'thinkingmachines/inkling:free',
    'google/gemma-4-26b-a4b-it:free',
)
OPENROUTER_RETRY_CODES = frozenset((402, 404, 408, 429, 502, 503, 504))


class ProviderError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProviderError(
            'The configured provider redirected the request. Update its endpoint explicitly.'
        )


@dataclass
class Provider:
    base_url: str
    model: str
    local: bool
    token_field: str = 'max_completion_tokens'
    models: tuple[str, ...] = ()
    last_model: str | None = field(default=None, init=False)
    last_usage: dict | None = field(default=None, init=False)

    @classmethod
    def configured(cls, environ=None):
        env = os.environ if environ is None else environ
        enabled = env.get('MILO_API_ENABLED')
        configured_base = env.get('MILO_API_BASE_URL', '').rstrip('/')
        openrouter = (
            not configured_base
            and bool(env.get('OPENROUTER_API_KEY'))
            and enabled != '0'
        )
        if not openrouter and enabled != '1':
            return None

        base = OPENROUTER_BASE_URL if openrouter else configured_base
        listed_models = env.get('MILO_API_MODELS', '')
        if listed_models.strip():
            models = tuple(part.strip() for part in listed_models.split(',') if part.strip())
        else:
            single_model = env.get('MILO_API_MODEL', '').strip()
            models = (single_model,) if single_model else (OPENROUTER_MODELS if openrouter else ())

        try:
            url = urlsplit(base)
            local = url.hostname in ('127.0.0.1', 'localhost', '::1')
            valid = url.scheme == 'https' or (url.scheme == 'http' and local)
            valid = (
                valid
                and bool(url.hostname)
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
            )
            _ = url.port
        except ValueError:
            valid = False
        if not valid or any(c.isspace() for c in base):
            raise ProviderError(
                'Set MILO_API_BASE_URL to an HTTPS API base, or loopback HTTP base, '
                'without credentials or a query.'
            )
        if not models or any(
            not model or len(model) > 150 or any(ord(c) < 32 for c in model)
            for model in models
        ):
            raise ProviderError('Set MILO_API_MODEL to your provider model identifier.')
        field_name = env.get('MILO_API_TOKEN_FIELD', 'max_completion_tokens')
        if field_name not in ('max_completion_tokens', 'max_tokens'):
            raise ProviderError(
                'MILO_API_TOKEN_FIELD must be max_completion_tokens or max_tokens.'
            )
        return cls(base, models[0], local, field_name, models)

    @property
    def _models(self):
        return self.models or (self.model,)

    @property
    def _openrouter(self):
        return urlsplit(self.base_url).hostname == 'openrouter.ai'

    def public(self):
        return {
            'configured': True,
            'model': self.model,
            'models': list(self._models),
            'destination': self.base_url,
            'local_endpoint': self.local,
            'sharing': 'Current question only; no conversation history, document contents or microphone audio.',
            'protocol': 'Chat Completions',
            'selection_required': True,
        }

    def _headers(self, key):
        headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream'}
        if key:
            headers['Authorization'] = 'Bearer ' + key
        if self._openrouter:
            headers['HTTP-Referer'] = 'https://github.com/Calculator5329/milo'
            headers['X-Title'] = 'Milo'
        return headers

    def stream(self, messages, cancelled, max_tokens=500):
        self.last_model = None
        self.last_usage = None
        if cancelled.is_set():
            return

        key = os.environ.get('MILO_API_KEY', '')
        if not key and self._openrouter:
            key = os.environ.get('OPENROUTER_API_KEY', '')
        if not self.local and not key:
            raise ProviderError('The API provider needs MILO_API_KEY in the server environment.')
        if '\n' in key or '\r' in key:
            raise ProviderError('The API credential is invalid.')

        opener = urllib.request.build_opener(NoRedirect())
        headers = self._headers(key)
        content_yielded = False
        models = self._models
        for index, model in enumerate(models):
            body = {
                'model': model,
                'messages': messages,
                'stream': True,
                self.token_field: max_tokens,
            }
            request = urllib.request.Request(
                self.base_url + '/chat/completions',
                data=json.dumps(body).encode(),
                headers=headers,
                method='POST',
            )
            total = 0
            finished = False
            usage = None
            try:
                with opener.open(request, timeout=20) as response:
                    while not cancelled.is_set():
                        raw = response.readline(65537)
                        if not raw:
                            break
                        if len(raw) > 65536:
                            raise ProviderError(
                                'The provider returned an oversized stream event.'
                            )
                        total += len(raw)
                        if total > 2_000_000:
                            raise ProviderError(
                                'The provider reply exceeded the demo limit.'
                            )
                        line = raw.decode('utf-8').strip()
                        if not line or line.startswith(':'):
                            continue
                        if not line.startswith('data:'):
                            continue
                        data = line[5:].strip()
                        if data == '[DONE]':
                            finished = True
                            break
                        event = json.loads(data)
                        if not isinstance(event, dict):
                            raise ProviderError(
                                'The provider returned an invalid stream event.'
                            )
                        if event.get('error'):
                            raise ProviderError(
                                'The provider reported a generation error.'
                            )
                        if isinstance(event.get('usage'), dict):
                            usage = dict(event['usage'])
                        choices = event.get('choices', [])
                        if not choices:
                            continue
                        choice = choices[0]
                        if choice.get('finish_reason') in (
                            'stop', 'length', 'content_filter'
                        ):
                            finished = True
                        delta = choice.get('delta', {})
                        if delta.get('tool_calls') or delta.get('function_call'):
                            raise ProviderError(
                                'This connection supports text answers only.'
                            )
                        for value in (delta.get('content'), delta.get('refusal')):
                            if isinstance(value, str) and value:
                                content_yielded = True
                                self.last_model = model
                                self.last_usage = {'model': model}
                                yield value
                if cancelled.is_set():
                    return
                if not finished:
                    raise ProviderError(
                        'The provider stream ended before completion.'
                    )
                self.last_model = model
                self.last_usage = dict(usage or {})
                self.last_usage['model'] = model
                return
            except urllib.error.HTTPError as error:
                code = error.code
                error.close()
                can_retry = (
                    not content_yielded
                    and code in OPENROUTER_RETRY_CODES
                    and index + 1 < len(models)
                )
                if can_retry:
                    continue
                raise ProviderError(
                    f'Provider request failed (HTTP {code}). Check the configured model and credentials.'
                ) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if not content_yielded and index + 1 < len(models):
                    continue
                raise ProviderError(
                    'The configured provider could not be reached or timed out.'
                ) from None
            except (json.JSONDecodeError, UnicodeError, TypeError, AttributeError, IndexError):
                raise ProviderError(
                    'The provider returned an invalid Chat Completions stream.'
                ) from None


def status():
    try:
        provider = Provider.configured()
        if provider:
            return provider.public()
        return {
            'configured': False,
            'reason': 'No API provider configured. Local inference remains available.',
        }
    except ProviderError as error:
        return {'configured': False, 'reason': str(error)}
