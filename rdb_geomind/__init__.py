# -*- coding: utf-8 -*-
"""RDB GeoMind 插件入口（QGIS 插件规范要求提供 classFactory）。"""


def classFactory(iface):
    from .plugin import RdbGeoMindPlugin
    return RdbGeoMindPlugin(iface)
