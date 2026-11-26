# -*- coding: utf-8 -*-
"""编排层测试：tool_calls 组装计划、计划序列化、脚本兜底确认拦截、
执行失败回滚提示、自动修正脚本仍需确认。"""
import json
import unittest

from rdb_geomind.core.backend import FakeBackend
from rdb_geomind.core.llm_client import LLMResponse
from rdb_geomind.core.planner import (KIND_SCRIPT, Plan, PlanExecutor, Planner)


SAMPLE_TOOL_CALLS = [
    {"id": "1", "name": "load_raster", "arguments": {"path": "/data/dem.tif", "name": "DEM"}},
    {"id": "2", "name": "buffer", "arguments": {"layer": "河流", "distance": 100}},
    {"id": "3", "name": "zoom_to_layer", "arguments": {"layer": "DEM"}},
]


class StubLLM:
    """返回固定文本的假 LLM，用于 request_script_fix 测试。"""

    def __init__(self, content):
        self._content = content
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        return LLMResponse(content=self._content)


class TestPlanner(unittest.TestCase):
    def setUp(self):
        self.planner = Planner()

    def test_build_plan_from_sample_tool_calls(self):
        plan = self.planner.build_from_tool_calls(SAMPLE_TOOL_CALLS)
        self.assertEqual(len(plan.steps), 3)
        self.assertEqual([s.name for s in plan.steps],
                         ["load_raster", "buffer", "zoom_to_layer"])
        self.assertEqual(plan.steps[1].arguments["distance"], 100)
        preview = plan.preview_lines()
        self.assertEqual(len(preview), 3)
        self.assertIn("buffer", preview[1])

    def test_plan_serialization_roundtrip(self):
        plan = self.planner.build_from_tool_calls(SAMPLE_TOOL_CALLS)
        data = json.loads(json.dumps(plan.to_dict(), ensure_ascii=False))
        restored = Plan.from_dict(data)
        self.assertEqual(restored.title, plan.title)
        self.assertEqual([s.name for s in restored.steps],
                         [s.name for s in plan.steps])
        self.assertEqual(restored.steps[0].arguments, plan.steps[0].arguments)

    def test_tool_plan_executes_on_fake_backend(self):
        backend = FakeBackend(existing_layers=["河流"])
        plan = self.planner.build_from_tool_calls(SAMPLE_TOOL_CALLS)
        result = PlanExecutor(backend).execute(plan)
        self.assertTrue(result.success, result.log)
        self.assertEqual(len(result.completed_steps), 3)
        self.assertIn("DEM", result.created_layers)
        called = [name for name, _ in backend.calls]
        self.assertIn("load_raster", called)
        self.assertIn("buffer", called)

    def test_unconfirmed_script_step_is_blocked(self):
        plan = self.planner.build_script_fallback(
            "created_layers = [backend.load_raster('/x.tif', 'X')]", "测试脚本")
        self.assertTrue(plan.has_unconfirmed_script())
        backend = FakeBackend()
        result = PlanExecutor(backend).execute(plan)
        self.assertFalse(result.success)
        self.assertIn("尚未确认", result.error)
        # 除执行器自身的图层快照查询外，不应发生任何业务调用
        business_calls = [c for c in backend.calls if c[0] != "list_layers"]
        self.assertEqual(business_calls, [])

    def test_confirmed_script_step_executes(self):
        plan = self.planner.build_script_fallback(
            "created_layers = [backend.load_raster('/x.tif', 'X')]", "测试脚本")
        plan.steps[0].confirmed = True
        backend = FakeBackend()
        result = PlanExecutor(backend).execute(plan)
        self.assertTrue(result.success, result.log)
        self.assertEqual(result.created_layers, ["X"])

    def test_failure_reports_rollback_hint_with_created_layers(self):
        plan = self.planner.build_from_tool_calls([
            {"id": "1", "name": "load_raster",
             "arguments": {"path": "/d.tif", "name": "新图层"}},
            {"id": "2", "name": "no_such_tool", "arguments": {}},
        ])
        result = PlanExecutor(FakeBackend()).execute(plan)
        self.assertFalse(result.success)
        self.assertEqual(len(result.completed_steps), 1)
        self.assertIn("新图层", result.rollback_hint)
        self.assertIn("回滚", result.rollback_hint)

    def test_script_fix_roundtrip_still_needs_confirmation(self):
        bad_plan = self.planner.build_script_fallback("raise ValueError('boom')", "坏脚本")
        bad_plan.steps[0].confirmed = True
        result = PlanExecutor(FakeBackend()).execute(bad_plan)
        self.assertFalse(result.success)
        stub = StubLLM("```python\ncreated_layers = []\n```")
        fixed = self.planner.request_script_fix(stub, bad_plan, result.failed_step,
                                                result.error)
        self.assertEqual(stub.calls, 1)
        self.assertEqual(fixed.steps[0].kind, KIND_SCRIPT)
        self.assertFalse(fixed.steps[0].confirmed)  # 修正脚本仍未确认
        blocked = PlanExecutor(FakeBackend()).execute(fixed)
        self.assertFalse(blocked.success)           # 未确认不得执行


if __name__ == "__main__":
    unittest.main()
