#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RDB GeoMind 授权码发码工具（命令行，仅供运营方在本地/服务器使用）。

用法::

    export RDB_GEOMIND_LICENSE_SECRET='<上线真实密钥>'
    # Pro 月付版（10 元/月，默认 30 天）：
    python3 issue_license.py user@example.com --plan monthly --days 30
    # Pro 永久版（100 元一次性，无到期日）：
    python3 issue_license.py user@example.com --plan permanent

输出一行授权码，发给用户粘贴到插件设置页即可；签发后工具会自验回读，
打印邮箱 / 授权类型 / 到期日（永久版显示「永久」）。
密钥只从环境变量读取，切勿写进命令行参数（会留在 shell 历史里）；
仓库中的示例假密钥仅供测试，上线前必须更换。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from rdb_geomind.core import license as license_module  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="签发 RDB GeoMind Pro 授权码")
    parser.add_argument("email", help="购买用户的邮箱")
    parser.add_argument("--plan", choices=list(license_module.PLANS),
                        default=license_module.PLAN_MONTHLY,
                        help="授权类型：monthly=月付版（默认），permanent=永久版")
    parser.add_argument("--days", type=int, default=30,
                        help="月付版有效天数，默认 30；永久版忽略此参数")
    args = parser.parse_args(argv)

    secret = os.environ.get(license_module.SECRET_ENV_VAR, "").strip()
    if not secret:
        print("错误：未设置环境变量 {}，拒绝用示例密钥签发正式授权码。".format(
            license_module.SECRET_ENV_VAR), file=sys.stderr)
        return 2
    try:
        code = license_module.issue_license(
            args.email, days=args.days, secret=secret, plan=args.plan)
    except license_module.LicenseError as exc:
        print("签发失败：{}".format(exc), file=sys.stderr)
        return 1
    # 自验回读：确保发出去的码在插件端能验签通过。
    payload = license_module.verify_license(code, secret=secret)
    plan = license_module.effective_plan(payload)
    expire_text = "永久" if plan == license_module.PLAN_PERMANENT else payload["exp"]
    print("邮箱：{}  类型：{}  到期：{}  版本：{}".format(
        payload["email"], plan, expire_text, payload["edition"]))
    print(code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
