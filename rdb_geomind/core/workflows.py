# -*- coding: utf-8 -*-
"""遥感常用工作流模板：参数化计划生成器。

与 LLM 路径的区别：模板不依赖大模型，用户在界面上填参数即可
一键生成 :class:`planner.Plan`，复用同一套执行引擎与确认流程。
这是本产品相对「纯聊天智能体」的超越点：高频遥感处理不必每次
让大模型现场拼工具，稳定、可复现、可批量。
"""

from __future__ import annotations

from .planner import KIND_TOOL, Plan, PlanStep
from .tools import INDEX_PRESETS


def _step(tool_name: str, arguments: dict, description: str = "") -> PlanStep:
    return PlanStep(kind=KIND_TOOL, name=tool_name, arguments=arguments,
                    description=description)


def _require(params: dict, *names: str):
    missing = [n for n in names if params.get(n) in (None, "", [])]
    if missing:
        raise ValueError("缺少必填参数：{}".format("、".join(missing)))


# ---------------------------------------------------------------- 模板实现
def build_clip_image(params: dict) -> Plan:
    """影像裁剪：按掩膜图层裁剪一幅影像，可选导出结果。"""
    _require(params, "raster_layer", "mask_layer")
    steps = [_step("clip_by_mask",
                   {"layer": params["raster_layer"],
                    "mask_layer": params["mask_layer"],
                    "output": params.get("output_name", "")},
                   "按掩膜裁剪影像")]
    if params.get("export_path"):
        steps.append(_step("export_layer",
                           {"layer": params.get("output_name") or "裁剪结果",
                            "path": params["export_path"]}, "导出裁剪结果"))
    return Plan(title="工作流：影像裁剪", steps=steps, source="workflow")


def build_mosaic_images(params: dict) -> Plan:
    """影像镶嵌：多幅影像合并为一幅。"""
    _require(params, "layers")
    if len(params["layers"]) < 2:
        raise ValueError("镶嵌至少需要 2 幅影像。")
    steps = [_step("mosaic", {"layers": list(params["layers"]),
                              "output": params.get("output_name", "")},
                   "镶嵌 {} 幅影像".format(len(params["layers"])))]
    return Plan(title="工作流：影像镶嵌", steps=steps, source="workflow")


def build_batch_reproject(params: dict) -> Plan:
    """批量重投影：对一组图层逐个生成重投影步骤。"""
    _require(params, "layers", "target_crs")
    steps = [_step("reproject",
                   {"layer": name, "target_crs": params["target_crs"], "output": ""},
                   "重投影 {} 到 {}".format(name, params["target_crs"]))
             for name in params["layers"]]
    return Plan(title="工作流：批量重投影", steps=steps, source="workflow")


def build_band_composite_and_index(params: dict) -> Plan:
    """波段合成与指数计算。

    mode=composite：按 red/green/blue 波段号设置真彩色/假彩色合成显示；
    mode=index：按 INDEX_PRESETS 中 10 种预置指数表达式做波段计算，
    band_map 给出各变量对应的波段号，如 {"NIR": 4, "RED": 3}。
    """
    _require(params, "layer", "mode")
    layer = params["layer"]
    if params["mode"] == "composite":
        _require(params, "red_band", "green_band", "blue_band")
        steps = [_step("band_composite",
                       {"layer": layer, "red_band": int(params["red_band"]),
                        "green_band": int(params["green_band"]),
                        "blue_band": int(params["blue_band"])},
                       "波段合成显示 R{} G{} B{}".format(
                           params["red_band"], params["green_band"], params["blue_band"]))]
        return Plan(title="工作流：波段合成", steps=steps, source="workflow")
    if params["mode"] == "index":
        index_name = str(params.get("index", "NDVI")).upper()
        preset = INDEX_PRESETS.get(index_name)
        if preset is None:
            raise ValueError("不支持的指数：{}（可选 {}）".format(
                index_name, " / ".join(sorted(INDEX_PRESETS))))
        band_map = params.get("band_map") or {}
        inputs = {}
        for var in preset["bands"]:
            if var not in band_map:
                raise ValueError("指数 {} 需要波段映射 {}，缺少 {} 的波段号。".format(
                    index_name, preset["bands"], var))
            inputs[var] = [layer, int(band_map[var])]
        steps = [_step("band_math",
                       {"expression": preset["expression"], "inputs": inputs,
                        "output": params.get("output_name", "")},
                       preset["label"])]
        return Plan(title="工作流：指数计算（{}）".format(index_name),
                    steps=steps, source="workflow")
    raise ValueError("mode 只能是 composite 或 index。")


