# -*- coding: utf-8 -*-
"""授权体系测试：签发→验签、篡改失败、过期失败、密钥不符失败、
三档 plan（月付/永久）、旧格式码兼容、发码 CLI 双 plan 回读。"""
import datetime as dt
import json
import os
import subprocess
import sys
import unittest

from rdb_geomind.core import license as lic
from rdb_geomind.core.usage import DailyUsageCounter, LicenseGate, QuotaExceeded
import tempfile

SECRET = "unit-test-secret-1234567890"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_legacy_code(email="legacy@example.com", exp="2027-01-01", secret=SECRET):
    """手工拼一张旧格式码：v1 载荷、无 plan 字段（定版前签发格式）。"""
    payload = {"email": email, "exp": exp, "edition": "pro", "v": 1}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return "{}-{}-{}".format(lic.CODE_PREFIX, lic._b32_grouped(raw),
                             lic._b32_grouped(lic._sign(raw, secret)))


class TestLicense(unittest.TestCase):
    def test_issue_and_verify_ok(self):
        code = lic.issue_license("buyer@example.com", 365, secret=SECRET,
                                 today=dt.date(2026, 1, 1))
        self.assertTrue(code.startswith("RDBGM-"))
        payload = lic.verify_license(code, secret=SECRET, today=dt.date(2026, 6, 1))
        self.assertEqual(payload["email"], "buyer@example.com")
        self.assertEqual(payload["edition"], "pro")
        self.assertEqual(payload["exp"], "2027-01-01")
        self.assertTrue(lic.is_pro(code, secret=SECRET, today=dt.date(2026, 6, 1)))

    def test_tampered_code_fails(self):
        code = lic.issue_license("buyer@example.com", 365, secret=SECRET)
        chars = list(code)
        # 翻转中间一个字符（避开前缀与分隔符位置的影响，签名校验必须失败）
        idx = len(chars) // 2
        chars[idx] = "A" if chars[idx] != "A" else "B"
        tampered = "".join(chars)
        with self.assertRaises(lic.LicenseError):
            lic.verify_license(tampered, secret=SECRET)
        self.assertFalse(lic.is_pro(tampered, secret=SECRET))

    def test_expired_code_fails(self):
        code = lic.issue_license("old@example.com", 10, secret=SECRET,
                                 today=dt.date(2025, 1, 1))
        with self.assertRaises(lic.LicenseError) as ctx:
            lic.verify_license(code, secret=SECRET, today=dt.date(2025, 2, 1))
        self.assertIn("到期", str(ctx.exception))
        self.assertFalse(lic.is_pro(code, secret=SECRET, today=dt.date(2025, 2, 1)))

    def test_wrong_secret_fails(self):
        code = lic.issue_license("buyer@example.com", 365, secret=SECRET)
        with self.assertRaises(lic.LicenseError):
            lic.verify_license(code, secret="another-secret")

    def test_bad_format_fails(self):
        for bad in ("", "HELLO-WORLD", "RDBGM-!!!", "RDBGM-AAAA"):
            with self.assertRaises(lic.LicenseError):
                lic.verify_license(bad, secret=SECRET)


