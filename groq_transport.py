"""Bounded Groq requests shared by translation, retrieval and conversation."""
import json
import math
import re
import time
import urllib.error
import urllib.request


def completion_budget(config, default=512):
    try:
        return max(128, min(8192, int(config.get('groqMaxCompletionTokens', default))))
    except (TypeError, ValueError, OverflowError):
        return default


def describe_http_error(error, api_key):
    raw = error.read(16000).decode('utf-8', errors='replace')
    try:
        detail = json.loads(raw).get('error', {})
        if not isinstance(detail, dict):
            detail = {}
    except (ValueError, AttributeError):
        detail = {}
    result = {'http_status': error.code}
    for field in ('code', 'type', 'message'):
        value = str(detail.get(field, raw[:600] if field == 'message' else ''))
        if api_key:
            value = value.replace(api_key, '[REDACTED]')
        value = re.sub(r'(?:gsk_|sk-)[A-Za-z0-9_-]+', '[REDACTED]', value)
        value = re.sub(r'(?i)Bearer\s+\S+', 'Bearer [REDACTED]', value)
        result[field] = value[:1000]
    return result


class GroqRequestError(Exception):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__(json.dumps(diagnostic, ensure_ascii=True))


def groq_json_request(payload, api_key, timeout=45):
    payload = dict(payload)
    # Groq documents non-thinking mode for Qwen 3.6. Avoid spending the small
    # output allowance on reasoning before the JSON answer even starts.
    if payload.get('model') == 'qwen/qwen3.6-27b':
        payload['reasoning_effort'] = 'none'
    deadline = time.monotonic() + timeout
    for attempt in range(2):
        request = urllib.request.Request('https://api.groq.com/openai/v1/chat/completions',
            data=json.dumps(payload).encode('utf-8'), headers={
                'Content-Type': 'application/json', 'Accept': 'application/json',
                'User-Agent': 'BurkinaDict/1.1', 'Authorization': 'Bearer ' + api_key}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=max(1, deadline-time.monotonic())) as response:
                result = json.loads(response.read().decode('utf-8'))
            choice = result['choices'][0]
            if choice.get('finish_reason') == 'length':
                raise GroqRequestError({'code': 'output_truncated', 'message': 'Réponse tronquée : raccourcir le texte ou adapter le budget de sortie.'})
            data = json.loads(choice['message']['content'].strip())
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object')
            return data
        except urllib.error.HTTPError as error:
            diagnostic = describe_http_error(error, api_key)
            # Repeating an oversized request cannot fix a per-request limit.
            oversized = 'request too large' in diagnostic['message'].lower()
            try:
                delay = float(error.headers.get('retry-after', 'nan'))
            except (ValueError, TypeError, AttributeError):
                delay = float('nan')
            if (attempt == 0 and error.code == 429 and not oversized
                    and math.isfinite(delay) and 0 <= delay <= 8
                    and time.monotonic() + delay + 1 < deadline):
                time.sleep(delay)
                continue
            raise GroqRequestError(diagnostic) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise GroqRequestError({'code': 'invalid_json_response',
                                    'message': 'Réponse Groq vide ou JSON invalide.'}) from None


def record_groq_error(config, error):
    diagnostic = error.diagnostic if isinstance(error, GroqRequestError) else {'code': type(error).__name__}
    config['groqLastError'] = diagnostic
    print('GROQ_DIAGNOSTIC ' + json.dumps(diagnostic, ensure_ascii=True), flush=True)