def build_zonal_statistics(params: dict) -> Plan:
    """分区统计：按行政区/地块等分区矢量统计影像。"""
    _require(params, "raster", "zones")
    steps = [_step("zonal_stats",
                   {"raster": params["raster"], "zones": params["zones"],
                    "stats": params.get("stats") or ["mean"],
                    "output": params.get("output_name", "")},
                   "分区统计")]
    if params.get("export_path"):
        steps.append(_step("export_layer",
                           {"layer": params.get("output_name") or "分区统计结果",
                            "path": params["export_path"]}, "导出统计结果"))
    return Plan(title="工作流：分区统计", steps=steps, source="workflow")


def build_layout_export(params: dict) -> Plan:
    """出图排版导出：标题/指北针/比例尺/图例全部参数化，生成一条导出计划步。

    在 QGIS 内执行时由界面层按这些参数组装打印版面并导出 PDF/图片；
    计划本身即完整的版面规格，可保存复用。
    """
    _require(params, "title", "output_path")
    layout_params = {
        "title": params["title"],
        "output_path": params["output_path"],
        "paper": params.get("paper", "A4"),
        "orientation": params.get("orientation", "landscape"),
        "show_north_arrow": bool(params.get("show_north_arrow", True)),
        "show_scale_bar": bool(params.get("show_scale_bar", True)),
        "show_legend": bool(params.get("show_legend", True)),
        "show_grid": bool(params.get("show_grid", False)),
        "author": params.get("author", "锐多宝 RDB"),
        "layers": params.get("layers") or [],
        "dpi": int(params.get("dpi", 300)),
    }
    steps = [_step("export_layout", layout_params,
                   "版面导出：{}（{}，{} dpi）".format(
                       layout_params["title"], layout_params["paper"],
                       layout_params["dpi"]))]
    return Plan(title="工作流：出图排版导出", steps=steps, source="workflow")


# ---------------------------------------------------------- 传感器预置与处理链
#: 主流/国产传感器波段映射预置（按各传感器公开波段口径）。
#: bands 为「指数变量名 -> 原始波段编号」；indexes 为该传感器实际能算的指数
#: （无对应波段的指数不列入，选错时 builder 给中文报错）；composites 为常用
#: 波段组合（R, G, B 波段号）。哨兵二号/Landsat 的编号按原始波段编号计
#: （如哨兵 B08 记 8、B11 记 11），已堆栈重排的请按堆栈顺序改 band_map 走通用模板。
SENSOR_PRESETS = {
    "gf12_pms": {
        "label": "高分一号/二号 PMS",
        "bands": {"BLUE": 1, "GREEN": 2, "RED": 3, "NIR": 4},
        "indexes": ["NDVI", "EVI", "SAVI", "GNDVI", "NDWI"],
        "composites": {"真彩色": (3, 2, 1), "标准假彩色": (4, 3, 2)},
        "note": "高分一号/二号 PMS 多光谱 4 波段公开口径：1 蓝、2 绿、3 红、4 近红外；无短波红外与红边波段。",
    },
    "gf6_wfv": {
        "label": "高分六号 WFV（含红边）",
        "bands": {"BLUE": 1, "GREEN": 2, "RED": 3, "NIR": 4, "REDEDGE": 5},
        "indexes": ["NDVI", "EVI", "SAVI", "GNDVI", "NDWI", "NDRE"],
        "composites": {"真彩色": (3, 2, 1), "标准假彩色": (4, 3, 2), "红边假彩色": (4, 5, 3)},
        "note": "高分六号宽幅（WFV）相机公开口径：1 蓝、2 绿、3 红、4 近红外、5 红边 1（690–730nm）、6 红边 2（730–770nm）；本预置红边取红边 1。用 PMS 相机数据时无红边波段，请改用通用模板。",
    },
    "jilin1": {
        "label": "吉林一号多光谱",
        "bands": {"BLUE": 1, "GREEN": 2, "RED": 3, "NIR": 4},
        "indexes": ["NDVI", "EVI", "SAVI", "GNDVI", "NDWI"],
        "composites": {"真彩色": (3, 2, 1), "标准假彩色": (4, 3, 2)},
        "note": "吉林一号多光谱公开口径为蓝、绿、红、近红外 4 波段（1–4）；不同星型产品波段以随附元数据为准。",
    },
    "sentinel2": {
        "label": "哨兵二号（Sentinel-2 L2A）",
        "bands": {"BLUE": 2, "GREEN": 3, "RED": 4, "REDEDGE": 5, "NIR": 8, "SWIR": 11},
        "indexes": ["NDVI", "EVI", "SAVI", "GNDVI", "NDWI", "NDRE", "NDMI", "MNDWI", "NDBI", "BSI"],
        "composites": {"真彩色": (4, 3, 2), "标准假彩色": (8, 4, 3), "短波红外假彩色": (11, 8, 4)},
        "note": "哨兵二号波段编号按原始编号：B02 蓝、B03 绿、B04 红、B05 红边、B08 近红外（10m）、B11 短波红外 1（20m）；混分辨率指数计算前建议先重采样对齐。SCL 为 L2A 场景分类波段（云影=3、中云=8、高云=9、卷云=10）。",
    },
    "landsat89": {
        "label": "Landsat 8/9 OLI",
        "bands": {"BLUE": 2, "GREEN": 3, "RED": 4, "NIR": 5, "SWIR": 6},
        "indexes": ["NDVI", "EVI", "SAVI", "GNDVI", "NDWI", "NDMI", "MNDWI", "NDBI", "BSI"],
        "composites": {"真彩色": (4, 3, 2), "标准假彩色": (5, 4, 3), "短波红外假彩色": (6, 5, 4)},
        "note": "Landsat 8/9 OLI 波段编号按原始编号：B2 蓝、B3 绿、B4 红、B5 近红外、B6 短波红外 1；热红外为 TIRS B10。",
    },
}


