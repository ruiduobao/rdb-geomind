# -*- coding: utf-8 -*-
"""第 3 轮（边界与异常）测试：非法/空输入、服务商失败与超时、授权码异常、
超长脚本与计划审阅兜底、工具参数类型异常。

授权码面板侧表现口径：verify_license 对伪造/过期/篡改一律抛中文
LicenseError，is_pro 一律返回 False（面板据此回落免费版），此处锁定。
"""
import datetime as dt
import json
import unittest

from rdb_geomind.core import license as lic
from rdb_geomind.core import tools as tool_module
from rdb_geomind.core.backend import FakeBackend
from rdb_geomind.core.llm_client import LLMClient, LLMError, LLMResponse
from rdb_geomind.core.planner import Plan, PlanExecutor, Planner

SECRET = "round3-test-secret"
TODAY = dt.date(2026, 11, 26)


def transport_of(status, body_bytes):
    def _t(url, headers, body, timeout):
        return status, body_bytes
    return _t


def json_transport(payload, status=200):
    return transport_of(status, json.dumps(payload).encode("utf-8"))


class TestLicenseBoundaries(unittest.TestCase):
    def test_issue_days_overflow_is_license_error(self):
        with self.assertRaises(lic.LicenseError):
            lic.issue_license("a@b.cn", days=10 ** 9, secret=SECRET)

    def test_issue_days_non_numeric(self):
        with self.assertRaises(lic.LicenseError):
            lic.issue_license("a@b.cn", days="30", secret=SECRET)

    def test_verify_empty_and_non_string(self):
        for bad in ("", "   ", None, 12345):
            with self.assertRaises(lic.LicenseError):
                lic.verify_license(bad, secret=SECRET)

    def test_verify_garbage_and_truncated(self):
        with self.assertRaises(lic.LicenseError):
            lic.verify_license("RDBGM-XXXXX-YYYYY", secret=SECRET)
        code = lic.issue_license("a@b.cn", days=30, secret=SECRET, today=TODAY)
        with self.assertRaises(lic.LicenseError):
            lic.verify_license(code[:24], secret=SECRET, today=TODAY)

    def test_verify_tampered_signature(self):
        code = lic.issue_license("a@b.cn", days=30, secret=SECRET, today=TODAY)
        tampered = code[:-2] + ("AA" if not code.endswith("AA") else "BB")
        with self.assertRaises(lic.LicenseError) as ctx:
            lic.verify_license(tampered, secret=SECRET, today=TODAY)
        self.assertIn("篡改", str(ctx.exception))
        self.assertFalse(lic.is_pro(tampered, secret=SECRET, today=TODAY))

    def test_verify_wrong_secret_and_expired(self):
        code = lic.issue_license("a@b.cn", days=30, secret=SECRET, today=TODAY)
        with self.assertRaises(lic.LicenseError):
            lic.verify_license(code, secret="another-secret", today=TODAY)
        old = lic.issue_license("a@b.cn", days=30, secret=SECRET,
                                today=dt.date(2020, 1, 1))
        with self.assertRaises(lic.LicenseError) as ctx:
            lic.verify_license(old, secret=SECRET, today=TODAY)
        self.assertIn("到期", str(ctx.exception))
        self.assertFalse(lic.is_pro(old, secret=SECRET, today=TODAY))

    def test_verify_lowercase_and_permanent_still_ok(self):
        code = lic.issue_license("a@b.cn", days=30, secret=SECRET, today=TODAY)
        payload = lic.verify_license(code.lower(), secret=SECRET, today=TODAY)
        self.assertEqual(payload["email"], "a@b.cn")
        perm = lic.issue_license("a@b.cn", secret=SECRET, plan=lic.PLAN_PERMANENT)
        payload = lic.verify_license(perm, secret=SECRET,
                                     today=dt.date(2099, 1, 1))
        self.assertEqual(payload["plan"], "permanent")


