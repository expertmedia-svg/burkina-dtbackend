import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from groq_transport import groq_json_request, GroqRequestError, completion_budget


def success(content='{"translation":"test"}', reason='stop'):
    return io.BytesIO(json.dumps({'choices': [{'finish_reason': reason, 'message': {'content': content}}]}).encode())


class TransportTests(unittest.TestCase):
    payload = {'model': 'qwen/qwen3.6-27b', 'max_completion_tokens': 512,
               'messages': [], 'response_format': {'type': 'json_object'}}

    def test_qwen_non_thinking_json_and_application_headers(self):
        with patch('urllib.request.urlopen', return_value=success()) as opener:
            result = groq_json_request(self.payload, 'test-only')
        request = opener.call_args.args[0]
        self.assertEqual(json.loads(request.data)['reasoning_effort'], 'none')
        self.assertEqual(request.get_header('User-agent'), 'BurkinaDict/1.1')
        self.assertEqual(request.get_header('Accept'), 'application/json')
        self.assertEqual(result['translation'], 'test')
        self.assertNotIn('reasoning_effort', self.payload)

    def test_short_rate_limit_is_retried_once(self):
        error = HTTPError('https://api.groq.com/', 429, '', {'retry-after': '0.6'},
            io.BytesIO(b'{"error":{"message":"Rate limit reached"}}'))
        with patch('urllib.request.urlopen', side_effect=[error, success()]) as opener, patch('time.sleep') as sleep:
            groq_json_request(self.payload, 'test-only')
        self.assertEqual(opener.call_count, 2)
        sleep.assert_called_once_with(0.6)

    def test_oversized_request_not_retried(self):
        error = HTTPError('https://api.groq.com/', 429, '', {'retry-after': '1'},
            io.BytesIO(b'{"error":{"message":"Request too large"}}'))
        with patch('urllib.request.urlopen', side_effect=error) as opener, patch('time.sleep') as sleep:
            with self.assertRaises(GroqRequestError):
                groq_json_request(self.payload, 'test-only')
        self.assertEqual(opener.call_count, 1)
        sleep.assert_not_called()

    def test_json_failure_does_not_create_retry_burst(self):
        error = HTTPError('https://api.groq.com/', 400, '', {},
            io.BytesIO(b'{"error":{"code":"json_validate_failed","message":"Failed to validate JSON"}}'))
        with patch('urllib.request.urlopen', side_effect=error) as opener:
            with self.assertRaises(GroqRequestError) as caught:
                groq_json_request(self.payload, 'test-only')
        self.assertEqual(caught.exception.diagnostic['code'], 'json_validate_failed')
        self.assertEqual(opener.call_count, 1)

    def test_truncated_or_malformed_output_not_accepted(self):
        for response in (success(reason='length'), success(content=''), success(content='[]')):
            with patch('urllib.request.urlopen', return_value=response):
                with self.assertRaises(GroqRequestError):
                    groq_json_request(self.payload, 'test-only')

    def test_budget_defaults_and_invalid_config(self):
        self.assertEqual(completion_budget({}), 512)
        self.assertEqual(completion_budget({'groqMaxCompletionTokens': 'invalid'}), 512)
        self.assertEqual(completion_budget({'groqMaxCompletionTokens': '256'}), 256)


if __name__ == '__main__':
    unittest.main()
