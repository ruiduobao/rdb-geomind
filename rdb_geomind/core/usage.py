# -*- coding: utf-8 -*-
"""免费版每日 LLM 调用计数器。

计数保存在本地 JSON 文件中，结构::

    {"date": "2026-11-26", "count": 3}

跨日自动重置；只统计次数，不记录任何对话内容。
Pro 用户不走计数器（见 LicenseGate）。
"""

from __future__ import annotations

import datetime as _dt
import json
import os

#: 免费版每日 LLM 调用上限（默认值，可由设置覆盖）。
DEFAULT_FREE_DAILY_LIMIT = 10


class QuotaExceeded(Exception):
    """免费版当日额度已用完。"""


class DailyUsageCounter:
    """基于本地 JSON 文件的每日计数器。"""

    def __init__(self, storage_path: str, limit: int = DEFAULT_FREE_DAILY_LIMIT,
                 today_provider=None):
        self.storage_path = storage_path
        self.limit = int(limit)
        # today_provider 可注入假日期，便于测试跨日重置。
        self._today_provider = today_provider or _dt.date.today

    # -- 内部 ------------------------------------------------------------
    def _today(self) -> str:
        return self._today_provider().isoformat()

    def _load(self) -> dict:
        try:
            with open(self.storage_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        if data.get("date") != self._today():
            # 跨日（或首次使用 / 文件损坏）：重置为今日 0 次。
            return {"date": self._today(), "count": 0}
        try:
            count = int(data.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        return {"date": self._today(), "count": count}

    def _save(self, data: dict) -> None:
        folder = os.path.dirname(os.path.abspath(self.storage_path))
        os.makedirs(folder, exist_ok=True)
        tmp_path = self.storage_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp_path, self.storage_path)

    # -- 对外 ------------------------------------------------------------
    def used_today(self) -> int:
        return self._load()["count"]

    def remaining(self) -> int:
        return max(0, self.limit - self.used_today())

    def check(self) -> None:
        """额度不足时抛 QuotaExceeded（中文提示）。"""
        if self.remaining() <= 0:
            raise QuotaExceeded(
                "免费版今日 {} 次智能对话额度已用完。升级 Pro 后不限次数："
                "月付 10 元/月，或永久版 100 元（一次性）；或明日再试。".format(self.limit))

    def consume(self, n: int = 1) -> int:
        """消耗 n 次额度，返回消耗后的今日已用次数。超限时先拦截再抛错。"""
        self.check()
        data = self._load()
        data["count"] += n
        self._save(data)
        return data["count"]


class LicenseGate:
    """把授权校验与免费计数合成为一个调用前检查点。

    UI / 编排层在每次发起 LLM 请求前调用 :meth:`before_llm_call`：
    Pro 直接放行；免费版走计数器扣减。
    """

    def __init__(self, counter: DailyUsageCounter, license_code: str | None = None,
                 secret: str | None = None):
        # 延迟导入避免循环依赖。
        from . import license as _license
        self._license = _license
        self.counter = counter
        self.license_code = license_code or ""
        self.secret = secret

    def is_pro(self) -> bool:
        return self._license.is_pro(self.license_code, secret=self.secret)

    def status_text(self) -> str:
        if self.is_pro():
            payload = self._license.verify_license(self.license_code, secret=self.secret)
            if self._license.effective_plan(payload) == self._license.PLAN_PERMANENT:
                return "Pro 版（永久版，不限次数）"
            return "Pro 版（月付，授权至 {}，不限次数）".format(payload["exp"])
        return "免费版（今日剩余 {} / {} 次）".format(
            self.counter.remaining(), self.counter.limit)

    def before_llm_call(self) -> None:
        """LLM 调用前调用：Pro 放行，免费版扣 1 次，超限抛 QuotaExceeded。"""
        if self.is_pro():
            return
        self.counter.consume(1)
