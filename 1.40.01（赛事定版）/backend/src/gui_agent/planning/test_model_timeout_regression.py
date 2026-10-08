import unittest
from unittest.mock import MagicMock, patch

import httpx
from pydantic import SecretStr

from gui_agent.planning import ai_provider as provider


class ModelTimeoutRegressionTest(unittest.TestCase):
    def setUp(self):
        provider.reset_model_circuit_breaker()
        self.settings = provider.AISettings(
            protocol="chat_completions",
            base_url="https://api.moonshot.cn/v1",
            model="kimi-k3",
            api_key=SecretStr("test-key"),
        )

    def test_generation_uses_low_reasoning_without_probe_token_limit(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = httpx.Response(
            200, json={"choices": [{"message": {"content": "{}"}}]}
        )
        with patch.object(provider.httpx, "Client", return_value=client):
            provider._post(self.settings, "plan", schema={"type": "object"})
        payload = client.post.call_args.kwargs["json"]
        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertEqual(payload["max_tokens"], 4096)

    def test_read_timeout_is_not_retried_and_reports_actual_attempt(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.side_effect = httpx.ReadTimeout("private exception detail")
        with patch.object(provider.httpx, "Client", return_value=client), \
                patch.object(provider.time, "sleep") as sleep:
            with self.assertRaises(provider.AIProviderUnavailableError) as raised:
                provider._post(self.settings, "plan", schema=None)
        self.assertEqual(client.post.call_count, 1)
        self.assertEqual(raised.exception.attempts, 1)
        self.assertNotIn("private exception detail", str(raised.exception))
        sleep.assert_not_called()

    def test_invalid_key_is_rejected_before_network(self):
        for key in ["test key", "test-key\n", "\u5bc6\u94a5"]:
            with self.subTest(key=repr(key)):
                settings = provider.AISettings(
                    protocol="chat_completions", base_url=self.settings.base_url,
                    model=self.settings.model, api_key=SecretStr(key),
                )
                with self.assertRaises(provider.AIProviderConfigurationError):
                    settings.validated()


if __name__ == "__main__":
    unittest.main()
