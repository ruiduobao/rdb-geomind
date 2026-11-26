# -*- coding: utf-8 -*-
"""新增工具测试：注册计数、计划生成（Planner 组装）、参数校验、FakeBackend 执行。

新增 7 个工具：join_attributes、spatial_join、field_calculator、
fix_geometries、extract_by_attribute、dissolve、merge_vector_layers。
"""
import unittest

from rdb_geomind.core.backend import FakeBackend
from rdb_geomind.core.planner import PlanExecutor, Planner
from rdb_geomind.core.tools import (TOOL_REGISTRY, execute_tool,
                                    validate_tool_arguments)

NEW_TOOLS = {
    "join_attributes": {"layer": "县界", "join_layer": "统计表",
                        "layer_field": "县代码", "join_field": "code"},
    "spatial_join": {"layer": "样点", "join_layer": "县界"},
    "field_calculator": {"layer": "县界", "field_name": "pop_density",
                         "expression": "\"pop\" / \"area\"", "field_type": "float"},
    "fix_geometries": {"layer": "地块"},
    "extract_by_attribute": {"layer": "地块", "expression": "\"类型\" = '水田'"},
    "dissolve": {"layer": "地块", "field": "类型"},
    "merge_vector_layers": {"layers": ["县A", "县B"]},
}


class TestNewTools(unittest.TestCase):
    def test_registry_count_21(self):
        self.assertEqual(len(TOOL_REGISTRY), 21)
        for name in NEW_TOOLS:
            self.assertIn(name, TOOL_REGISTRY)

    def test_each_new_tool_plan_and_execute(self):
        planner = Planner()
        for name, args in NEW_TOOLS.items():
            backend = FakeBackend(existing_layers=["县界", "统计表", "样点",
                                                   "地块", "县A", "县B"])
            plan = planner.build_from_tool_calls(
                [{"id": "1", "name": name, "arguments": dict(args)}])
            self.assertEqual(plan.steps[0].name, name)
            result = PlanExecutor(backend).execute(plan)
            self.assertTrue(result.success, (name, result.log))
            called = [c[0] for c in backend.calls]
            self.assertIn(name, called)

    def test_new_tool_arguments_passed_through(self):
        backend = FakeBackend()
        out = execute_tool("join_attributes", backend, dict(NEW_TOOLS["join_attributes"]))
        self.assertIn(out, backend.layers)
        _, kwargs = backend.calls[-1]
        self.assertEqual(kwargs["layer_field"], "县代码")
        self.assertEqual(kwargs["join_field"], "code")

    def test_missing_required_rejected(self):
        for name, args in NEW_TOOLS.items():
            broken = dict(args)
            first_required = TOOL_REGISTRY[name].parameters["required"][0]
            broken.pop(first_required)
            with self.assertRaises(ValueError, msg=name):
                validate_tool_arguments(name, broken)

    def test_specific_constraints(self):
        with self.assertRaises(ValueError):  # 合并至少 2 个图层
            validate_tool_arguments("merge_vector_layers", {"layers": ["只有一个"]})
        with self.assertRaises(ValueError):  # 字段名不能以数字开头
            validate_tool_arguments("field_calculator",
                                    {"layer": "L", "field_name": "1bad",
                                     "expression": "1"})
        with self.assertRaises(ValueError):  # 缓冲距离必须为正
            validate_tool_arguments("buffer", {"layer": "L", "distance": 0})
        with self.assertRaises(KeyError):    # 未知工具
            validate_tool_arguments("no_such_tool", {})

    def test_valid_arguments_normalized_copy(self):
        args = dict(NEW_TOOLS["dissolve"])
        out = validate_tool_arguments("dissolve", args)
        self.assertEqual(out, args)
        self.assertIsNot(out, args)


if __name__ == "__main__":
    unittest.main()