def _index_step(layer: str, index_name: str, band_numbers: dict, output: str) -> PlanStep:
    """按指数预置与波段编号生成一条 band_math 计划步。"""
    preset = INDEX_PRESETS[index_name]
    inputs = {}
    for var in preset["bands"]:
        if var not in band_numbers:
            raise ValueError("指数 {} 需要 {} 波段，但当前波段映射里没有。".format(
                index_name, var))
        inputs[var] = [layer, int(band_numbers[var])]
    return _step("band_math",
                 {"expression": preset["expression"], "inputs": inputs, "output": output},
                 "{}（{}）".format(preset["label"], preset["source"]))


def _sensor_chain(sensor_key: str, params: dict, default_index: str = "NDVI",
                  title_suffix: str = "处理链") -> Plan:
    """传感器处理链：波段组合（可选）→ 指数计算 → 分区统计（可选）→ 导出（可选）。

    params: layer 必填；index 指数名（须在该传感器可算清单内）；
    composite 组合名（可空=不做组合步）；zones 分区图层（可空）；
    output_name 指数输出名；export_path 导出路径（可空）。
    """
    sensor = SENSOR_PRESETS[sensor_key]
    _require(params, "layer")
    layer = params["layer"]
    index_name = str(params.get("index") or default_index).upper()
    if index_name not in sensor["indexes"]:
        raise ValueError("{} 不能计算 {}（无对应波段）。该传感器可算指数：{}。".format(
            sensor["label"], index_name, "、".join(sensor["indexes"])))
    steps = []
    composite = params.get("composite")
    if composite:
        combo = sensor["composites"].get(composite)
        if combo is None:
            raise ValueError("组合 {} 不在 {} 的预置组合内（可选 {}）。".format(
                composite, sensor["label"], "、".join(sensor["composites"])))
        steps.append(_step("band_composite",
                           {"layer": layer, "red_band": combo[0],
                            "green_band": combo[1], "blue_band": combo[2]},
                           "{}显示（R{} G{} B{}）".format(composite, *combo)))
    index_output = params.get("output_name") or "{}结果".format(index_name)
    steps.append(_index_step(layer, index_name, sensor["bands"], index_output))
    if params.get("zones"):
        steps.append(_step("zonal_stats",
                           {"raster": index_output, "zones": params["zones"],
                            "stats": params.get("stats") or ["mean"],
                            "output": params.get("stats_output") or ""},
                           "按分区统计 {}".format(index_name)))
    if params.get("export_path"):
        steps.append(_step("export_layer",
                           {"layer": index_output, "path": params["export_path"]},
                           "导出 {} 结果".format(index_name)))
    return Plan(title="工作流：{} {}".format(sensor["label"], title_suffix),
                steps=steps, source="workflow")


