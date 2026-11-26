# -*- coding: utf-8 -*-
"""对话编排门面：把「用户一句话」变成「待确认的执行计划」。

流程：额度检查（免费版计数）→ 调 LLM（带工具定义）→
- 返回 tool_calls：组装工具计划；
- 返回纯文本且含代码块：视为没有工具能匹配，包装为脚本兜底计划（待确认）；
- 返回纯文本无代码：作为普通答复展示。
本模块不执行任何计划，执行永远由用户在界面确认后触发。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import tools as tool_module
from .planner import Plan, Planner, _extract_code_block

SYSTEM_PROMPT = (
    "你是 RDB GeoMind，嵌在 QGIS 里的地理空间智能助手，由锐多宝 RDB 开发，"
    "定位语是「为中文遥感人做的 QGIS 智能助手——说人话，干地信活」。"
    "用户会用中文描述想做的 GIS / 遥感处理任务。优先通过调用提供的工具完成任务，"
    "一次可以规划多个工具调用，顺序即执行顺序，参数中引用图层时使用工程中已有的"
    "图层名。只有当没有任何工具能完成需求时，才直接输出一段完整的 PyQGIS Python "
    "脚本（放在 ```python 代码块中，脚本可使用变量 backend 调用后端接口，"
    "并把新建图层名放进列表变量 created_layers），并在代码块前用一句话说明原因。"
    "不确定图层名时先调用 list_layers 查看。"
    "术语与数据习惯按中文遥感语境处理：坐标系优先想到 CGCS2000（EPSG:4490）与 "
    "WGS84（EPSG:4326）、高斯-克吕格投影，国内数据常见高分（GF）、哨兵（Sentinel）、"
    "Landsat、MODIS 等来源；行政区划、县界、研究区等中文地名与图层名按用户原文引用，"
    "不要擅自翻译或改名。回答与计划说明一律用中文，只写通用表述，不编造具体数据、"
    "波段参数或下载地址；不确定时明确说不确定，并先查图层再规划。"
)


@dataclass
class AgentReply:
    kind: str                       # plan / text
    message: str = ""
    plan: Plan | None = None
    history: list = field(default_factory=list)


class Agent:
    def __init__(self, llm_client, gate=None, planner: Planner | None = None):
        self.llm = llm_client
        self.gate = gate            # usage.LicenseGate，可为 None（不计次）
        self.planner = planner or Planner()

    def ask(self, user_text: str, history: list | None = None) -> AgentReply:
        """处理一轮用户输入，返回待确认计划或文本答复。"""
        if self.gate is not None:
            self.gate.before_llm_call()  # 免费版超限时在此抛 QuotaExceeded
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": user_text})
        response = self.llm.chat(messages, tools=tool_module.get_tool_schemas())
        new_history = list(history or []) + [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": response.content or ""},
        ]
        if response.has_tool_calls:
            plan = self.planner.build_from_tool_calls(response.tool_calls)
            return AgentReply(kind="plan",
                              message=response.content or "已生成执行计划，请检查后确认执行。",
                              plan=plan, history=new_history)
        content = (response.content or "").strip()
        if "```" in content:
            code = _extract_code_block(content)
            explanation = content.split("```")[0].strip() or "没有现成工具可完成该需求，改用脚本执行。"
            plan = self.planner.build_script_fallback(code, explanation)
            return AgentReply(kind="plan",
                              message="没有匹配的工具，已生成脚本计划，请仔细检查脚本并单独确认后执行。",
                              plan=plan, history=new_history)
        return AgentReply(kind="text", message=content or "（模型未返回内容）",
                          plan=None, history=new_history)
