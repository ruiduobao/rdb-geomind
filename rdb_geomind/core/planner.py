# -*- coding: utf-8 -*-
"""智能体编排：执行计划（Plan）的组装、预览与执行。

设计要点：

- LLM 返回的 tool_calls 先被组装成 :class:`Plan` 对象（可序列化），
  在界面上预览，用户点「确认执行」后才由 :class:`PlanExecutor` 真正跑。
- 没有工具能匹配需求时走「脚本兜底」：LLM 生成一段 PyQGIS 脚本，
  作为 kind="script" 的计划步进入计划，**必须被单独确认**
  （step.confirmed=True）才允许执行，未确认执行会直接报错拦截。
- 执行出错时：记录已完成步，汇总本次新建图层，给出回滚提示
  （建议移除这些图层）；脚本步支持一次自动重试——把错误信息回喂
  LLM 生成修正脚本，修正后的脚本步仍需用户再次确认。
"""

from __future__ import annotations

import traceback
import uuid
from dataclasses import dataclass, field

from . import tools as tool_module

KIND_TOOL = "tool"
KIND_SCRIPT = "script"


@dataclass
class PlanStep:
    kind: str                       # tool / script
    name: str                       # 工具名，或脚本标题
    arguments: dict = field(default_factory=dict)
    description: str = ""
    script_code: str = ""           # kind=script 时的脚本文本
    confirmed: bool = False         # 脚本步必须单独确认；工具步随计划整体确认
    step_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def to_dict(self) -> dict:
        return {"step_id": self.step_id, "kind": self.kind, "name": self.name,
                "arguments": self.arguments, "description": self.description,
                "script_code": self.script_code, "confirmed": self.confirmed}

    @classmethod
    def from_dict(cls, data: dict) -> "PlanStep":
        if not isinstance(data, dict):
            raise ValueError("计划步数据格式不正确：应为对象，实际收到 {}。".format(
                type(data).__name__))
        kind, name = data.get("kind"), data.get("name")
        if kind not in (KIND_TOOL, KIND_SCRIPT) or not name:
            raise ValueError("计划步数据不完整：缺少合法的 kind（tool/script）或 name。")
        return cls(kind=kind, name=name,
                   arguments=data.get("arguments", {}),
                   description=data.get("description", ""),
                   script_code=data.get("script_code", ""),
                   confirmed=data.get("confirmed", False),
                   step_id=data.get("step_id") or uuid.uuid4().hex[:8])

    def preview_text(self) -> str:
        if self.kind == KIND_SCRIPT:
            state = "已确认" if self.confirmed else "待确认（脚本）"
            return "[脚本·{}] {}：{}".format(state, self.name, self.description)
        args = "，".join("{}={}".format(k, v) for k, v in self.arguments.items())
        return "[工具] {}{}".format(self.name, "（{}）".format(args) if args else "")


@dataclass
class Plan:
    title: str = "执行计划"
    steps: list = field(default_factory=list)   # list[PlanStep]
    source: str = "llm"                          # llm / workflow / script_fallback

    def to_dict(self) -> dict:
        return {"title": self.title, "source": self.source,
                "steps": [s.to_dict() for s in self.steps]}

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        if not isinstance(data, dict):
            raise ValueError("计划数据格式不正确：应为对象，实际收到 {}。".format(
                type(data).__name__))
        steps_data = data.get("steps", [])
        if not isinstance(steps_data, list):
            raise ValueError("计划数据格式不正确：steps 应为列表。")
        return cls(title=data.get("title", "执行计划"),
                   source=data.get("source", "llm"),
                   steps=[PlanStep.from_dict(s) for s in steps_data])

    def preview_lines(self) -> list:
        return ["{}. {}".format(i + 1, step.preview_text())
                for i, step in enumerate(self.steps)]

    def review_text(self) -> str:
        """整份计划的审阅文本（脚本步带行号与风险标注），见 core/review.py。"""
        from .review import plan_review_text
        return plan_review_text(self)

    def has_unconfirmed_script(self) -> bool:
        return any(s.kind == KIND_SCRIPT and not s.confirmed for s in self.steps)