def build_gf12_pms_chain(params: dict) -> Plan:
    """高分一号/二号 PMS 处理链（4 波段口径）。"""
    return _sensor_chain("gf12_pms", params)


def build_gf6_rededge_chain(params: dict) -> Plan:
    """高分六号红边指数链：默认 NDRE，可换其余可算指数。"""
    return _sensor_chain("gf6_wfv", params, default_index="NDRE",
                         title_suffix="红边指数链")


def build_jilin1_chain(params: dict) -> Plan:
    """吉林一号多光谱处理链（4 波段口径）。"""
    return _sensor_chain("jilin1", params)


def build_sentinel2_chain(params: dict) -> Plan:
    """哨兵二号处理链：SCL 云掩膜（可选）→ 波段组合（可选）→ 指数 → 分区统计（可选）。

    scl_layer 给定时先生成有效像元掩膜：SCL 类别 3（云影）、8/9（云）、
    10（卷云）置 0、其余置 1，后续可按该掩膜裁剪或与指数结果相乘应用。
    """
    sensor = SENSOR_PRESETS["sentinel2"]
    steps = []
    if params.get("scl_layer"):
        mask_output = params.get("mask_output") or "SCL有效像元掩膜"
        steps.append(_step(
            "band_math",
            {"expression": "(SCL != 3) * (SCL != 8) * (SCL != 9) * (SCL != 10)",
             "inputs": {"SCL": [params["scl_layer"], 1]}, "output": mask_output},
            "SCL 云掩膜：剔除云影(3)/中云(8)/高云(9)/卷云(10)，生成有效像元掩膜"))
    plan = _sensor_chain("sentinel2", params)
    plan.steps = steps + plan.steps
    plan.title = "工作流：{} 处理链".format(sensor["label"])
    return plan


def build_landsat_lst_chain(params: dict) -> Plan:
    """Landsat 8/9 地表温度单窗简化链：NDVI → 植被覆盖度 → 地表比辐射率 → LST。

    简化口径（模板内明示）：植被覆盖度 Pv=((NDVI-0.2)/0.3)^2（NDVIsoil=0.2、
    NDVIveg=0.5 经验值，未做区间裁剪）；比辐射率 ε=0.986+0.004·Pv（Sobrino
    NDVI 阈值法简化式）；单窗式 LST=BT/(1+(λ·BT/ρ)·lnε)，λ 取 B10 中心波长
    10.9μm、ρ=14388μm·K，并以 lnε≈ε-1 近似（ε∈[0.986,0.990] 时误差 <0.1%）。
    bt_layer 为已由 B10 经元数据 K1/K2 换算的亮温栅格（单位 K）。
    """
    _require(params, "layer", "bt_layer")
    layer, bt_layer = params["layer"], params["bt_layer"]
    bands = SENSOR_PRESETS["landsat89"]["bands"]
    prefix = params.get("output_name") or "LST"
    ndvi_output = "{}_NDVI".format(prefix)
    pv_output = "{}_PV".format(prefix)
    em_output = "{}_EM".format(prefix)
    steps = [
        _index_step(layer, "NDVI", bands, ndvi_output),
        _step("band_math",
              {"expression": "((NDVI - 0.2) / 0.3) ** 2",
               "inputs": {"NDVI": [ndvi_output, 1]}, "output": pv_output},
              "植被覆盖度 Pv=((NDVI-0.2)/0.3)^2（经验阈值 0.2/0.5，简化式）"),
        _step("band_math",
              {"expression": "0.986 + 0.004 * PV",
               "inputs": {"PV": [pv_output, 1]}, "output": em_output},
              "地表比辐射率 ε=0.986+0.004·Pv（NDVI 阈值法简化式）"),
        _step("band_math",
              {"expression": "BT / (1 + (10.9 * BT / 14388) * (EM - 1))",
               "inputs": {"BT": [bt_layer, 1], "EM": [em_output, 1]},
               "output": prefix},
              "地表温度 LST（单窗简化式，λ=10.9μm、lnε≈ε-1 近似，结果单位 K）"),
    ]
    return Plan(title="工作流：Landsat 8/9 地表温度单窗简化链",
                steps=steps, source="workflow")


