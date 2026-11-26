# -*- coding: utf-8 -*-
"""每日计数器测试：达上限拦截、跨日重置、Pro 不计次。"""
import datetime as dt
import os
import tempfile
import unittest

from rdb_geomind.core import license as lic
from rdb_geomind.core.usage import DailyUsageCounter, LicenseGate, QuotaExceeded

SECRET = "unit-test-secret-1234567890"


class FakeClock:
    def __init__(self, day):
        self.day = day

    def __call__(self):
        return self.day


class TestUsageCounter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "usage.json")
        self.clock = FakeClock(dt.date(2026, 11, 26))

    def tearDown(self):
        self.tmp.cleanup()

    def test_limit_blocks(self):
        counter = DailyUsageCounter(self.path, limit=3, today_provider=self.clock)
        for _ in range(3):
            counter.consume()
        self.assertEqual(counter.remaining(), 0)
        with self.assertRaises(QuotaExceeded):
            counter.consume()
        with self.assertRaises(QuotaExceeded):
            counter.check()

    def test_cross_day_reset(self):
        counter = DailyUsageCounter(self.path, limit=2, today_provider=self.clock)
        counter.consume()
        counter.consume()
        self.assertEqual(counter.remaining(), 0)
        self.clock.day = dt.date(2026, 11, 27)  # 第二天
        self.assertEqual(counter.used_today(), 0)
        self.assertEqual(counter.remaining(), 2)
        counter.consume()  # 新的一天可以继续用
        self.assertEqual(counter.used_today(), 1)

    def test_persistence_across_instances(self):
        first = DailyUsageCounter(self.path, limit=10, today_provider=self.clock)
        first.consume()
        first.consume()
        second = DailyUsageCounter(self.path, limit=10, today_provider=self.clock)
        self.assertEqual(second.used_today(), 2)

    def test_gate_pro_unlimited_and_free_counted(self):
        counter = DailyUsageCounter(self.path, limit=1, today_provider=self.clock)
        code = lic.issue_license("pro@example.com", 365, secret=SECRET)
        pro_gate = LicenseGate(counter, license_code=code, secret=SECRET)
        for _ in range(5):  # Pro 连调多次不拦截、不计数
            pro_gate.before_llm_call()
        self.assertEqual(counter.used_today(), 0)
        self.assertIn("Pro", pro_gate.status_text())

        free_gate = LicenseGate(counter, license_code="", secret=SECRET)
        free_gate.before_llm_call()
        with self.assertRaises(QuotaExceeded):
            free_gate.before_llm_call()


if __name__ == "__main__":
    unittest.main()
