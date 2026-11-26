"""RDB GeoMind 核心逻辑包。

本包刻意与 QGIS / PyQt 完全解耦：所有模块只依赖 Python 标准库，
因此可以在没有安装 QGIS 的机器上做无头测试。真正调用 QGIS 的
代码只存在于 backend.QgisBackend 中，且采用延迟导入。
"""

__all__ = [
    "license",
    "usage",
    "llm_client",
    "backend",
    "tools",
    "planner",
    "workflows",
]
