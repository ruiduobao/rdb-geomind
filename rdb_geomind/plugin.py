# -*- coding: utf-8 -*-
"""RDB GeoMind 主插件类：装配 UI 与 core 引擎。

职责边界：本类只做胶水——把面板回调接到 Agent / PlanExecutor /
LicenseGate 上；所有可测试的逻辑都在 core/ 包内。
"""

from __future__ import annotations

import os

try:
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import QAction
except ImportError:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QAction

from .core import workflows as workflow_module
from .core.agent import Agent
from .core.backend import QgisBackend
from .core.llm_client import LLMClient, LLMError
from .core.planner import PlanExecutor
from .core.review import SessionRecorder
from .core.usage import DailyUsageCounter, LicenseGate, QuotaExceeded


class RdbGeoMindPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None
        self.backend = QgisBackend(iface)
        self.history: list = []
        self.recorder = SessionRecorder()  # 会话留档（对话/计划/执行，可导出）
        self._settings: dict = {}

    # ------------------------------------------------------------ QGIS 生命周期
    def initGui(self):
        from .dockwidget import GeoMindDockWidget
        self.dock = GeoMindDockWidget(self.iface.mainWindow())
        self.dock.on_send = self._handle_send
        self.dock.on_execute_plan = self._handle_execute_plan
        self.dock.on_workflow_plan = workflow_module.build_plan
        self.dock.on_settings_saved = self._handle_settings_saved
        self.dock.on_verify_license = self._handle_verify_license
        self.dock.on_export_history = self._handle_export_history
        self._settings = self.dock.collect_settings()
        self._refresh_status()
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.action = QAction("RDB GeoMind", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.triggered.connect(self._toggle_dock)
        self.iface.addPluginToMenu("RDB GeoMind", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removePluginMenu("RDB GeoMind", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

    def _toggle_dock(self, checked):
        if self.dock is not None:
            self.dock.setVisible(checked)

    # ------------------------------------------------------------ 内部装配
    def _usage_path(self) -> str:
        home = os.path.expanduser("~")
        return os.path.join(home, ".rdb_geomind", "usage.json")

    def _build_gate(self) -> LicenseGate:
        counter = DailyUsageCounter(self._usage_path())
        return LicenseGate(counter, license_code=self._settings.get("license_code", ""))

    def _build_agent(self) -> Agent:
        client = LLMClient.from_provider(
            self._settings.get("provider", "deepseek"),
            api_key=self._settings.get("api_key", ""),
            model=self._settings.get("model", ""),
            base_url_override=self._settings.get("base_url", ""))
        return Agent(client, gate=self._build_gate())

    def _refresh_status(self):
        if self.dock is None:
            return
        try:
            self.dock.set_status_text(self._build_gate().status_text())
        except Exception:
            pass  # 状态展示失败不阻断插件加载

    # ------------------------------------------------------------ 回调
    def _handle_settings_saved(self, settings: dict):
        self._settings = settings
        self._refresh_status()

    def _handle_verify_license(self, code: str) -> str:
        from .core import license as license_module
        if not code:
            return "未填授权码，当前为免费版（每日 10 次）。"
        try:
            payload = license_module.verify_license(code)
        except license_module.LicenseError as exc:
            return "无效：{}".format(exc)
        if license_module.effective_plan(payload) == license_module.PLAN_PERMANENT:
            return "有效：Pro 永久版（不限次数，{}）".format(payload["email"])
        return "有效：Pro 月付版，授权至 {}（{}）".format(payload["exp"], payload["email"])

    def _handle_export_history(self) -> str:
        """把本会话的对话/计划/执行记录导出为 Markdown 留档文件。"""
        from datetime import datetime
        directory = os.path.join(os.path.expanduser("~"),
                                 ".rdb_geomind", "history")
        filename = "会话记录-{}.md".format(
            datetime.now().strftime("%Y%m%d-%H%M%S"))
        return self.recorder.save(os.path.join(directory, filename))

    def _handle_send(self, text: str):
        dock = self.dock
        self.recorder.record_chat("我", text)
        try:
            agent = self._build_agent()
            reply = agent.ask(text, history=self.history)
        except QuotaExceeded as exc:
            dock.append_chat("GeoMind", str(exc))
            self._refresh_status()
            return
        except LLMError as exc:
            dock.append_chat("GeoMind", "调用模型失败：{}".format(exc))
            return
        self.history = reply.history
        self.recorder.record_chat("GeoMind", reply.message)
        dock.append_chat("GeoMind", reply.message)
        if reply.plan is not None:
            self.recorder.record_plan(reply.plan)
            dock.show_plan(reply.plan)
            dock.append_log("已生成计划「{}」，共 {} 步，等待确认执行。".format(
                reply.plan.title, len(reply.plan.steps)))
        self._refresh_status()

    def _handle_execute_plan(self, plan):
        dock = self.dock
        executor = PlanExecutor(self.backend)
        result = executor.execute(plan)
        self.recorder.record_execution(plan, result)
        for line in result.log:
            dock.append_log(line)
        if result.success:
            dock.append_log("计划执行完成。新建图层：{}".format(
                "、".join(result.created_layers) or "无"))
        else:
            dock.append_log("执行中断：{}".format(result.error))
            dock.append_log(result.rollback_hint)
            failed = result.failed_step
            if failed is not None and failed.kind == "script" and failed.confirmed:
                dock.append_log("可点「自动修正脚本」让模型按报错改一版（仍需再次确认）。")
                self._offer_script_fix(plan, failed, result.error)
        self._refresh_status()

    def _offer_script_fix(self, plan, failed_step, error_message: str):
        """脚本失败后的一次自动重试：生成修正版计划放入预览，等用户确认。"""
        dock = self.dock
        try:
            agent = self._build_agent()
            fixed = agent.planner.request_script_fix(
                agent.llm, plan, failed_step, error_message,
                chat_history=self.history)
        except Exception as exc:
            dock.append_log("自动修正失败：{}".format(exc))
            return
        dock.show_plan(fixed)
        dock.append_log("已生成修正版脚本计划，请检查脚本内容、确认脚本步后再执行。")