def build_multitemporal_composite(params: dict) -> Plan:
    """多时相指数合成：多期同名指数栅格逐像元取均值或最大值合成一期。

    layers 为 2–6 期单波段栅格（如多期 NDVI）；method=mean/max。
    """
    _require(params, "layers")
    layers = list(params["layers"])
    if len(layers) < 2:
        raise ValueError("多时相合成至少需要 2 期栅格。")
    if len(layers) > 6:
        raise ValueError("多时相合成最多支持 6 期栅格（波段计算变量上限）。")
    method = str(params.get("method") or "mean").lower()
    names = ["L{}".format(i + 1) for i in range(len(layers))]
    if method == "mean":
        expression = "({}) / {}".format(" + ".join(names), len(names))
        desc = "多时相均值合成（{} 期）".format(len(layers))
    elif method == "max":
        # 用恒等式 max(a,b)=(a+b+|a-b|)/2 实现，只依赖四则运算与 abs，
        # 不赌栅格计算器是否支持 maximum 函数。
        expression = names[0]
        for var in names[1:]:
            expression = "({} + {} + abs({} - {})) / 2".format(
                expression, var, expression, var)
        desc = "多时相最大值合成（{} 期）".format(len(layers))
    else:
        raise ValueError("method 只能是 mean 或 max。")
    inputs = {var: [layer_name, 1] for var, layer_name in zip(names, layers)}
    steps = [_step("band_math",
                   {"expression": expression, "inputs": inputs,
                    "output": params.get("output_name", "")}, desc)]
    return Plan(title="工作流：多时相指数合成", steps=steps, source="workflow")


def build_change_detection(params: dict) -> Plan:
    """变化检测：两期单波段栅格（常用同名指数）作差，可选阈值生成变化掩膜。"""
    _require(params, "layer_before", "layer_after")
    diff_output = params.get("output_name") or "变化量"
    steps = [_step("band_math",
                   {"expression": "(AFTER - BEFORE)",
                    "inputs": {"BEFORE": [params["layer_before"], 1],
                               "AFTER": [params["layer_after"], 1]},
                    "output": diff_output},
                   "两期作差：后时相 − 前时相")]
    threshold = params.get("threshold")
    if threshold not in (None, ""):
        t = float(threshold)
        if t <= 0:
            raise ValueError("变化阈值必须大于 0。")
        steps.append(_step(
            "band_math",
            {"expression": "((DIFF > {}) + (DIFF < -{}))".format(t, t),
             "inputs": {"DIFF": [diff_output, 1]},
             "output": params.get("mask_output") or "变化掩膜"},
            "阈值掩膜：|变化量| > {} 的像元置 1".format(t)))
    return Plan(title="工作流：两期变化检测", steps=steps, source="workflow")


def _sensor_params(default_index_label: str) -> list:
    """传感器处理链模板共用的参数规格。"""
    return [
        {"name": "layer", "label": "影像图层", "type": "layer", "required": True},
        {"name": "index", "label": "指数名（默认 {}）".format(default_index_label),
         "type": "text", "required": False},
        {"name": "composite", "label": "波段组合名（可空，如 真彩色）", "type": "text",
         "required": False},
        {"name": "zones", "label": "分区统计图层（可空）", "type": "layer", "required": False},
        {"name": "output_name", "label": "指数输出图层名（可空）", "type": "text",
         "required": False},
        {"name": "export_path", "label": "导出路径（可空）", "type": "path", "required": False},
    ]