class Planner:
    """把 LLM 输出组装为计划；不直接执行任何操作。"""

    def build_from_tool_calls(self, tool_calls, title: str = "智能体执行计划") -> Plan:
        """tool_calls: llm_client.ToolCall 列表或等价 dict 列表。"""
        steps = []
        for call in tool_calls:
            if hasattr(call, "name"):
                name, arguments = call.name, call.arguments
            else:
                name, arguments = call.get("name", ""), call.get("arguments", {})
            tool = tool_module.TOOL_REGISTRY.get(name)
            description = tool.description if tool else "（未知工具，执行时将报错）"
            steps.append(PlanStep(kind=KIND_TOOL, name=name,
                                  arguments=dict(arguments or {}),
                                  description=description))
        return Plan(title=title, steps=steps, source="llm")

    def build_script_fallback(self, script_code: str, explanation: str = "",
                              title: str = "脚本兜底计划") -> Plan:
        """无工具可匹配时，把 LLM 生成的脚本包装为待确认的计划步。"""
        step = PlanStep(kind=KIND_SCRIPT, name="自定义脚本",
                        description=explanation or "由大模型生成的 PyQGIS 脚本",
                        script_code=script_code, confirmed=False)
        return Plan(title=title, steps=[step], source="script_fallback")

    def request_script_fix(self, llm_client, plan: Plan, failed_step: PlanStep,
                           error_message: str, chat_history: list | None = None) -> Plan:
        """一次自动重试：把失败脚本与错误回喂 LLM，生成修正脚本的新计划。

        返回的新计划中脚本步 confirmed=False，仍需用户确认后才能执行。
        """
        prompt = (
            "下面这段在 QGIS 中运行的 Python 脚本执行失败，请只输出修正后的完整脚本，"
            "不要输出解释文字。\n\n失败脚本：\n```python\n{}\n```\n\n错误信息：\n{}"
            .format(failed_step.script_code, error_message))
        messages = list(chat_history or []) + [{"role": "user", "content": prompt}]
        response = llm_client.chat(messages)
        code = _extract_code_block(response.content)
        if not code.strip():
            from .llm_client import LLMError
            raise LLMError("模型未返回可用的修正脚本，请手动修改脚本或重新描述需求。")
        step = PlanStep(kind=KIND_SCRIPT, name="自定义脚本（自动修正）",
                        description="根据报错自动修正的脚本，执行前请再次检查并确认。",
                        script_code=code, confirmed=False)
        return Plan(title=plan.title + "（修正版）", steps=[step],
                    source="script_fallback")


def _extract_code_block(text: str) -> str:
    text = (text or "").strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            cleaned = part.strip()
            if cleaned.startswith("python"):
                cleaned = cleaned[len("python"):].strip()
            if cleaned:
                return cleaned
    return text


@dataclass
class ExecutionResult:
    success: bool
    log: list = field(default_factory=list)          # 执行日志（中文）
    completed_steps: list = field(default_factory=list)  # 已完成步的 step_id
    created_layers: list = field(default_factory=list)   # 本次新建图层名
    failed_step: PlanStep | None = None
    error: str = ""
    rollback_hint: str = ""


class PlanExecutor:
    """执行计划。脚本步未确认时拒绝执行；出错时生成回滚提示。"""

    def __init__(self, backend, script_runner=None):
        self.backend = backend
        # script_runner(code, backend) -> 可迭代的新建图层名；默认用 exec 沙箱。
        self._script_runner = script_runner or _default_script_runner

    def execute(self, plan: Plan) -> ExecutionResult:
        result = ExecutionResult(success=True)
        before_layers = set(self._safe_list_layers())
        for step in plan.steps:
            try:
                if step.kind == KIND_SCRIPT:
                    if not step.confirmed:
                        raise PermissionError(
                            "脚本步「{}」尚未确认，已拦截执行。请先在计划预览中"
                            "查看脚本内容并确认。".format(step.name))
                    if not (step.script_code or "").strip():
                        raise ValueError(
                            "脚本步「{}」内容为空，已拦截执行。空脚本不会产生任何"
                            "效果，请重新生成或手动填写脚本。".format(step.name))
                    produced = self._script_runner(step.script_code, self.backend)
                    for name in produced or []:
                        if name not in result.created_layers:
                            result.created_layers.append(name)
                    result.log.append("✓ 脚本步完成：{}".format(step.name))
                else:
                    output = tool_module.execute_tool(step.name, self.backend,
                                                      step.arguments)
                    result.log.append("✓ {} -> {}".format(step.name, output))
                    if isinstance(output, str) and output:
                        after = set(self._safe_list_layers())
                        if output not in before_layers and output not in result.created_layers:
                            result.created_layers.append(output)
                        before_layers = after
                result.completed_steps.append(step.step_id)
            except Exception as exc:  # 单步失败即停，保留现场信息
                result.success = False
                result.failed_step = step
                result.error = "{}：{}".format(type(exc).__name__, exc)
                result.log.append("✗ {} 失败：{}".format(step.name, exc))
                if result.created_layers:
                    result.rollback_hint = (
                        "本次执行已新建图层：{}。如需回滚，请在图层面板中移除这些"
                        "图层（原始数据未被修改）。".format("、".join(result.created_layers)))
                else:
                    result.rollback_hint = "本次执行未新建图层，无需回滚。"
                break
        return result

    def _safe_list_layers(self) -> list:
        try:
            return self.backend.list_layers()
        except Exception:
            return []


def _default_script_runner(code: str, backend):
    """默认脚本执行器：在受限命名空间中 exec 脚本。

    脚本可使用变量 ``backend``（GisBackend 接口）操作工程；
    返回脚本命名空间中 ``created_layers`` 列表（若脚本定义了的话）。
    注意：这不是安全沙箱，脚本等同于用户本人在 QGIS 控制台运行代码，
    因此才有「执行前必须确认」的硬性要求。
    """
    namespace = {"backend": backend, "__builtins__": __builtins__}
    try:
        exec(compile(code, "<geomind-script>", "exec"), namespace)
    except Exception:
        raise RuntimeError("脚本执行异常：\n" + traceback.format_exc(limit=3))
    produced = namespace.get("created_layers") or []
    return list(produced)
