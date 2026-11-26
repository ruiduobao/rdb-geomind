# -*- coding: utf-8 -*-
"""Agent 门面测试：tool_calls 走计划、代码块走脚本兜底、纯文本走答复、
免费版额度在 Agent 层拦截。"""
import os
import tempfile
import unittest

from rdb_geomind.core.agent import Agent
from rdb_geomind.core.llm_client import LLMResponse, ToolCall
from rdb_geomind.core.usage import DailyUsageCounter, LicenseGate, QuotaExceeded


class StubLLM:
    def __init__(self, response):
        self._response = response

    def chat(self, messages, tools=None):
        assert tools, "Agent 应把工具定义传给 LLM"
        return self._response


class TestAgent(unittest.TestCase):
    def test_tool_calls_become_plan(self):
        resp = LLMResponse(content="好的", tool_calls=[
            ToolCall(id="1", name="list_layers", arguments={})])
        reply = Agent(StubLLM(resp)).ask("看看有哪些图层")
        self.assertEqual(reply.kind, "plan")
        self.assertEqual(reply.plan.steps[0].name, "list_layers")

    def test_code_block_becomes_script_fallback(self):
        resp = LLMResponse(
            content="这个需求没有现成工具。\n```python\ncreated_layers = []\n```")
        reply = Agent(StubLLM(resp)).ask("做点特别的处理")
        self.assertEqual(reply.kind, "plan")
        self.assertEqual(reply.plan.source, "script_fallback")
        self.assertFalse(reply.plan.steps[0].confirmed)

    def test_plain_text_reply(self):
        reply = Agent(StubLLM(LLMResponse(content="NDVI 是植被指数。"))).ask("什么是 NDVI")
        self.assertEqual(reply.kind, "text")
        self.assertIsNone(reply.plan)
        self.assertIn("NDVI", reply.message)

    def test_free_quota_blocks_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            counter = DailyUsageCounter(os.path.join(tmp, "u.json"), limit=1)
            gate = LicenseGate(counter, license_code="")
            agent = Agent(StubLLM(LLMResponse(content="ok")), gate=gate)
            agent.ask("第一次")
            with self.assertRaises(QuotaExceeded):
                agent.ask("第二次")


if __name__ == "__main__":
    unittest.main()
