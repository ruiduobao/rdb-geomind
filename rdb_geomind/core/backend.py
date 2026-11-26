# -*- coding: utf-8 -*-
"""GIS 后端抽象层。

工具执行函数只依赖 :class:`GisBackend` 接口，不直接 import qgis，
因此同一套工具既能在 QGIS 里跑（QgisBackend），也能在无头测试里跑
（FakeBackend）。
"""

from __future__ import annotations

import abc
import re


class BackendError(Exception):
    """后端执行失败，消息为中文。"""


_GDAL_CALC_LETTERS = "ABCDEF"


def build_gdal_raster_calc(expression, inputs):
    """把 band_math 的变量表达式翻译成 gdal:rastercalculator 的入参。

    GDAL 栅格计算器只认单字母变量 A–F（参数 INPUT_A/BAND_A …），公式为
    gdalnumeric 语法；而本产品的工具层用变量名引用波段（如 NDVI 的
    ``(NIR-RED)/(NIR+RED)``，inputs 形如 ``{"NIR": ["图层名", 4]}``）。
    本函数做单趟替换（避免变量名与字母互相串改），返回
    ``(formula, [(letter, layer_name, band), ...])``。
    纯函数、无 qgis 依赖，可在无头环境单测。
    """
    if not inputs:
        raise BackendError("波段计算至少需要一个输入波段。")
    if len(inputs) > len(_GDAL_CALC_LETTERS):
        raise BackendError(
            "波段计算最多支持 {} 个输入变量，当前 {} 个。".format(
                len(_GDAL_CALC_LETTERS), len(inputs)))
    letters = {}
    entries = []
    for i, (var, spec) in enumerate(inputs.items()):
        layer_name, band = spec if isinstance(spec, (list, tuple)) else (spec, 1)
        letter = _GDAL_CALC_LETTERS[i]
        letters[var] = letter
        entries.append((letter, layer_name, int(band)))
    pattern = r"\b(" + "|".join(re.escape(v) for v in
                                sorted(letters, key=len, reverse=True)) + r")\b"
    formula = re.sub(pattern, lambda m: letters[m.group(1)], expression or "")
    return formula, entries


class GisBackend(abc.ABC):
    """GIS 操作抽象接口。所有方法返回新建/受影响图层的名称（字符串）或信息 dict。"""

    @abc.abstractmethod
    def load_vector(self, path: str, name: str = "") -> str: ...

    @abc.abstractmethod
    def load_raster(self, path: str, name: str = "") -> str: ...

    @abc.abstractmethod
    def list_layers(self) -> list: ...

    @abc.abstractmethod
    def set_style(self, layer: str, mode: str = "single", color: str = "#3a7bd5",
                  opacity: float = 1.0, field: str = "") -> str: ...

    @abc.abstractmethod
    def reproject(self, layer: str, target_crs: str, output: str = "") -> str: ...

    @abc.abstractmethod
    def clip_by_mask(self, layer: str, mask_layer: str, output: str = "") -> str: ...

    @abc.abstractmethod
    def mosaic(self, layers: list, output: str = "") -> str: ...

    @abc.abstractmethod
    def band_math(self, expression: str, inputs: dict, output: str = "") -> str: ...

    @abc.abstractmethod
    def band_composite(self, layer: str, red_band: int, green_band: int,
                       blue_band: int) -> str: ...

    @abc.abstractmethod
    def zonal_stats(self, raster: str, zones: str, stats: list,
                    output: str = "") -> str: ...

    @abc.abstractmethod
    def buffer(self, layer: str, distance: float, output: str = "") -> str: ...

    @abc.abstractmethod
    def export_layer(self, layer: str, path: str, driver: str = "") -> str: ...

    @abc.abstractmethod
    def zoom_to_layer(self, layer: str) -> str: ...

    @abc.abstractmethod
    def export_layout(self, params: dict) -> str: ...

    @abc.abstractmethod
    def join_attributes(self, layer: str, join_layer: str, layer_field: str,
                        join_field: str, output: str = "") -> str: ...

    @abc.abstractmethod
    def spatial_join(self, layer: str, join_layer: str, output: str = "") -> str: ...

    @abc.abstractmethod
    def field_calculator(self, layer: str, field_name: str, expression: str,
                         field_type: str = "float", output: str = "") -> str: ...

    @abc.abstractmethod
    def fix_geometries(self, layer: str, output: str = "") -> str: ...

    @abc.abstractmethod
    def extract_by_attribute(self, layer: str, expression: str,
                             output: str = "") -> str: ...

    @abc.abstractmethod
    def dissolve(self, layer: str, field: str = "", output: str = "") -> str: ...

    @abc.abstractmethod
    def merge_vector_layers(self, layers: list, output: str = "") -> str: ...


