# -*- coding: utf-8 -*-
"""大模型接入层：OpenAI 兼容 Chat Completions 接口。

只用 Python 标准库 urllib 实现，零第三方依赖。
网络层可注入假 transport（签名见 :data:`Transport`），便于无头测试。

支持 function / tool calling 协议解析：把响应里的 tool_calls 统一成
``[{"id": ..., "name": ..., "arguments": {...}}]`` 的结构交给编排层。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

#: transport 可调用对象签名：(url, headers, body_bytes, timeout) -> (status, body_bytes)
Transport = object  # typing.Callable，为避免运行时开销此处仅作文档标注

#: 内置服务商预置：均为 OpenAI 兼容端点。用户只需填自己的 API Key。
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    "qwen": {
        "label": "通义千问（DashScope 兼容模式）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
    },
    "glm": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-plus",
    },
    "kimi": {
        "label": "Kimi（Moonshot）",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "base_url": "",
        "default_model": "",
    },
}


class LLMError(Exception):
    """LLM 调用失败，消息为中文、面向最终用户。"""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list = field(default_factory=list)  # list[ToolCall]
    raw: dict = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def _default_transport(url, headers, body, timeout):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:  # 服务器返回了错误状态码
        return exc.code, exc.read()


def _parse_tool_calls(message: dict) -> list:
    calls = []
    for item in message.get("tool_calls") or []:
        func = item.get("function") or {}
        raw_args = func.get("arguments") or "{}"
        if isinstance(raw_args, str):
            try:
                arguments = json.loads(raw_args) if raw_args.strip() else {}
            except ValueError:
                raise LLMError("模型返回的工具参数不是合法 JSON，已中止本次执行。")
        elif isinstance(raw_args, dict):
            arguments = raw_args
        else:
            raise LLMError("模型返回的工具参数格式异常（应为 JSON 对象），已中止本次执行。")
        calls.append(ToolCall(id=item.get("id", ""), name=func.get("name", ""),
                              arguments=arguments))
    return calls


class LLMClient:
    """OpenAI 兼容聊天客户端。API Key 只存本地设置、只发往用户所选服务商。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 60.0, transport=None):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout
        self._transport = transport or _default_transport

    @classmethod
    def from_provider(cls, provider_key: str, api_key: str, model: str = "",
                      base_url_override: str = "", **kwargs) -> "LLMClient":
        preset = PROVIDERS.get(provider_key)
        if preset is None:
            raise LLMError("未知服务商：{}，请在设置中选择自定义服务商。".format(provider_key))
        base_url = base_url_override or preset["base_url"]
        return cls(base_url=base_url, api_key=api_key,
                   model=model or preset["default_model"], **kwargs)

    # -- 请求 ------------------------------------------------------------
    def chat(self, messages: list, tools: list | None = None,
             temperature: float = 0.2) -> LLMResponse:
        """发起一轮对话。``tools`` 为 OpenAI 风格的工具定义列表（可空）。"""
        if not self.base_url:
            raise LLMError("尚未配置模型服务地址（base_url），请先在设置页填写。")
        if not self.api_key:
            raise LLMError("尚未填写 API Key，请先在设置页填写你自己的密钥。")
        if not self.model:
            raise LLMError("尚未填写模型名，请先在设置页填写。")
        if not messages:
            raise LLMError("对话内容为空，无法发送请求。")
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + self.api_key}
        url = self.base_url + "/chat/completions"
        try:
            status, raw_body = self._transport(url, headers, body, self.timeout)
        except LLMError:
            raise
        except Exception as exc:  # 网络层异常（断网、超时、DNS 等）
            raise LLMError("连接模型服务失败：{}。请检查网络与服务地址。".format(exc))
        # 先判状态码再解析成功体：错误响应常是网关/代理返回的 HTML 页，
        # 若先按 JSON 解析会把 401/429 误报成「无法解析」而丢掉关键提示。
        if status != 200:
            detail = ""
            try:
                err_data = json.loads(raw_body.decode("utf-8"))
            except Exception:
                err_data = None
            if isinstance(err_data, dict):
                err = err_data.get("error") or {}
                detail = err.get("message", "") if isinstance(err, dict) else str(err)
            hint = {
                401: "API Key 无效或已过期，请在设置页检查。",
                403: "当前账号无权调用该模型，请检查服务商控制台。",
                404: "接口地址或模型名不存在，请在设置页核对。",
                429: "请求过于频繁或额度不足，请稍后再试。",
            }.get(status, "请稍后重试，或检查服务商状态。")
            raise LLMError("模型服务请求失败（HTTP {}）：{} {}".format(
                status, detail, hint).strip())
        try:
            data = json.loads(raw_body.decode("utf-8"))
        except Exception:
            raise LLMError("模型服务返回了无法解析的内容（HTTP {}）。".format(status))
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("模型服务响应结构异常，缺少 choices/message 字段。")
        return LLMResponse(content=message.get("content") or "",
                           tool_calls=_parse_tool_calls(message), raw=data)