#: 工作流注册表：key -> 元信息 + 构建函数。界面按 params 规格动态生成表单。
WORKFLOWS = {
    "clip_image": {
        "title": "按研究区裁剪一景影像",
        "description": "场景：一景影像全流程的第一步——用行政区/研究区掩膜裁剪高分、哨兵等影像，可选导出。",
        "params": [
            {"name": "raster_layer", "label": "待裁剪影像图层", "type": "layer", "required": True},
            {"name": "mask_layer", "label": "掩膜图层", "type": "layer", "required": True},
            {"name": "output_name", "label": "输出图层名（可空）", "type": "text", "required": False},
            {"name": "export_path", "label": "导出路径（可空）", "type": "path", "required": False},
        ],
        "builder": build_clip_image,
    },
    "mosaic_images": {
        "title": "多景影像镶嵌成图",
        "description": "场景：研究区跨多景影像时，把相邻多景镶嵌合并为一幅，再接指数计算与出图。",
        "params": [
            {"name": "layers", "label": "影像图层（多个）", "type": "layers", "required": True},
            {"name": "output_name", "label": "输出图层名（可空）", "type": "text", "required": False},
        ],
        "builder": build_mosaic_images,
    },
    "batch_reproject": {
        "title": "批量统一坐标系（CGCS2000/WGS84）",
        "description": "场景：多源数据坐标系不一致时，一键统一重投影到 CGCS2000、WGS84 等目标坐标系。",
        "params": [
            {"name": "layers", "label": "图层（多个）", "type": "layers", "required": True},
            {"name": "target_crs", "label": "目标坐标系（如 EPSG:4326）", "type": "text", "required": True},
        ],
        "builder": build_batch_reproject,
    },
    "band_composite_index": {
        "title": "波段合成与植被/水体指数",
        "description": "场景：给一景影像做真/假彩色合成判读，或算 10 种预置指数（NDVI/NDWI/NDBI/EVI/SAVI/NDMI/MNDWI/GNDVI/NDRE/BSI）。",
        "params": [
            {"name": "layer", "label": "影像图层", "type": "layer", "required": True},
            {"name": "mode", "label": "模式 composite / index", "type": "text", "required": True},
            {"name": "red_band", "label": "合成-红波段号", "type": "int", "required": False},
            {"name": "green_band", "label": "合成-绿波段号", "type": "int", "required": False},
            {"name": "blue_band", "label": "合成-蓝波段号", "type": "int", "required": False},
            {"name": "index", "label": "指数名（10 种预置，默认 NDVI）", "type": "text", "required": False},
            {"name": "band_map", "label": "指数波段映射（如 NIR=4,RED=3）", "type": "band_map", "required": False},
            {"name": "output_name", "label": "输出图层名（可空）", "type": "text", "required": False},
        ],
        "builder": build_band_composite_and_index,
    },
    "zonal_statistics": {
        "title": "按县界/地块分区统计",
        "description": "场景：按行政区划县界或地块分区，统计区内影像/栅格的均值、总和与极值，可选导出表格。",
        "params": [
            {"name": "raster", "label": "栅格图层", "type": "layer", "required": True},
            {"name": "zones", "label": "分区矢量图层", "type": "layer", "required": True},
            {"name": "stats", "label": "统计量（如 mean,sum）", "type": "text_list", "required": False},
            {"name": "output_name", "label": "输出图层名（可空）", "type": "text", "required": False},
            {"name": "export_path", "label": "导出路径（可空）", "type": "path", "required": False},
        ],
        "builder": build_zonal_statistics,
    },
    "gf12_pms_chain": {
        "title": "高分一号/二号 PMS 处理链",
        "description": "场景：国产高分一号/二号 PMS 四波段（蓝1/绿2/红3/近红外4）开箱链——波段组合→指数（NDVI/EVI/SAVI/GNDVI/NDWI）→分区统计→导出，波段口径按公开 PMS 口径预置。",
        "params": _sensor_params("NDVI"),
        "builder": build_gf12_pms_chain,
    },
    "gf6_rededge_chain": {
        "title": "高分六号红边指数链",
        "description": "场景：高分六号宽幅相机红边优势链——默认 NDRE 红边指数（红边 1 为第 5 波段），也可算 NDVI/EVI/SAVI/GNDVI/NDWI，并可接红边假彩色组合与分区统计。",
        "params": _sensor_params("NDRE"),
        "builder": build_gf6_rededge_chain,
    },
    "jilin1_chain": {
        "title": "吉林一号多光谱处理链",
        "description": "场景：吉林一号多光谱四波段（蓝1/绿2/红3/近红外4）处理链——波段组合→指数（NDVI/EVI/SAVI/GNDVI/NDWI）→分区统计→导出。",
        "params": _sensor_params("NDVI"),
        "builder": build_jilin1_chain,
    },
    "sentinel2_chain": {
        "title": "哨兵二号处理链（SCL 云掩膜）",
        "description": "场景：哨兵二号 L2A 全流程——可选 SCL 云掩膜（剔除云影3/中云8/高云9/卷云10）→波段组合→10 种指数（B02 蓝2/B03 绿3/B04 红4/B05 红边5/B08 近红外8/B11 短波红外11）→分区统计。",
        "params": [
            {"name": "layer", "label": "影像图层", "type": "layer", "required": True},
            {"name": "scl_layer", "label": "SCL 场景分类图层（可空）", "type": "layer",
             "required": False},
            {"name": "index", "label": "指数名（默认 NDVI）", "type": "text", "required": False},
            {"name": "composite", "label": "波段组合名（可空，如 真彩色）", "type": "text",
             "required": False},
            {"name": "zones", "label": "分区统计图层（可空）", "type": "layer", "required": False},
            {"name": "output_name", "label": "指数输出图层名（可空）", "type": "text",
             "required": False},
            {"name": "export_path", "label": "导出路径（可空）", "type": "path", "required": False},
        ],
        "builder": build_sentinel2_chain,
    },
    "landsat89_lst": {
        "title": "Landsat 8/9 地表温度单窗简化链",
        "description": "场景：Landsat 8/9 快速地表温度——OLI 算 NDVI（近红外5/红4）→植被覆盖度→比辐射率→单窗简化式 LST；亮温图层须先由 B10 按该景元数据 K1/K2 换算（单位 K），简化口径见计划步说明。",
        "params": [
            {"name": "layer", "label": "OLI 影像图层", "type": "layer", "required": True},
            {"name": "bt_layer", "label": "B10 亮温图层（K）", "type": "layer", "required": True},
            {"name": "output_name", "label": "输出名前缀（默认 LST）", "type": "text",
             "required": False},
        ],
        "builder": build_landsat_lst_chain,
    },
    "multitemporal_composite": {
        "title": "多时相指数合成",
        "description": "场景：多期同名指数（如逐月 NDVI）逐像元合成一期——均值合成压噪声、最大值合成取生长季峰值，2–6 期。",
        "params": [
            {"name": "layers", "label": "多期栅格图层（多个）", "type": "layers", "required": True},
            {"name": "method", "label": "合成方法 mean / max", "type": "text", "required": False},
            {"name": "output_name", "label": "输出图层名（可空）", "type": "text", "required": False},
        ],
        "builder": build_multitemporal_composite,
    },
    "change_detection": {
        "title": "两期变化检测",
        "description": "场景：两期同名指数/分类前值作差找变化，可选阈值生成变化掩膜（如 |ΔNDVI|>0.2 的地表扰动区）。",
        "params": [
            {"name": "layer_before", "label": "前时相栅格图层", "type": "layer", "required": True},
            {"name": "layer_after", "label": "后时相栅格图层", "type": "layer", "required": True},
            {"name": "threshold", "label": "变化阈值（可空，如 0.2）", "type": "text",
             "required": False},
            {"name": "output_name", "label": "变化量输出图层名（可空）", "type": "text",
             "required": False},
        ],
        "builder": build_change_detection,
    },
    "layout_export": {
        "title": "成果出图（专题图排版导出）",
        "description": "场景：一景影像全流程的收尾——裁剪→镶嵌→指数→出图，在此按标题/指北针/比例尺/图例/纸张/分辨率排版导出 PDF 或图片。",
        "params": [
            {"name": "title", "label": "图名标题", "type": "text", "required": True},
            {"name": "output_path", "label": "输出文件路径", "type": "path", "required": True},
            {"name": "paper", "label": "纸张 A4/A3", "type": "text", "required": False},
            {"name": "orientation", "label": "方向 landscape/portrait", "type": "text", "required": False},
            {"name": "show_north_arrow", "label": "指北针", "type": "bool", "required": False},
            {"name": "show_scale_bar", "label": "比例尺", "type": "bool", "required": False},
            {"name": "show_legend", "label": "图例", "type": "bool", "required": False},
            {"name": "dpi", "label": "分辨率 DPI", "type": "int", "required": False},
        ],
        "builder": build_layout_export,
    },
}


def build_plan(workflow_key: str, params: dict) -> Plan:
    """按工作流 key 与参数生成执行计划。"""
    entry = WORKFLOWS.get(workflow_key)
    if entry is None:
        raise KeyError("未知工作流：{}".format(workflow_key))
    return entry["builder"](dict(params or {}))