class FakeBackend(GisBackend):
    """无头测试用假后端：记录每次调用，维护一份假图层清单，不做真实计算。"""

    def __init__(self, existing_layers: list | None = None):
        self.calls: list = []  # [(method, kwargs), ...]
        self.layers: list = list(existing_layers or [])
        self._counter = 0

    def _record(self, method: str, result_name: str = "", **kwargs):
        self.calls.append((method, kwargs))
        if result_name and result_name not in self.layers:
            self.layers.append(result_name)
        return result_name

    def _auto_name(self, prefix: str, given: str = "") -> str:
        if given:
            return given
        self._counter += 1
        return "{}_{}".format(prefix, self._counter)

    def load_vector(self, path, name=""):
        return self._record("load_vector", self._auto_name("vector", name),
                            path=path, name=name)

    def load_raster(self, path, name=""):
        return self._record("load_raster", self._auto_name("raster", name),
                            path=path, name=name)

    def list_layers(self):
        self.calls.append(("list_layers", {}))
        return list(self.layers)

    def set_style(self, layer, mode="single", color="#3a7bd5", opacity=1.0, field=""):
        return self._record("set_style", layer, layer=layer, mode=mode, color=color,
                            opacity=opacity, field=field)

    def reproject(self, layer, target_crs, output=""):
        return self._record("reproject", self._auto_name("reprojected", output),
                            layer=layer, target_crs=target_crs, output=output)

    def clip_by_mask(self, layer, mask_layer, output=""):
        return self._record("clip_by_mask", self._auto_name("clipped", output),
                            layer=layer, mask_layer=mask_layer, output=output)

    def mosaic(self, layers, output=""):
        return self._record("mosaic", self._auto_name("mosaic", output),
                            layers=list(layers), output=output)

    def band_math(self, expression, inputs, output=""):
        return self._record("band_math", self._auto_name("bandmath", output),
                            expression=expression, inputs=dict(inputs), output=output)

    def band_composite(self, layer, red_band, green_band, blue_band):
        return self._record("band_composite", layer, layer=layer, red_band=red_band,
                            green_band=green_band, blue_band=blue_band)

    def zonal_stats(self, raster, zones, stats, output=""):
        return self._record("zonal_stats", self._auto_name("zonal", output),
                            raster=raster, zones=zones, stats=list(stats), output=output)

    def buffer(self, layer, distance, output=""):
        return self._record("buffer", self._auto_name("buffer", output),
                            layer=layer, distance=distance, output=output)

    def export_layer(self, layer, path, driver=""):
        return self._record("export_layer", layer, layer=layer, path=path, driver=driver)

    def zoom_to_layer(self, layer):
        return self._record("zoom_to_layer", layer, layer=layer)

    def export_layout(self, params):
        return self._record("export_layout", params.get("output_path", "layout.pdf"),
                            params=dict(params))

    def join_attributes(self, layer, join_layer, layer_field, join_field, output=""):
        return self._record("join_attributes", self._auto_name("joined", output),
                            layer=layer, join_layer=join_layer,
                            layer_field=layer_field, join_field=join_field, output=output)

    def spatial_join(self, layer, join_layer, output=""):
        return self._record("spatial_join", self._auto_name("spatial_joined", output),
                            layer=layer, join_layer=join_layer, output=output)

    def field_calculator(self, layer, field_name, expression, field_type="float",
                         output=""):
        return self._record("field_calculator", self._auto_name("fieldcalc", output),
                            layer=layer, field_name=field_name, expression=expression,
                            field_type=field_type, output=output)

    def fix_geometries(self, layer, output=""):
        return self._record("fix_geometries", self._auto_name("fixed", output),
                            layer=layer, output=output)

    def extract_by_attribute(self, layer, expression, output=""):
        return self._record("extract_by_attribute", self._auto_name("extracted", output),
                            layer=layer, expression=expression, output=output)

    def dissolve(self, layer, field="", output=""):
        return self._record("dissolve", self._auto_name("dissolved", output),
                            layer=layer, field=field, output=output)

    def merge_vector_layers(self, layers, output=""):
        return self._record("merge_vector_layers", self._auto_name("merged", output),
                            layers=list(layers), output=output)


