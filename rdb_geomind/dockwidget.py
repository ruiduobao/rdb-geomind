# -*- coding: utf-8 -*-
"""RDB GeoMind 停靠聊天面板。

三个页签：
- 「智能对话」：中文聊天输入、对话历史、执行计划预览（确认后才执行）、执行日志；
- 「工作流模板」：六个遥感工作流，填参一键生成计划（不耗 LLM 次数）；
- 「设置」：模型服务商 / API Key / 模型名、授权码与免费/Pro 状态。

本模块只在 QGIS 运行时被加载（需要 qgis.PyQt），核心逻辑全部在 core/ 包。
"""

from __future__ import annotations

import os

try:
    from qgis.PyQt.QtCore import Qt, QSettings
    from qgis.PyQt.QtWidgets import (QComboBox, QDockWidget, QFormLayout,
                                     QHBoxLayout, QLabel, QLineEdit, QListWidget,
                                     QPlainTextEdit, QPushButton, QTabWidget,
                                     QTextBrowser, QVBoxLayout, QWidget)
except ImportError:  # 无 QGIS 的纯 PyQt5 环境兜底（便于离线检查导入）
    from PyQt5.QtCore import Qt, QSettings
    from PyQt5.QtWidgets import (QComboBox, QDockWidget, QFormLayout,
                                 QHBoxLayout, QLabel, QLineEdit, QListWidget,
                                 QPlainTextEdit, QPushButton, QTabWidget,
                                 QTextBrowser, QVBoxLayout, QWidget)

from .core import workflows as workflow_module
from .core.llm_client import PROVIDERS
from .core.review import format_numbered_code
from .core.planner import KIND_SCRIPT, Plan
from .core.usage import DEFAULT_FREE_DAILY_LIMIT