class TestLLMBoundaries(unittest.TestCase):
    def client(self, transport):
        return LLMClient("https://example.invalid/v1", "sk-x", "m1",
                         transport=transport)

    def test_transport_timeout_becomes_llm_error(self):
        def boom(url, headers, body, timeout):
            raise TimeoutError("timed out")
        with self.assertRaises(LLMError) as ctx:
            self.client(boom).chat([{"role": "user", "content": "hi"}])
        self.assertIn("连接模型服务失败", str(ctx.exception))

    def test_http_error_with_html_body_keeps_status_hint(self):
        client = self.client(transport_of(401, b"<html>Unauthorized</html>"))
        with self.assertRaises(LLMError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertIn("API Key", str(ctx.exception))
        client = self.client(transport_of(500, b"Internal Server Error"))
        with self.assertRaises(LLMError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertIn("HTTP 500", str(ctx.exception))

    def test_http_error_json_detail_still_used(self):
        client = self.client(json_transport(
            {"error": {"message": "quota exceeded"}}, status=429))
        with self.assertRaises(LLMError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertIn("quota exceeded", str(ctx.exception))

    def test_ok_status_with_non_json_body(self):
        client = self.client(transport_of(200, b"not json"))
        with self.assertRaises(LLMError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertIn("无法解析", str(ctx.exception))

    def test_empty_choices_and_empty_messages(self):
        client = self.client(json_transport({"choices": []}))
        with self.assertRaises(LLMError):
            client.chat([{"role": "user", "content": "hi"}])
        client = self.client(json_transport(
            {"choices": [{"message": {"content": "ok"}}]}))
        with self.assertRaises(LLMError):
            client.chat([])

    def test_tool_arguments_wrong_type_rejected(self):
        payload = {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "1", "function": {"name": "buffer", "arguments": [1, 2]}}]}}]}
        with self.assertRaises(LLMError):
            self.client(json_transport(payload)).chat(
                [{"role": "user", "content": "hi"}])

    def test_tool_arguments_bad_json_rejected(self):
        payload = {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "1", "function": {"name": "buffer",
                                     "arguments": "{bad json"}}]}}]}
        with self.assertRaises(LLMError):
            self.client(json_transport(payload)).chat(
                [{"role": "user", "content": "hi"}])


class TestPlannerBoundaries(unittest.TestCase):
    def test_from_dict_malformed_inputs(self):
        for bad in (None, "x", {"steps": "abc"}, {"steps": [{"name": "x"}]},
                    {"steps": [{"kind": "weird", "name": "x"}]}):
            with self.assertRaises(ValueError):
                Plan.from_dict(bad)

    def test_from_dict_roundtrip_still_ok(self):
        plan = Planner().build_script_fallback("created_layers = []", "说明")
        clone = Plan.from_dict(plan.to_dict())
        self.assertEqual(clone.steps[0].script_code, "created_layers = []")
        self.assertEqual(clone.source, "script_fallback")

    def test_confirmed_empty_script_blocked_at_execution(self):
        plan = Planner().build_script_fallback("   \n  ", "空脚本")
        plan.steps[0].confirmed = True
        result = PlanExecutor(FakeBackend()).execute(plan)
        self.assertFalse(result.success)
        self.assertIn("为空", result.error)

    def test_huge_script_executes_and_reviews(self):
        huge = "\n".join("# 注释行 %d" % i for i in range(20000)) \
            + "\ncreated_layers = []"
        plan = Planner().build_script_fallback(huge, "超长脚本")
        plan.steps[0].confirmed = True
        result = PlanExecutor(FakeBackend()).execute(plan)
        self.assertTrue(result.success)
        self.assertGreater(len(plan.review_text()), 100000)

    def test_script_fix_with_empty_llm_reply_raises(self):
        class EmptyLLM:
            def chat(self, messages, tools=None):
                return LLMResponse(content="")
        planner = Planner()
        failed = planner.build_script_fallback("bad(", "x").steps[0]
        with self.assertRaises(LLMError):
            planner.request_script_fix(EmptyLLM(), Plan(), failed, "err")


class TestToolArgumentBoundaries(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()

    def test_arguments_not_a_dict(self):
        with self.assertRaises(ValueError) as ctx:
            tool_module.execute_tool("buffer", self.backend, "not-a-dict")
        self.assertIn("键值对", str(ctx.exception))

    def test_numeric_arg_as_string_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            tool_module.execute_tool(
                "buffer", self.backend, {"layer": "L", "distance": "abc"})
        self.assertIn("应为数字", str(ctx.exception))

    def test_integer_arg_float_with_fraction_rejected(self):
        with self.assertRaises(ValueError):
            tool_module.execute_tool(
                "band_composite", self.backend,
                {"layer": "L", "red_band": 4.5, "green_band": 3, "blue_band": 2})

    def test_integer_arg_integral_float_normalized(self):
        args = tool_module.validate_tool_arguments(
            "band_composite",
            {"layer": "L", "red_band": 4.0, "green_band": 3, "blue_band": 2})
        self.assertEqual(args["red_band"], 4)
        self.assertIsInstance(args["red_band"], int)

    def test_enum_violation_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            tool_module.execute_tool(
                "set_style", self.backend, {"layer": "L", "mode": "weird"})
        self.assertIn("只能取", str(ctx.exception))

    def test_bool_not_accepted_as_number(self):
        with self.assertRaises(ValueError):
            tool_module.execute_tool(
                "buffer", self.backend, {"layer": "L", "distance": True})


if __name__ == "__main__":
    unittest.main()
