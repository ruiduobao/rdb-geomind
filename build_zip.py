#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 rdb_geomind 插件打包为可通过 QGIS「从 ZIP 安装插件」安装的 zip 包。

用法：python3 build_zip.py
产物：dist/rdb_geomind-<version>.zip（顶层目录为 rdb_geomind/）。
"""

import os
import re
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.join(ROOT, "rdb_geomind")
DIST_DIR = os.path.join(ROOT, "dist")

EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo")


def read_version() -> str:
    with open(os.path.join(PLUGIN_DIR, "metadata.txt"), encoding="utf-8") as fh:
        match = re.search(r"^version\s*=\s*(.+)$", fh.read(), re.MULTILINE)
    return match.group(1).strip() if match else "0.0.0"


def main() -> str:
    os.makedirs(DIST_DIR, exist_ok=True)
    out_path = os.path.join(DIST_DIR, "rdb_geomind-{}.zip".format(read_version()))
    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(PLUGIN_DIR):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for filename in filenames:
                if filename.endswith(EXCLUDE_SUFFIXES):
                    continue
                full = os.path.join(dirpath, filename)
                arcname = os.path.relpath(full, ROOT)
                zf.write(full, arcname)
                count += 1
    print("已打包 {} 个文件 -> {}".format(count, out_path))
    return out_path


if __name__ == "__main__":
    main()
