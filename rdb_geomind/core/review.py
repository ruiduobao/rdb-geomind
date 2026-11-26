# -*- coding: utf-8 -*-
"""计划审阅与会话留档（纯标准库，无 QGIS 依赖，可无头测试）。

解决两个周到性缺口（见整改方案阶段一）：

1. **脚本审阅体验**：脚本步要用户单独确认，但此前脚本只以纯文本
   悬停提示展示，无行号、无风险提示，审阅成本高。本模块生成带
   行号的脚本审阅文本，并对常见高风险写法（删文件、联网、起子
   进程、动态执行等）给出中文风险标注——只标注、不拦截，决定权
   仍在用户（与产品「脚本须单独确认」的既有口径一致）。
2. **会话留档导出**：对话与执行记录此前只活在面板里，关掉即失。
   :class:`SessionRecorder` 把对话、计划与执行结果逐条记录，
   可一键导出为 Markdown 文件，便于留档与售后排查。
"""

from __future__ import annotations

import os
from datetime import datetime

#: 高风险写法扫描表：（关键词, 中文说明）。按行扫描，命中即标注行号。
RISK_PATTERNS = [
    ("os.remove", "删除文件（os.remove）"),
    ("os.unlink", "删除文件（os.unlink）"),
    ("shutil.rmtree", "递归删除目录（shutil.rmtree）"),
    ("shutil.move", "移动文件（shutil.move）"),
    ("subprocess", "启动外部进程（subprocess）"),
    ("os.system", "执行系统命令（os.system）"),
    ("eval(", "动态求值（eval）"),
    ("exec(", "动态执行代码（exec）"),
    ("__import__", "动态导入模块（__import__）"),
    ("urllib.request", "发起网络请求（urllib.request）"),
    ("requests.", "发起网络请求（requests）"),
    ("socket.", "网络套接字（socket）"),
    ("open(", "读写本地文件（open）"),
]


def format_numbered_code(code: str) -> str:
    """把脚本格式化为带行号的审阅文本。空脚本返回占位说明。"""
    lines = (code or "").replace("\r\n", "\n").split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return "（空脚本）"
    width = len(str(len(lines)))
    return "\n".join("{:>{w}} | {}".format(i + 1, line, w=width)
                     for i, line in enumerate(lines))


def scan_script_risks(code: str) -> list:
    """逐行扫描高风险写法，返回 [{\"line\": 行号, \"pattern\": 命中词, \"note\": 说明}]。"""
    hits = []
    for lineno, line in enumerate((code or "").split("\n"), start=1):
        for pattern, note in RISK_PATTERNS:
            if pattern in line:
                hits.append({"line": lineno, "pattern": pattern, "note": note})
    return hits


def script_review_text(step) -> str:
    """单个脚本步的完整审阅文本：标题 + 风险标注 + 带行号脚本。"""
    out = ["脚本步：{}".format(step.name)]
    if getattr(step, "description", ""):
        out.append("说明：{}".format(step.description))
    risks = scan_script_risks(step.script_code)
    if risks:
        out.append("风险标注（只提示、不拦截，请逐项核对）：")
        for hit in risks:
            out.append("  - 第 {} 行：{}".format(hit["line"], hit["note"]))
    else:
        out.append("风险标注：未检出常见高风险写法（仍请通读脚本再确认）。")
    out.append("脚本内容（带行号）：")
    out.append(format_numbered_code(step.script_code))
    return "\n".join(out)


def plan_review_text(plan) -> str:
    """整份计划的审阅文本：工具步逐条列参数，脚本步附带行号全文。"""
    out = ["计划：{}（来源：{}，共 {} 步）".format(
        plan.title, plan.source, len(plan.steps))]
    for i, step in enumerate(plan.steps, start=1):
        out.append("")
        if step.kind == "script":
            state = "已确认" if step.confirmed else "待确认"
            out.append("{}. [脚本·{}]".format(i, state))
            out.append(script_review_text(step))
        else:
            args = "，".join("{}={}".format(k, v)
                             for k, v in (step.arguments or {}).items())
            out.append("{}. [工具] {}{}".format(
                i, step.name, "（{}）".format(args) if args else ""))
            if step.description:
                out.append("    {}".format(step.description))
    return "\n".join(out)


class SessionRecorder:
    """会话留档记录器：对话 / 计划 / 执行结果逐条入册，可导出 Markdown。"""

    def __init__(self, clock=None):
        # clock: () -> str 时间戳文本，测试可注入假时钟保证确定性
        self._clock = clock or (lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.entries = []  # [{"time","kind","title","body"}]

    def record(self, kind: str, title: str, body: str = ""):
        self.entries.append({"time": self._clock(), "kind": kind,
                             "title": title, "body": body or ""})
        return self.entries[-1]

    def record_chat(self, who: str, text: str):
        return self.record("chat", who, text)

    def record_plan(self, plan):
        return self.record("plan", plan.title, plan_review_text(plan))

    def record_execution(self, plan, result):
        lines = list(result.log or [])
        if result.created_layers:
            lines.append("新建图层：{}".format("、".join(result.created_layers)))
        if not result.success:
            lines.append("执行中断：{}".format(result.error))
            if result.rollback_hint:
                lines.append(result.rollback_hint)
        state = "成功" if result.success else "失败"
        return self.record("execution", "执行{}：{}".format(state, plan.title),
                           "\n".join(lines))

    def to_markdown(self) -> str:
        out = ["# RDB GeoMind 会话记录", ""]
        if not self.entries:
            out.append("（暂无记录）")
        for e in self.entries:
            out.append("## [{}] {} · {}".format(e["time"], e["kind"], e["title"]))
            if e["body"]:
                out.append("")
                out.append(e["body"])
            out.append("")
        return "\n".join(out)

    def save(self, path: str) -> str:
        """导出为 Markdown 文件（自动建目录），返回写入路径。"""
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_markdown())
        return path
