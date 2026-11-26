# -*- coding: utf-8 -*-
"""阶段一新增：计划审阅（行号/风险标注）与会话留档导出测试。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rdb_geomind.core.planner import Plan, PlanStep, KIND_SCRIPT, KIND_TOOL, ExecutionResult
from rdb_geomind.core.review import (SessionRecorder, format_numbered_code,
                                     plan_review_text, scan_script_risks,
                                     script_review_text)


class TestNumberedCode(unittest.TestCase):
    def test_lines_numbered_and_aligned(self):
        text = format_numbered_code("a = 1\nb = 2")
        self.assertEqual(text.split("\n"), ["1 | a = 1", "2 | b = 2"])

    def test_empty_code_placeholder(self):
        self.assertEqual(format_numbered_code(""), "（空脚本）")
        self.assertEqual(format_numbered_code("  \n"), "1 |   ")

    def test_trailing_blank_lines_trimmed(self):
        self.assertEqual(format_numbered_code("x\n\n\n"), "1 | x")


class TestRiskScan(unittest.TestCase):
    def test_hits_with_line_numbers(self):
        code = "import os\nos.remove('a')\nx = eval('1')\nprint('ok')"
        hits = scan_script_risks(code)
        self.assertEqual([(h["line"], h["pattern"]) for h in hits],
                         [(2, "os.remove"), (3, "eval(")])

    def test_clean_code_no_hits(self):
        self.assertEqual(scan_script_risks("print(backend.list_layers())"), [])


def _script_step(code="print('hi')", confirmed=False):
    return PlanStep(kind=KIND_SCRIPT, name="自定义脚本", description="演示",
                    script_code=code, confirmed=confirmed)


class TestReviewText(unittest.TestCase):
    def test_script_review_contains_numbered_code_and_risk(self):
        text = script_review_text(_script_step("import os\nos.remove('x')"))
        self.assertIn("1 | import os", text)
        self.assertIn("第 2 行", text)
        self.assertIn("删除文件", text)

    def test_script_review_clean_note(self):
        text = script_review_text(_script_step())
        self.assertIn("未检出常见高风险写法", text)

    def test_plan_review_mixes_tool_and_script(self):
        plan = Plan(title="演示计划", source="llm", steps=[
            PlanStep(kind=KIND_TOOL, name="list_layers", arguments={},
                     description="列图层"),
            _script_step(),
        ])
        text = plan_review_text(plan)
        self.assertIn("共 2 步", text)
        self.assertIn("[工具] list_layers", text)
        self.assertIn("[脚本·待确认]", text)
        self.assertIn("1 | print('hi')", text)

    def test_plan_method_delegates(self):
        plan = Plan(title="演示计划", steps=[_script_step()])
        self.assertEqual(plan.review_text(), plan_review_text(plan))


class TestSessionRecorder(unittest.TestCase):
    def _recorder(self):
        return SessionRecorder(clock=lambda: "2026-11-26 03:00:00")

    def test_record_and_markdown(self):
        rec = self._recorder()
        rec.record_chat("我", "算一下 NDVI")
        plan = Plan(title="指数计划", steps=[_script_step(confirmed=True)])
        rec.record_plan(plan)
        result = ExecutionResult(success=True, log=["✓ band_math -> NDVI"],
                                 created_layers=["NDVI"])
        rec.record_execution(plan, result)
        md = rec.to_markdown()
        self.assertIn("# RDB GeoMind 会话记录", md)
        self.assertIn("算一下 NDVI", md)
        self.assertIn("执行成功：指数计划", md)
        self.assertIn("新建图层：NDVI", md)
        self.assertEqual(len(rec.entries), 3)

    def test_failed_execution_records_rollback(self):
        rec = self._recorder()
        plan = Plan(title="坏计划", steps=[])
        result = ExecutionResult(success=False, error="ValueError：参数错",
                                 rollback_hint="本次执行未新建图层，无需回滚。")
        rec.record_execution(plan, result)
        self.assertIn("无需回滚", rec.to_markdown())

    def test_save_writes_file(self):
        rec = self._recorder()
        rec.record_chat("GeoMind", "你好")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "history.md")
            returned = rec.save(path)
            self.assertEqual(returned, path)
            with open(path, encoding="utf-8") as fh:
                self.assertIn("你好", fh.read())

    def test_empty_markdown(self):
        self.assertIn("（暂无记录）", self._recorder().to_markdown())


if __name__ == "__main__":
    unittest.main()
