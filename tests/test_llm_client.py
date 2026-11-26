# -*- coding: utf-8 -*-
"""LLM 客户端测试：注入假 transport，解析 tool_calls 与纯文本两类响应。"""
import json
import unittest

from rdb_geomind.core.llm_client import LLMClient, LLMError, PROVIDERS


def make_transport(payload, status=200, capture=None):
    def transport(url, headers, body, timeout):
        if capture is not None:
            capture["url"] = url
            capture["headers"] = headers
            capture["body"] = json.loads(body.decode("utf-8"))
        return status, json.dumps(payload).encode("utf-8")
    return transport


TOOL_CALL_RESPONSE = {
    "choices": [{
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "buffer",
                    "arguments": json.dumps({"layer": "道路", "distance": 500}),
                },
            }],
        },
    }],
}

TEXT_RESPONSE = {"choices": [{"message": {"role": "assistant",
                                          "content": "你好，我是 GeoMind。"}}]}


class TestLLMClient(unittest.TestCase):
    def test_tool_calls_response_parsed(self):
        capture = {}
        client = LLMClient("https://example.invalid/v1", "sk-test", "m1",
                           transport=make_transport(TOOL_CALL_RESPONSE, capture=capture))
        resp = client.chat([{"role": "user", "content": "给道路做 500 米缓冲"}],
                           tools=[{"type": "function", "function": {"name": "buffer"}}])
        self.assertTrue(resp.has_tool_calls)
        call = resp.tool_calls[0]
        self.assertEqual(call.name, "buffer")
        self.assertEqual(call.arguments, {"layer": "道路", "distance": 500})
        # 请求确实发往 chat/completions 且带了工具定义与授权头
        self.assertTrue(capture["url"].endswith("/chat/completions"))
        self.assertEqual(capture["headers"]["Authorization"], "Bearer sk-test")
        self.assertIn("tools", capture["body"])

    def test_plain_text_response_parsed(self):
        client = LLMClient("https://example.invalid/v1", "sk-test", "m1",
                           transport=make_transport(TEXT_RESPONSE))
        resp = client.chat([{"role": "user", "content": "你好"}])
        self.assertFalse(resp.has_tool_calls)
        self.assertEqual(resp.content, "你好，我是 GeoMind。")

    def test_http_error_gives_chinese_hint(self):
        client = LLMClient("https://example.invalid/v1", "bad-key", "m1",
                           transport=make_transport(
                               {"error": {"message": "invalid api key"}}, status=401))
        with self.assertRaises(LLMError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertIn("API Key", str(ctx.exception))

    def test_missing_key_rejected_before_request(self):
        client = LLMClient("https://example.invalid/v1", "", "m1",
                           transport=make_transport(TEXT_RESPONSE))
        with self.assertRaises(LLMError):
            client.chat([{"role": "user", "content": "hi"}])

    def test_provider_presets(self):
        for key in ("deepseek", "qwen", "glm", "kimi"):
            client = LLMClient.from_provider(key, api_key="k")
            self.assertTrue(client.base_url.startswith("https://"))
            self.assertTrue(client.model)
        custom = LLMClient.from_provider("custom", api_key="k", model="my-model",
                                         base_url_override="http://localhost:11434/v1")
        self.assertEqual(custom.base_url, "http://localhost:11434/v1")
        self.assertIn("custom", PROVIDERS)


if __name__ == "__main__":
    unittest.main()
