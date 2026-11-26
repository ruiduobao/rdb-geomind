# -*- coding: utf-8 -*-
"""RDB GeoMind 授权码体系（HMAC-SHA256 签名）。

授权码格式（自有设计）::

    RDBGM-<载荷>-<签名>

其中载荷与签名均为 Base32 编码后按 5 字符分组、以 ``-`` 连接的可读字符串。

载荷为紧凑 JSON（v2，定版三档）::

    {"email": "...", "edition": "pro", "plan": "monthly", "exp": "YYYY-MM-DD", "v": 2}
    {"email": "...", "edition": "pro", "plan": "permanent", "v": 2}

- ``plan=monthly``：Pro 月付版，带 ``exp`` 到期日，到期后验签失败、回落免费版；
- ``plan=permanent``：Pro 永久版，无 ``exp`` 字段，验签时永不过期。

旧格式码兼容口径（定版决定，README 同步写明）：v1 载荷没有 ``plan`` 字段，
一律按 ``monthly`` 处理——用其原有 ``exp`` 校验到期日，未过期照常为 Pro，
过期则失效。不拒绝旧码，避免已购用户被锁在门外。

签名 = HMAC-SHA256(secret, 载荷原始字节) 的前 20 字节，防篡改。

安全说明：签发密钥只能通过环境变量 ``RDB_GEOMIND_LICENSE_SECRET`` 注入，
仓库中仅保留示例假密钥，**上线前必须更换为随机长密钥并妥善保管**，
切勿把真实密钥写进代码、仓库或安装包。
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import json
import os

#: 环境变量名：签发 / 验签共用密钥从这里读取。
SECRET_ENV_VAR = "RDB_GEOMIND_LICENSE_SECRET"

#: 示例假密钥，仅用于本地开发与自动化测试。上线前必须更换！
EXAMPLE_DEV_SECRET = "EXAMPLE-DEV-SECRET-REPLACE-BEFORE-RELEASE"

CODE_PREFIX = "RDBGM"
_SIGNATURE_BYTES = 20  # 160 bit 签名，足够防伪造且码长可控
_PAYLOAD_VERSION = 2
_SUPPORTED_VERSIONS = (1, 2)  # v1 为旧格式（无 plan），按 monthly 兼容

PLAN_MONTHLY = "monthly"
PLAN_PERMANENT = "permanent"
PLANS = (PLAN_MONTHLY, PLAN_PERMANENT)


class LicenseError(Exception):
    """授权相关错误，消息为可直接展示给用户的中文。"""


def get_secret(explicit: str | None = None) -> str:
    """按 显式参数 > 环境变量 > 示例假密钥 的顺序取得密钥。"""
    if explicit:
        return explicit
    env_value = os.environ.get(SECRET_ENV_VAR, "").strip()
    if env_value:
        return env_value
    return EXAMPLE_DEV_SECRET


def _b32_grouped(raw: bytes) -> str:
    text = base64.b32encode(raw).decode("ascii").rstrip("=")
    return "-".join(text[i:i + 5] for i in range(0, len(text), 5))


def _b32_ungroup(text: str) -> bytes:
    cleaned = text.replace("-", "").strip().upper()
    padding = "=" * ((8 - len(cleaned) % 8) % 8)
    return base64.b32decode(cleaned + padding)


def _sign(payload_bytes: bytes, secret: str) -> bytes:
    digest = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    return digest[:_SIGNATURE_BYTES]


def issue_license(email: str, days: int | None = None, secret: str | None = None,
                  today: _dt.date | None = None,
                  plan: str = PLAN_MONTHLY) -> str:
    """为 ``email`` 签发一张 Pro 授权码。

    - ``plan="monthly"``：月付版，须给 ``days``（有效天数），
      到期日 = 签发日 + days 天（含到期日当天可用，见 verify_license）。
    - ``plan="permanent"``：永久版，忽略 ``days``，载荷不带到期日，永不过期。
    """
    email = (email or "").strip()
    if not email or "@" not in email:
        raise LicenseError("邮箱格式不正确，无法签发授权码。")
    if plan not in PLANS:
        raise LicenseError("未知授权类型：{}（可选 monthly / permanent）。".format(plan))
    payload = {
        "email": email,
        "edition": "pro",
        "plan": plan,
        "v": _PAYLOAD_VERSION,
    }
    if plan == PLAN_MONTHLY:
        if days is None or isinstance(days, bool) \
                or not isinstance(days, (int, float)) or days <= 0:
            raise LicenseError("月付版授权必须给出正整数有效天数（days）。")
        if days > 36500:
            raise LicenseError("有效天数过大：{} 天（上限 36500 天）。".format(days))
        start = today or _dt.date.today()
        expire = start + _dt.timedelta(days=days)
        payload["exp"] = expire.isoformat()
    payload_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
    signature = _sign(payload_bytes, get_secret(secret))
    return "{}-{}-{}".format(CODE_PREFIX, _b32_grouped(payload_bytes),
                             _b32_grouped(signature))


def effective_plan(payload: dict) -> str:
    """返回载荷的实际授权类型；旧格式（无 plan）按 monthly 兼容处理。"""
    return payload.get("plan") or PLAN_MONTHLY


def verify_license(code: str, secret: str | None = None,
                   today: _dt.date | None = None) -> dict:
    """验签并校验有效期。成功返回载荷 dict，失败抛 LicenseError（中文）。

    permanent 码永不过期；monthly 码（含无 plan 的旧格式码）按 exp 校验。
    """
    if not code or not isinstance(code, str):
        raise LicenseError("授权码为空。")
    text = code.strip().upper()
    if not text.startswith(CODE_PREFIX + "-"):
        raise LicenseError("授权码格式不正确：应以 RDBGM- 开头。")
    body = text[len(CODE_PREFIX) + 1:]
    parts = body.split("-")
    # 载荷分组数不固定，按签名固定长度（20 字节 -> 32 个 Base32 字符）反推切分点。
    sig_chars = len(base64.b32encode(b"\x00" * _SIGNATURE_BYTES).decode().rstrip("="))
    joined = "".join(parts)
    if len(joined) <= sig_chars:
        raise LicenseError("授权码长度不正确，可能被截断。")
    payload_part, sig_part = joined[:-sig_chars], joined[-sig_chars:]
    try:
        payload_bytes = _b32_ungroup(payload_part)
        signature = _b32_ungroup(sig_part)
    except Exception:
        raise LicenseError("授权码包含非法字符，解码失败。")
    expected = _sign(payload_bytes, get_secret(secret))
    if not hmac.compare_digest(signature, expected):
        raise LicenseError("授权码签名校验失败：授权码无效或已被篡改。")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        raise LicenseError("授权码载荷损坏，无法解析。")
    if payload.get("v") not in _SUPPORTED_VERSIONS:
        raise LicenseError("授权码版本不受支持，请联系锐多宝 RDB 获取新码。")
    plan = effective_plan(payload)
    if plan not in PLANS:
        raise LicenseError("授权码类型不受支持，请联系锐多宝 RDB 获取新码。")
    if plan == PLAN_PERMANENT:
        return payload  # 永久版：无到期日，永不过期
    exp_text = payload.get("exp", "")
    try:
        expire = _dt.date.fromisoformat(exp_text)
    except ValueError:
        raise LicenseError("授权码到期日字段损坏。")
    now = today or _dt.date.today()
    if now > expire:
        raise LicenseError("授权码已于 {} 到期，请续费后获取新码。".format(exp_text))
    return payload


def is_pro(code: str | None, secret: str | None = None,
           today: _dt.date | None = None) -> bool:
    """便捷判断：授权码有效且为 pro 版时返回 True，任何异常都返回 False。"""
    if not code:
        return False
    try:
        payload = verify_license(code, secret=secret, today=today)
    except LicenseError:
        return False
    return payload.get("edition") == "pro"