class TestPlanTiers(unittest.TestCase):
    def test_permanent_code_never_expires(self):
        code = lic.issue_license("forever@example.com", secret=SECRET,
                                 plan=lic.PLAN_PERMANENT,
                                 today=dt.date(2026, 1, 1))
        payload = lic.verify_license(code, secret=SECRET, today=dt.date(2026, 1, 2))
        self.assertEqual(payload["plan"], "permanent")
        self.assertNotIn("exp", payload)
        # 远未来日期验签仍通过、仍是 Pro
        far = lic.verify_license(code, secret=SECRET, today=dt.date(2126, 1, 1))
        self.assertEqual(far["email"], "forever@example.com")
        self.assertTrue(lic.is_pro(code, secret=SECRET, today=dt.date(2126, 1, 1)))

    def test_monthly_code_expires_and_gate_falls_back_to_free(self):
        code = lic.issue_license("monthly@example.com", 30, secret=SECRET,
                                 plan=lic.PLAN_MONTHLY, today=dt.date(2026, 1, 1))
        payload = lic.verify_license(code, secret=SECRET, today=dt.date(2026, 1, 15))
        self.assertEqual(payload["plan"], "monthly")
        self.assertEqual(payload["exp"], "2026-01-31")
        with self.assertRaises(lic.LicenseError):
            lic.verify_license(code, secret=SECRET, today=dt.date(2026, 2, 1))
        self.assertFalse(lic.is_pro(code, secret=SECRET, today=dt.date(2026, 2, 1)))
        # LicenseGate 层：真实时钟下该码已过期（相对今天 2026-11-26），应按免费版计数拦截
        with tempfile.TemporaryDirectory() as tmp:
            counter = DailyUsageCounter(os.path.join(tmp, "usage.json"), limit=1)
            gate = LicenseGate(counter, license_code=code, secret=SECRET)
            self.assertFalse(gate.is_pro())
            self.assertIn("免费版", gate.status_text())
            gate.before_llm_call()
            with self.assertRaises(QuotaExceeded):
                gate.before_llm_call()

    def test_permanent_gate_unlimited(self):
        code = lic.issue_license("forever@example.com", secret=SECRET,
                                 plan=lic.PLAN_PERMANENT)
        with tempfile.TemporaryDirectory() as tmp:
            counter = DailyUsageCounter(os.path.join(tmp, "usage.json"), limit=1)
            gate = LicenseGate(counter, license_code=code, secret=SECRET)
            self.assertTrue(gate.is_pro())
            self.assertIn("永久版", gate.status_text())
            for _ in range(5):
                gate.before_llm_call()
            self.assertEqual(counter.used_today(), 0)

    def test_legacy_code_without_plan_treated_as_monthly(self):
        code = make_legacy_code()
        payload = lic.verify_license(code, secret=SECRET, today=dt.date(2026, 6, 1))
        self.assertEqual(lic.effective_plan(payload), "monthly")
        self.assertTrue(lic.is_pro(code, secret=SECRET, today=dt.date(2026, 6, 1)))
        # 旧码过期后同样失效（按 monthly 校验 exp）
        self.assertFalse(lic.is_pro(code, secret=SECRET, today=dt.date(2027, 6, 1)))
        with self.assertRaises(lic.LicenseError):
            lic.verify_license(code, secret=SECRET, today=dt.date(2027, 6, 1))


class TestIssueLicenseCli(unittest.TestCase):
    def run_cli(self, *args):
        env = dict(os.environ)
        env[lic.SECRET_ENV_VAR] = SECRET
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "issue_license.py"), *args],
            capture_output=True, text=True, env=env, cwd=ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_cli_monthly_issue_and_readback(self):
        out = self.run_cli("cli-monthly@example.com", "--plan", "monthly", "--days", "30")
        self.assertIn("monthly", out)
        code = out.strip().splitlines()[-1]
        payload = lic.verify_license(code, secret=SECRET)
        self.assertEqual(payload["email"], "cli-monthly@example.com")
        self.assertEqual(payload["plan"], "monthly")
        self.assertIn("exp", payload)

    def test_cli_permanent_issue_and_readback(self):
        out = self.run_cli("cli-perm@example.com", "--plan", "permanent")
        self.assertIn("永久", out)
        code = out.strip().splitlines()[-1]
        payload = lic.verify_license(code, secret=SECRET, today=dt.date(2126, 1, 1))
        self.assertEqual(payload["email"], "cli-perm@example.com")
        self.assertEqual(payload["plan"], "permanent")
        self.assertNotIn("exp", payload)


if __name__ == "__main__":
    unittest.main()