class QgisBackend(GisBackend):
    """真实后端：调用 qgis.core 与 processing，仅在 QGIS 运行时内可用。

    本类在模块导入时不 import qgis，所有导入都延迟到方法调用时，
    保证无 QGIS 环境下 import 本模块不会失败。
    """

    def __init__(self, iface=None):
        self.iface = iface

    # -- 内部工具 ---------------------------------------------------------
    def _project(self):
        from qgis.core import QgsProject
        return QgsProject.instance()

    def _layer_by_name(self, name: str):
        matches = self._project().mapLayersByName(name)
        if not matches:
            raise BackendError("工程中找不到图层：{}".format(name))
        return matches[0]

    def _run_processing(self, algorithm: str, params: dict):
        import processing  # QGIS processing 插件，运行时注入
        return processing.run(algorithm, params)

    def _add_result(self, layer) -> str:
        self._project().addMapLayer(layer)
        return layer.name()

    # -- 接口实现 ---------------------------------------------------------
    def load_vector(self, path, name=""):
        from qgis.core import QgsVectorLayer
        layer = QgsVectorLayer(path, name or path.rsplit("/", 1)[-1], "ogr")
        if not layer.isValid():
            raise BackendError("矢量图层加载失败：{}".format(path))
        return self._add_result(layer)

    def load_raster(self, path, name=""):
        from qgis.core import QgsRasterLayer
        layer = QgsRasterLayer(path, name or path.rsplit("/", 1)[-1])
        if not layer.isValid():
            raise BackendError("栅格图层加载失败：{}".format(path))
        return self._add_result(layer)

    def list_layers(self):
        return [layer.name() for layer in self._project().mapLayers().values()]

    def set_style(self, layer, mode="single", color="#3a7bd5", opacity=1.0, field=""):
        from qgis.core import (QgsCategorizedSymbolRenderer, QgsFillSymbol,
                               QgsLineSymbol, QgsMarkerSymbol, QgsRendererCategory)
        from qgis.PyQt.QtGui import QColor
        target = self._layer_by_name(layer)
        target.setOpacity(opacity)
        if mode == "categorized" and field:
            values = sorted({str(f[field]) for f in target.getFeatures()
                             if f[field] is not None})
            categories = []
            palette = ["#3a7bd5", "#e4572e", "#2ca25f", "#756bb1", "#d7301f",
                       "#fee08b", "#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3"]
            for idx, value in enumerate(values):
                symbol = QgsFillSymbol.createSimple(
                    {"color": palette[idx % len(palette)]})
                categories.append(QgsRendererCategory(value, symbol, value))
            target.setRenderer(QgsCategorizedSymbolRenderer(field, categories))
        else:
            symbol_layer_color = QColor(color)
            renderer = target.renderer()
            if renderer is not None and hasattr(renderer, "symbol"):
                symbol = renderer.symbol()
                if symbol is not None:
                    symbol.setColor(symbol_layer_color)
        target.triggerRepaint()
        return layer

    def reproject(self, layer, target_crs, output=""):
        src = self._layer_by_name(layer)
        is_vector = hasattr(src, "geometryType")
        algorithm = "native:reprojectlayer" if is_vector else "gdal:warpreproject"
        params = {"INPUT": src, "TARGET_CRS": target_crs,
                  "OUTPUT": output or "TEMPORARY_OUTPUT"}
        result = self._run_processing(algorithm, params)
        out = result["OUTPUT"]
        if isinstance(out, str):
            return out
        return self._add_result(out)

    def clip_by_mask(self, layer, mask_layer, output=""):
        src = self._layer_by_name(layer)
        mask = self._layer_by_name(mask_layer)
        is_vector = hasattr(src, "geometryType")
        if is_vector:
            result = self._run_processing(
                "native:clip", {"INPUT": src, "OVERLAY": mask,
                                "OUTPUT": output or "TEMPORARY_OUTPUT"})
        else:
            result = self._run_processing(
                "gdal:cliprasterbymasklayer",
                {"INPUT": src, "MASK": mask, "OUTPUT": output or "TEMPORARY_OUTPUT"})
        out = result["OUTPUT"]
        return out if isinstance(out, str) else self._add_result(out)

    def mosaic(self, layers, output=""):
        sources = [self._layer_by_name(name) for name in layers]
        result = self._run_processing(
            "gdal:merge", {"INPUT": sources, "SEPARATE": False,
                           "OUTPUT": output or "TEMPORARY_OUTPUT"})
        out = result["OUTPUT"]
        return out if isinstance(out, str) else self._add_result(out)

    def band_math(self, expression, inputs, output=""):
        # inputs: {"NIR": (layer, band), ...}，表达式用变量名引用波段；
        # GDAL 计算器只认单字母 A–F，先经 build_gdal_raster_calc 翻译。
        formula, entries = build_gdal_raster_calc(expression, inputs)
        params = {"FORMULA": formula, "OUTPUT": output or "TEMPORARY_OUTPUT"}
        for letter, layer_name, band in entries:
            params["INPUT_" + letter] = self._layer_by_name(layer_name)
            params["BAND_" + letter] = band
        result = self._run_processing("gdal:rastercalculator", params)
        out = result["OUTPUT"]
        return out if isinstance(out, str) else self._add_result(out)

    def band_composite(self, layer, red_band, green_band, blue_band):
        from qgis.core import QgsMultiBandColorRenderer, QgsContrastEnhancement
        target = self._layer_by_name(layer)
        renderer = QgsMultiBandColorRenderer(
            target.dataProvider(), red_band, green_band, blue_band,
            QgsContrastEnhancement(target.dataProvider(), red_band),
            QgsContrastEnhancement(target.dataProvider(), green_band),
            QgsContrastEnhancement(target.dataProvider(), blue_band))
        target.setRenderer(renderer)
        target.triggerRepaint()
        return layer

    def zonal_stats(self, raster, zones, stats, output=""):
        stat_map = {"count": 0, "sum": 1, "mean": 2, "median": 3, "min": 5, "max": 6}
        codes = [stat_map[s] for s in (stats or ["mean"]) if s in stat_map] or [2]
        result = self._run_processing(
            "native:zonalstatisticsfb",
            {"INPUT": self._layer_by_name(zones),
             "INPUT_RASTER": self._layer_by_name(raster),
             "RASTER_BAND": 1, "COLUMN_PREFIX": "zs_",
             "STATISTICS": codes, "OUTPUT": output or "TEMPORARY_OUTPUT"})
        out = result["OUTPUT"]
        return out if isinstance(out, str) else self._add_result(out)

    def buffer(self, layer, distance, output=""):
        result = self._run_processing(
            "native:buffer",
            {"INPUT": self._layer_by_name(layer), "DISTANCE": distance,
             "SEGMENTS": 8, "DISSOLVE": False, "OUTPUT": output or "TEMPORARY_OUTPUT"})
        out = result["OUTPUT"]
        return out if isinstance(out, str) else self._add_result(out)

    def export_layer(self, layer, path, driver=""):
        from qgis.core import QgsVectorFileWriter, QgsCoordinateTransformContext
        src = self._layer_by_name(layer)
        options = QgsVectorFileWriter.SaveVectorOptions()
        if driver:
            options.driverName = driver
        error = QgsVectorFileWriter.writeAsVectorFormatV3(
            src, path, QgsCoordinateTransformContext(), options)
        if error[0] != QgsVectorFileWriter.NoError:
            raise BackendError("导出失败：{}".format(error[1] if len(error) > 1 else error))
        return path

    def _processing_output(self, algorithm: str, params: dict) -> str:
        result = self._run_processing(algorithm, params)
        out = result["OUTPUT"]
        return out if isinstance(out, str) else self._add_result(out)

    def join_attributes(self, layer, join_layer, layer_field, join_field, output=""):
        return self._processing_output(
            "native:joinattributestable",
            {"INPUT": self._layer_by_name(layer),
             "JOIN": self._layer_by_name(join_layer),
             "INPUT_FIELD": layer_field, "JOIN_FIELD": join_field,
             "JOIN_FIELDS": [], "METHOD": 1, "DISCARD_NONMATCHING": False,
             "PREFIX": "", "OUTPUT": output or "TEMPORARY_OUTPUT"})

    def spatial_join(self, layer, join_layer, output=""):
        return self._processing_output(
            "native:joinattributesbylocation",
            {"INPUT": self._layer_by_name(layer),
             "JOIN": self._layer_by_name(join_layer),
             "PREDICATE": [0], "JOIN_FIELDS": [], "METHOD": 1,
             "DISCARD_NONMATCHING": False, "PREFIX": "",
             "OUTPUT": output or "TEMPORARY_OUTPUT"})

    def field_calculator(self, layer, field_name, expression, field_type="float",
                         output=""):
        type_codes = {"float": 0, "integer": 1, "string": 2}
        return self._processing_output(
            "native:fieldcalculator",
            {"INPUT": self._layer_by_name(layer),
             "FIELD_NAME": field_name,
             "FIELD_TYPE": type_codes.get(field_type, 0),
             "FIELD_LENGTH": 20 if field_type != "string" else 254,
             "FIELD_PRECISION": 6 if field_type == "float" else 0,
             "FORMULA": expression,
             "OUTPUT": output or "TEMPORARY_OUTPUT"})

    def fix_geometries(self, layer, output=""):
        return self._processing_output(
            "native:fixgeometries",
            {"INPUT": self._layer_by_name(layer),
             "OUTPUT": output or "TEMPORARY_OUTPUT"})

    def extract_by_attribute(self, layer, expression, output=""):
        return self._processing_output(
            "native:extractbyexpression",
            {"INPUT": self._layer_by_name(layer), "EXPRESSION": expression,
             "OUTPUT": output or "TEMPORARY_OUTPUT"})

    def dissolve(self, layer, field="", output=""):
        return self._processing_output(
            "native:dissolve",
            {"INPUT": self._layer_by_name(layer),
             "FIELD": [field] if field else [],
             "OUTPUT": output or "TEMPORARY_OUTPUT"})

    def merge_vector_layers(self, layers, output=""):
        return self._processing_output(
            "native:mergevectorlayers",
            {"LAYERS": [self._layer_by_name(n) for n in layers],
             "OUTPUT": output or "TEMPORARY_OUTPUT"})

    def zoom_to_layer(self, layer):
        if self.iface is None:
            raise BackendError("无界面环境下无法缩放画布。")
        target = self._layer_by_name(layer)
        self.iface.mapCanvas().setExtent(target.extent())
        self.iface.mapCanvas().refresh()
        return layer

    def export_layout(self, params):
        """用 QgsPrintLayout 按参数组装版面并导出 PDF / 图片（仅 QGIS 内可用）。

        版面元素：地图框、标题、指北针、比例尺、图例，显隐与纸张/分辨率
        全部由 params 控制（由 workflows.build_layout_export 生成）。
        """
        from qgis.core import (QgsLayoutExporter, QgsLayoutItemLabel,
                               QgsLayoutItemLegend, QgsLayoutItemMap,
                               QgsLayoutItemPicture, QgsLayoutItemScaleBar,
                               QgsLayoutPoint, QgsLayoutSize, QgsPrintLayout,
                               QgsUnitTypes)
        from qgis.PyQt.QtGui import QFont
        project = self._project()
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        page = layout.pageCollection().page(0)
        paper = str(params.get("paper", "A4")).upper()
        width, height = (297.0, 210.0) if paper != "A3" else (420.0, 297.0)
        if params.get("orientation") == "portrait":
            width, height = height, width
        page.setPageSize(QgsLayoutSize(width, height, QgsUnitTypes.LayoutMillimeters))

        margin = 12.0
        map_item = QgsLayoutItemMap(layout)
        map_item.attemptMove(QgsLayoutPoint(margin, margin + 14,
                                            QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(width - 2 * margin, height - 2 * margin - 14,
                                             QgsUnitTypes.LayoutMillimeters))
        layers = [self._layer_by_name(n) for n in params.get("layers") or []]
        if layers:
            map_item.setLayers(layers)
        layout.addLayoutItem(map_item)
        map_item.zoomToExtent(project.mapLayers().values().__iter__().__next__().extent()
                              if project.mapLayers() else map_item.extent())

        title = QgsLayoutItemLabel(layout)
        title.setText(params.get("title", ""))
        title.setFont(QFont("Sans Serif", 16, QFont.Bold))
        title.attemptMove(QgsLayoutPoint(margin, 4, QgsUnitTypes.LayoutMillimeters))
        title.attemptResize(QgsLayoutSize(width - 2 * margin, 10,
                                          QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(title)

        if params.get("show_north_arrow", True):
            arrow = QgsLayoutItemPicture(layout)
            try:
                # QgsLayoutItemPicture 没有 setNorthArrowStyle 方法（官方 API
                # 核对 2026-11-26）；指北针就是一张 SVG 图片 + 朝向同步设置。
                arrow.setPicturePath(":/images/themes/default/north_arrows/arrow_04.svg")
                arrow.setLinkedMap(map_item)
                arrow.setNorthMode(QgsLayoutItemPicture.GridNorth)
            except Exception:
                pass  # 指北针属装饰元素，设置失败不阻断导出
            arrow.attemptMove(QgsLayoutPoint(width - margin - 12, margin + 16,
                                             QgsUnitTypes.LayoutMillimeters))
            arrow.attemptResize(QgsLayoutSize(10, 14, QgsUnitTypes.LayoutMillimeters))
            layout.addLayoutItem(arrow)

        if params.get("show_scale_bar", True):
            scale_bar = QgsLayoutItemScaleBar(layout)
            scale_bar.setStyle("Single Box")
            scale_bar.setLinkedMap(map_item)
            scale_bar.applyDefaultSize()
            scale_bar.attemptMove(QgsLayoutPoint(margin, height - margin - 2,
                                                 QgsUnitTypes.LayoutMillimeters))
            layout.addLayoutItem(scale_bar)

        if params.get("show_legend", True):
            legend = QgsLayoutItemLegend(layout)
            legend.setTitle("图例")
            legend.setLinkedMap(map_item)
            legend.attemptMove(QgsLayoutPoint(width - margin - 42, height - margin - 40,
                                              QgsUnitTypes.LayoutMillimeters))
            layout.addLayoutItem(legend)

        output_path = params.get("output_path", "")
        exporter = QgsLayoutExporter(layout)
        settings = QgsLayoutExporter.PdfExportSettings()
        settings.dpi = int(params.get("dpi", 300))
        if output_path.lower().endswith((".png", ".jpg", ".jpeg")):
            image_settings = QgsLayoutExporter.ImageExportSettings()
            image_settings.dpi = settings.dpi
            result = exporter.exportToImage(output_path, image_settings)
        else:
            result = exporter.exportToPdf(output_path, settings)
        if result != QgsLayoutExporter.Success:
            raise BackendError("版面导出失败，错误码：{}".format(result))
        return output_path
