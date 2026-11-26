# -*- coding: utf-8 -*-
"""工具注册表：把 GIS 操作定义成「JSON Schema + 执行函数」。

每个工具的执行函数只依赖 GisBackend 接口，因此可以在 QGIS 内
（QgisBackend）与无头测试（FakeBackend）下共用同一套定义。
工具定义可直接转换成 OpenAI 风格的 tools 参数交给 LLM 做
function calling。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict          # JSON Schema（type=object）
    func: Callable            # func(backend, **args) -> 结果（图层名或信息）

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _obj(properties: dict, required: list | None = None) -> dict:
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


_STR = {"type": "string"}
_NUM = {"type": "number"}


def _build_registry() -> dict:
    tools = {}

    def add(tool: Tool):
        tools[tool.name] = tool

    add(Tool(
        name="load_vector",
        description="加载一个矢量文件（Shapefile/GeoJSON/GeoPackage 等）到当前工程。",
        parameters=_obj({"path": _STR, "name": _STR}, ["path"]),
        func=lambda b, path, name="": b.load_vector(path, name),
    ))
    add(Tool(
        name="load_raster",
        description="加载一个栅格文件（GeoTIFF 等）到当前工程。",
        parameters=_obj({"path": _STR, "name": _STR}, ["path"]),
        func=lambda b, path, name="": b.load_raster(path, name),
    ))
    add(Tool(
        name="list_layers",
        description="列出当前工程中的全部图层名称。",
        parameters=_obj({}),
        func=lambda b: {"layers": b.list_layers()},
    ))
    add(Tool(
        name="set_style",
        description="设置图层样式：single 单色填充、categorized 按字段分类着色，可调透明度。",
        parameters=_obj({
            "layer": _STR,
            "mode": {"type": "string", "enum": ["single", "categorized"]},
            "color": _STR,
            "opacity": _NUM,
            "field": _STR,
        }, ["layer"]),
        func=lambda b, layer, mode="single", color="#3a7bd5", opacity=1.0, field="":
            b.set_style(layer, mode, color, opacity, field),
    ))
    add(Tool(
        name="reproject",
        description="把图层重投影到目标坐标系（如 EPSG:4326、EPSG:3857）。",
        parameters=_obj({"layer": _STR, "target_crs": _STR, "output": _STR},
                        ["layer", "target_crs"]),
        func=lambda b, layer, target_crs, output="": b.reproject(layer, target_crs, output),
    ))
    add(Tool(
        name="clip_by_mask",
        description="用掩膜图层的范围裁剪目标图层（矢量按面裁剪、栅格按掩膜裁剪）。",
        parameters=_obj({"layer": _STR, "mask_layer": _STR, "output": _STR},
                        ["layer", "mask_layer"]),
        func=lambda b, layer, mask_layer, output="": b.clip_by_mask(layer, mask_layer, output),
    ))
    add(Tool(
        name="mosaic",
        description="把多幅栅格镶嵌合并为一幅。",
        parameters=_obj({"layers": {"type": "array", "items": _STR}, "output": _STR},
                        ["layers"]),
        func=lambda b, layers, output="": b.mosaic(layers, output),
    ))
    add(Tool(
        name="band_math",
        description=("波段计算：expression 中用变量名引用 inputs 里的波段，"
                     "如 NDVI 表达式 (NIR-RED)/(NIR+RED)，inputs 形如 "
                     "{\"NIR\": [\"图层名\", 4], \"RED\": [\"图层名\", 3]}。"),
        parameters=_obj({
            "expression": _STR,
            "inputs": {"type": "object"},
            "output": _STR,
        }, ["expression", "inputs"]),
        func=lambda b, expression, inputs, output="": b.band_math(expression, inputs, output),
    ))
    add(Tool(
        name="band_composite",
        description="设置栅格图层的 RGB 波段合成显示（指定红/绿/蓝各用哪个波段）。",
        parameters=_obj({
            "layer": _STR,
            "red_band": {"type": "integer"},
            "green_band": {"type": "integer"},
            "blue_band": {"type": "integer"},
        }, ["layer", "red_band", "green_band", "blue_band"]),
        func=lambda b, layer, red_band, green_band, blue_band:
            b.band_composite(layer, red_band, green_band, blue_band),
    ))
    add(Tool(
        name="zonal_stats",
        description="分区统计：按分区矢量统计栅格像元（mean/sum/min/max/median/count）。",
        parameters=_obj({
            "raster": _STR,
            "zones": _STR,
            "stats": {"type": "array", "items": _STR},
            "output": _STR,
        }, ["raster", "zones"]),
        func=lambda b, raster, zones, stats=None, output="":
            b.zonal_stats(raster, zones, stats or ["mean"], output),
    ))
    add(Tool(
        name="buffer",
        description="为矢量图层生成指定距离的缓冲区。",
        parameters=_obj({"layer": _STR, "distance": _NUM, "output": _STR},
                        ["layer", "distance"]),
        func=lambda b, layer, distance, output="": b.buffer(layer, distance, output),
    ))
    add(Tool(
        name="export_layer",
        description="把图层导出为文件（path 决定格式，可用 driver 指定驱动）。",
        parameters=_obj({"layer": _STR, "path": _STR, "driver": _STR},
                        ["layer", "path"]),
        func=lambda b, layer, path, driver="": b.export_layer(layer, path, driver),
    ))
    add(Tool(
        name="zoom_to_layer",
        description="把地图画布缩放并平移到指定图层的范围。",
        parameters=_obj({"layer": _STR}, ["layer"]),
        func=lambda b, layer: b.zoom_to_layer(layer),
    ))
    add(Tool(
        name="export_layout",
        description="按版面参数（标题、指北针、比例尺、图例、纸张、输出路径）生成出图导出。",
        parameters=_obj({
            "title": _STR,
            "output_path": _STR,
            "paper": _STR,
            "show_north_arrow": {"type": "boolean"},
            "show_scale_bar": {"type": "boolean"},
            "show_legend": {"type": "boolean"},
            "layers": {"type": "array", "items": _STR},
        }, ["title", "output_path"]),
        func=lambda b, **kw: b.export_layout(kw),
    ))
    add(Tool(
        name="join_attributes",
        description="属性连接：按两图层的关键字段把连接图层的属性拼到目标图层上（join）。",
        parameters=_obj({
            "layer": _STR, "join_layer": _STR,
            "layer_field": _STR, "join_field": _STR, "output": _STR,
        }, ["layer", "join_layer", "layer_field", "join_field"]),
        func=lambda b, layer, join_layer, layer_field, join_field, output="":
            b.join_attributes(layer, join_layer, layer_field, join_field, output),
    ))
    add(Tool(
        name="spatial_join",
        description="空间连接：按空间相交关系把连接图层的属性拼到目标图层上。",
        parameters=_obj({
            "layer": _STR, "join_layer": _STR, "output": _STR,
        }, ["layer", "join_layer"]),
        func=lambda b, layer, join_layer, output="":
            b.spatial_join(layer, join_layer, output),
    ))
    add(Tool(
        name="field_calculator",
        description="字段计算：用表达式新建/计算一个字段（如面积、比率、拼接文本）。",
        parameters=_obj({
            "layer": _STR, "field_name": _STR, "expression": _STR,
            "field_type": {"type": "string", "enum": ["float", "integer", "string"]},
            "output": _STR,
        }, ["layer", "field_name", "expression"]),
        func=lambda b, layer, field_name, expression, field_type="float", output="":
            b.field_calculator(layer, field_name, expression, field_type, output),
    ))
    add(Tool(
        name="fix_geometries",
        description="几何有效性修复：修复矢量图层的自交、无效几何等问题。",
        parameters=_obj({"layer": _STR, "output": _STR}, ["layer"]),
        func=lambda b, layer, output="": b.fix_geometries(layer, output),
    ))
    add(Tool(
        name="extract_by_attribute",
        description="按属性提取：用属性表达式（如 \"类型\" = '水田'）筛出子集为新图层。",
        parameters=_obj({
            "layer": _STR, "expression": _STR, "output": _STR,
        }, ["layer", "expression"]),
        func=lambda b, layer, expression, output="":
            b.extract_by_attribute(layer, expression, output),
    ))
    add(Tool(
        name="dissolve",
        description="融合：按指定字段（可空=全部融合为一）合并相邻/同类要素。",
        parameters=_obj({
            "layer": _STR, "field": _STR, "output": _STR,
        }, ["layer"]),
        func=lambda b, layer, field="", output="": b.dissolve(layer, field, output),
    ))
    add(Tool(
        name="merge_vector_layers",
        description="矢量合并：把多个结构一致的矢量图层合并为一个图层。",
        parameters=_obj({
            "layers": {"type": "array", "items": _STR}, "output": _STR,
        }, ["layers"]),
        func=lambda b, layers, output="": b.merge_vector_layers(layers, output),
    ))
    return tools


#: 全局工具注册表：name -> Tool
TOOL_REGISTRY: dict = _build_registry()

#: 常用遥感指数预置表达式（变量名与 band_math 的 inputs 对应）。
#: 波段变量口径（与各传感器模板的 band_map 一致）：
#:   BLUE 蓝光、GREEN 绿光、RED 红光、REDEDGE 红边、NIR 近红外、SWIR 短波红外（SWIR1）。
#: 每条含 source（公式出处）与 note（波段口径说明），界面与文档可直接引用。
INDEX_PRESETS = {
    "NDVI": {"label": "归一化植被指数 NDVI", "expression": "(NIR - RED) / (NIR + RED)",
             "bands": ["NIR", "RED"],
             "source": "Rouse et al. (1974)；Tucker (1979)",
             "note": "NIR 为近红外、RED 为红光，植被监测最常用指数。"},
    "NDWI": {"label": "归一化水体指数 NDWI（McFeeters）",
             "expression": "(GREEN - NIR) / (GREEN + NIR)",
             "bands": ["GREEN", "NIR"],
             "source": "McFeeters (1996)",
             "note": "GREEN 为绿光、NIR 为近红外，突出开放水体。"},
    "NDBI": {"label": "归一化建筑指数 NDBI", "expression": "(SWIR - NIR) / (SWIR + NIR)",
             "bands": ["SWIR", "NIR"],
             "source": "Zha, Gao & Ni (2003)",
             "note": "SWIR 取短波红外 1（SWIR1）、NIR 为近红外，突出建成区。"},
    "EVI": {"label": "增强植被指数 EVI",
            "expression": "2.5 * (NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1)",
            "bands": ["NIR", "RED", "BLUE"],
            "source": "Huete et al. (2002)，MODIS EVI 标准式（增益 2.5、C1=6、C2=7.5、L=1）",
            "note": "NIR 近红外、RED 红光、BLUE 蓝光；高植被覆盖区不易饱和。"},
    "SAVI": {"label": "土壤调节植被指数 SAVI",
             "expression": "1.5 * (NIR - RED) / (NIR + RED + 0.5)",
             "bands": ["NIR", "RED"],
             "source": "Huete (1988)，土壤亮度校正因子 L=0.5",
             "note": "NIR 近红外、RED 红光；稀疏植被/裸土背景下比 NDVI 更稳。"},
    "NDMI": {"label": "归一化水分指数 NDMI",
             "expression": "(NIR - SWIR) / (NIR + SWIR)",
             "bands": ["NIR", "SWIR"],
             "source": "Wilson & Sader (2002)；与 Gao (1996) NDWI 冠层水分式同构",
             "note": "NIR 近红外、SWIR 取短波红外 1（SWIR1），反映植被冠层含水量。"},
    "MNDWI": {"label": "改进归一化水体指数 MNDWI",
              "expression": "(GREEN - SWIR) / (GREEN + SWIR)",
              "bands": ["GREEN", "SWIR"],
              "source": "Xu (2006)",
              "note": "GREEN 绿光、SWIR 取短波红外 1（SWIR1）；比 NDWI 更能压制建成区噪声。"},
    "GNDVI": {"label": "绿光归一化植被指数 GNDVI",
              "expression": "(NIR - GREEN) / (NIR + GREEN)",
              "bands": ["NIR", "GREEN"],
              "source": "Gitelson, Kaufman & Merzlyak (1996)",
              "note": "NIR 近红外、GREEN 绿光，对叶绿素含量更敏感。"},
    "NDRE": {"label": "红边归一化植被指数 NDRE（红边 NDVI）",
             "expression": "(NIR - REDEDGE) / (NIR + REDEDGE)",
             "bands": ["NIR", "REDEDGE"],
             "source": "Barnes et al. (2000)；Gitelson & Merzlyak (1994) 红边口径",
             "note": "NIR 近红外、REDEDGE 红边波段（如哨兵二号 B05、高分六号红边波段），高覆盖植被不饱和。"},
    "BSI": {"label": "裸土指数 BSI",
            "expression": "((SWIR + RED) - (NIR + BLUE)) / ((SWIR + RED) + (NIR + BLUE))",
            "bands": ["SWIR", "RED", "NIR", "BLUE"],
            "source": "Rikimaru, Roy & Miyatake (2002)",
            "note": "SWIR 取短波红外 1（SWIR1）、RED 红光、NIR 近红外、BLUE 蓝光，突出裸地/未利用地。"},
}


def get_tool_schemas() -> list:
    """返回全部工具的 OpenAI 风格定义，供 LLM function calling 使用。"""
    return [tool.to_openai_schema() for tool in TOOL_REGISTRY.values()]


def _check_arg_type(tool_name: str, key: str, value, spec: dict):
    """按 schema 声明校验单个参数的类型与枚举，返回（可能经规范化的）值。

    LLM 偶尔会把数字写成字符串、把波段号写成浮点等——在校验层统一拦下
    并给中文提示（整数型遇恰为整数的浮点则顺手规范化），避免裸 TypeError
    一路冒泡到执行层才炸。
    """
    declared = (spec or {}).get("type")
    if declared == "string" and not isinstance(value, str):
        raise ValueError("工具 {} 的参数 {} 应为字符串，实际收到 {}。".format(
            tool_name, key, type(value).__name__))
    if declared == "number" and (isinstance(value, bool)
                                 or not isinstance(value, (int, float))):
        raise ValueError("工具 {} 的参数 {} 应为数字，实际收到 {!r}。".format(
            tool_name, key, value))
    if declared == "integer":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("工具 {} 的参数 {} 应为整数，实际收到 {!r}。".format(
                tool_name, key, value))
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError("工具 {} 的参数 {} 应为整数，实际收到 {!r}。".format(
                    tool_name, key, value))
            value = int(value)
    if declared == "boolean" and not isinstance(value, bool):
        raise ValueError("工具 {} 的参数 {} 应为布尔值，实际收到 {!r}。".format(
            tool_name, key, value))
    if declared == "array" and not isinstance(value, list):
        raise ValueError("工具 {} 的参数 {} 应为列表，实际收到 {}。".format(
            tool_name, key, type(value).__name__))
    if declared == "object" and not isinstance(value, dict):
        raise ValueError("工具 {} 的参数 {} 应为对象，实际收到 {}。".format(
            tool_name, key, type(value).__name__))
    enum = (spec or {}).get("enum")
    if enum is not None and value not in enum:
        raise ValueError("工具 {} 的参数 {} 只能取 {}，实际收到 {!r}。".format(
            tool_name, key, "、".join(str(e) for e in enum), value))
    return value


def validate_tool_arguments(name: str, arguments: dict) -> dict:
    """按工具 schema 校验参数：必填项齐全、类型正确、关键约束满足。

    返回规范化参数副本。纯函数、无后端依赖，计划生成与执行前都可调用；
    校验失败抛中文 ValueError。
    """
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        raise KeyError("未知工具：{}（可用工具：{}）".format(
            name, "、".join(sorted(TOOL_REGISTRY))))
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValueError("工具 {} 的参数必须是键值对对象，实际收到 {}。".format(
            name, type(arguments).__name__))
    args = dict(arguments)
    properties = (tool.parameters or {}).get("properties") or {}
    for key, value in list(args.items()):
        if key in properties and value is not None:
            args[key] = _check_arg_type(name, key, value, properties[key])
    required = (tool.parameters or {}).get("required") or []
    missing = [k for k in required if args.get(k) in (None, "", [])]
    if missing:
        raise ValueError("工具 {} 缺少必填参数：{}".format(name, "、".join(missing)))
    if name == "merge_vector_layers" and len(args.get("layers") or []) < 2:
        raise ValueError("矢量合并至少需要 2 个图层。")
    if name == "mosaic" and len(args.get("layers") or []) < 2:
        raise ValueError("影像镶嵌至少需要 2 幅影像。")
    if name == "field_calculator":
        field = str(args.get("field_name") or "")
        if not field.replace("_", "").isalnum() or field[:1].isdigit():
            raise ValueError("字段名只能由字母、数字与下划线组成且不能以数字开头：{}".format(field))
    if name == "buffer" and not (args.get("distance") or 0) > 0:
        raise ValueError("缓冲区距离必须大于 0。")
    return args


def execute_tool(name: str, backend, arguments: dict):
    """按名执行工具。未知工具抛 KeyError、参数不合规抛 ValueError（均为中文消息）。"""
    args = validate_tool_arguments(name, arguments)
    return TOOL_REGISTRY[name].func(backend, **args)