class GeoMindDockWidget(QDockWidget):
    """主面板。业务动作通过回调交给 plugin.py（on_send / on_execute 等）。"""

    def __init__(self, parent=None):
        super().__init__("RDB GeoMind · 智能助手", parent)
        self.setObjectName("RdbGeoMindDock")
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

        # 由 plugin.py 注入的回调
        self.on_send = None            # fn(text) -> None
        self.on_execute_plan = None    # fn(plan) -> None
        self.on_workflow_plan = None   # fn(workflow_key, params) -> Plan
        self.on_settings_saved = None  # fn(settings_dict) -> None
        self.on_verify_license = None  # fn(code) -> str 状态文本
        self.on_export_history = None  # fn() -> str 导出的文件路径（会话留档）

        self.current_plan: Plan | None = None
        self._workflow_inputs: dict = {}

        self.settings_store = QSettings("RDB", "GeoMind")
        tabs = QTabWidget()
        tabs.addTab(self._build_chat_tab(), "智能对话")
        tabs.addTab(self._build_workflow_tab(), "工作流模板")
        tabs.addTab(self._build_settings_tab(), "设置")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(4, 4, 4, 4)
        brand = QLabel("RDB GeoMind · 锐多宝出品")
        brand.setStyleSheet("font-weight: bold; font-size: 14px;")
        container_layout.addWidget(brand)
        tagline = QLabel("为中文遥感人做的 QGIS 智能助手——说人话，干地信活。")
        tagline.setWordWrap(True)
        container_layout.addWidget(tagline)
        container_layout.addWidget(tabs)
        self.setWidget(container)
        self._load_settings_into_form()

    # ------------------------------------------------------------------ 对话页
    def _build_chat_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.status_label = QLabel("免费版（今日剩余 {} / {} 次）".format(
            DEFAULT_FREE_DAILY_LIMIT, DEFAULT_FREE_DAILY_LIMIT))
        layout.addWidget(self.status_label)

        self.chat_history = QTextBrowser()
        self.chat_history.setPlaceholderText("对话历史会显示在这里。")
        layout.addWidget(self.chat_history, 3)

        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText(
            "用中文描述你想做的处理，例如：把 dem.tif 按研究区边界裁剪，再算 NDVI…")
        self.input_edit.setMaximumHeight(70)
        layout.addWidget(self.input_edit)

        send_row = QHBoxLayout()
        self.send_button = QPushButton("发送给智能体")
        self.send_button.clicked.connect(self._handle_send)
        send_row.addWidget(self.send_button)
        self.export_history_button = QPushButton("导出会话记录")
        self.export_history_button.clicked.connect(self._handle_export_history)
        send_row.addWidget(self.export_history_button)
        send_row.addStretch(1)
        layout.addLayout(send_row)

        layout.addWidget(QLabel("执行计划预览（核对无误后点「确认执行」才会真正运行）："))
        self.plan_list = QListWidget()
        layout.addWidget(self.plan_list, 2)

        plan_row = QHBoxLayout()
        self.confirm_script_button = QPushButton("确认所选脚本步")
        self.confirm_script_button.clicked.connect(self._handle_confirm_script_step)
        self.execute_button = QPushButton("确认执行")
        self.execute_button.clicked.connect(self._handle_execute)
        plan_row.addWidget(self.confirm_script_button)
        plan_row.addWidget(self.execute_button)
        layout.addLayout(plan_row)

        layout.addWidget(QLabel("执行日志："))
        self.log_view = QTextBrowser()
        layout.addWidget(self.log_view, 2)
        return page

    def _handle_send(self):
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        self.append_chat("我", text)
        self.input_edit.clear()
        if self.on_send:
            self.on_send(text)

    def _handle_export_history(self):
        if self.on_export_history is None:
            self.append_log("当前会话暂无可导出的记录。")
            return
        try:
            path = self.on_export_history()
        except Exception as exc:
            self.append_log("导出会话记录失败：{}".format(exc))
            return
        self.append_log("会话记录已导出到：{}".format(path))

    def _handle_execute(self):
        if self.current_plan is None:
            self.append_log("当前没有待执行的计划。")
            return
        if self.on_execute_plan:
            self.on_execute_plan(self.current_plan)

    def _handle_confirm_script_step(self):
        """把计划预览中选中的脚本步标记为已确认（用户已看过脚本内容）。"""
        if self.current_plan is None:
            return
        row = self.plan_list.currentRow()
        if row < 0 or row >= len(self.current_plan.steps):
            self.append_log("请先在计划预览中选中要确认的脚本步。")
            return
        step = self.current_plan.steps[row]
        if step.kind != KIND_SCRIPT:
            self.append_log("选中的不是脚本步，无需单独确认。")
            return
        step.confirmed = True
        self.append_log("已确认脚本步：{}（执行时将按脚本内容运行）".format(step.name))
        self.show_plan(self.current_plan)

    # ------------------------------------------------------------------ 工作流页
    def _build_workflow_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.workflow_combo = QComboBox()
        for key, entry in workflow_module.WORKFLOWS.items():
            self.workflow_combo.addItem("{} - {}".format(entry["title"],
                                                         entry["description"]), key)
        self.workflow_combo.currentIndexChanged.connect(self._rebuild_workflow_form)
        layout.addWidget(self.workflow_combo)

        self.workflow_form = QFormLayout()
        form_holder = QWidget()
        form_holder.setLayout(self.workflow_form)
        layout.addWidget(form_holder)

        self.workflow_button = QPushButton("生成执行计划（到对话页确认执行）")
        self.workflow_button.clicked.connect(self._handle_workflow)
        layout.addWidget(self.workflow_button)
        layout.addStretch(1)
        self._rebuild_workflow_form()
        return page

    def _rebuild_workflow_form(self):
        while self.workflow_form.rowCount():
            self.workflow_form.removeRow(0)
        self._workflow_inputs = {}
        key = self.workflow_combo.currentData()
        entry = workflow_module.WORKFLOWS.get(key)
        if entry is None:
            return
        for spec in entry["params"]:
            edit = QLineEdit()
            hint = {"layer": "填工程中的图层名", "layers": "多个图层名用英文逗号分隔",
                    "band_map": "如 NIR=4,RED=3", "text_list": "多个值用英文逗号分隔",
                    "bool": "填 true / false"}.get(spec["type"], "")
            edit.setPlaceholderText(hint)
            label = spec["label"] + (" *" if spec.get("required") else "")
            self.workflow_form.addRow(label, edit)
            self._workflow_inputs[spec["name"]] = (edit, spec)

    def _collect_workflow_params(self) -> dict:
        params = {}
        for name, (edit, spec) in self._workflow_inputs.items():
            raw = edit.text().strip()
            if not raw:
                continue
            kind = spec["type"]
            if kind in ("layers", "text_list"):
                params[name] = [p.strip() for p in raw.split(",") if p.strip()]
            elif kind == "int":
                params[name] = int(raw)
            elif kind == "bool":
                params[name] = raw.lower() in ("true", "1", "yes", "是")
            elif kind == "band_map":
                mapping = {}
                for pair in raw.split(","):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        mapping[k.strip().upper()] = int(v.strip())
                params[name] = mapping
            else:
                params[name] = raw
        return params

    def _handle_workflow(self):
        if self.on_workflow_plan is None:
            return
        key = self.workflow_combo.currentData()
        try:
            plan = self.on_workflow_plan(key, self._collect_workflow_params())
        except Exception as exc:
            self.append_log("工作流参数有误：{}".format(exc))
            return
        self.show_plan(plan)
        self.append_log("已生成工作流计划「{}」，请切换到对话页核对后确认执行。".format(plan.title))

    # ------------------------------------------------------------------ 设置页
    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.provider_combo = QComboBox()
        for key, preset in PROVIDERS.items():
            self.provider_combo.addItem(preset["label"], key)
        self.provider_combo.currentIndexChanged.connect(self._apply_provider_preset)
        form.addRow("模型服务商", self.provider_combo)

        self.base_url_edit = QLineEdit()
        form.addRow("服务地址 base_url", self.base_url_edit)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("填你自己的 API Key，只保存在本机设置中")
        form.addRow("API Key", self.api_key_edit)
        self.model_edit = QLineEdit()
        form.addRow("模型名", self.model_edit)

        self.license_edit = QLineEdit()
        self.license_edit.setPlaceholderText("Pro 授权码（RDBGM- 开头），没有可留空用免费版")
        form.addRow("授权码", self.license_edit)
        self.license_status = QLabel("未验证")
        form.addRow("授权状态", self.license_status)

        row = QHBoxLayout()
        save_button = QPushButton("保存设置")
        save_button.clicked.connect(self._handle_save_settings)
        verify_button = QPushButton("验证授权码")
        verify_button.clicked.connect(self._handle_verify_license)
        row.addWidget(save_button)
        row.addWidget(verify_button)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow(holder)

        note = QLabel("定价：免费版每日 10 次智能对话、工作流模板不限次；"
                      "Pro 月付 10 元/月不限次数，永久版 100 元（一次性）不限次数。"
                      "API Key 只存本机、只发往你选择的服务商。")
        note.setWordWrap(True)
        form.addRow(note)
        support = QLabel("技术支持：caaschengrui@163.com")
        support.setWordWrap(True)
        form.addRow(support)
        return page

    def _apply_provider_preset(self):
        key = self.provider_combo.currentData()
        preset = PROVIDERS.get(key, {})
        if preset.get("base_url"):
            self.base_url_edit.setText(preset["base_url"])
        if preset.get("default_model") and not self.model_edit.text().strip():
            self.model_edit.setText(preset["default_model"])

    def collect_settings(self) -> dict:
        return {
            "provider": self.provider_combo.currentData(),
            "base_url": self.base_url_edit.text().strip(),
            "api_key": self.api_key_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "license_code": self.license_edit.text().strip(),
        }

    def _handle_save_settings(self):
        data = self.collect_settings()
        for key, value in data.items():
            self.settings_store.setValue("geomind/" + key, value)
        if self.on_settings_saved:
            self.on_settings_saved(data)
        self.append_log("设置已保存到本机。")

    def _handle_verify_license(self):
        code = self.license_edit.text().strip()
        if self.on_verify_license:
            self.license_status.setText(self.on_verify_license(code))
        elif not code:
            self.license_status.setText("未填授权码，当前为免费版。")
        else:
            self.license_status.setText("已填写，保存设置后生效。")

    def _load_settings_into_form(self):
        store = self.settings_store
        provider = store.value("geomind/provider", "deepseek")
        index = self.provider_combo.findData(provider)
        if index >= 0:
            self.provider_combo.setCurrentIndex(index)
        self.base_url_edit.setText(store.value("geomind/base_url", "") or "")
        self.api_key_edit.setText(store.value("geomind/api_key", "") or "")
        self.model_edit.setText(store.value("geomind/model", "") or "")
        self.license_edit.setText(store.value("geomind/license_code", "") or "")
        if not self.base_url_edit.text():
            self._apply_provider_preset()

    # ------------------------------------------------------------------ 展示接口
    def append_chat(self, who: str, text: str):
        self.chat_history.append("<b>{}</b>：{}".format(who, text))

    def append_log(self, text: str):
        self.log_view.append(text)

    def set_status_text(self, text: str):
        self.status_label.setText(text)

    def show_plan(self, plan: Plan):
        self.current_plan = plan
        self.plan_list.clear()
        for line in plan.preview_lines():
            self.plan_list.addItem(line)
        for index, step in enumerate(plan.steps):
            if step.kind == KIND_SCRIPT and step.script_code:
                self.plan_list.item(index).setToolTip(
                    "脚本内容（带行号，确认前请仔细检查）：\n"
                    + format_numbered_code(step.script_code))
