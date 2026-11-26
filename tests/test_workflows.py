# -*- coding: utf-8 -*-
"""工作流模板测试：十三个模板全部生成计划，多条在 FakeBackend 下跑通执行。"""
import unittest

from rdb_geomind.core.backend import FakeBackend
from rdb_geomind.core.planner import PlanExecutor
from rdb_geomind.core.workflows import WORKFLOWS, build_plan


class TestWorkflows(unittest.TestCase):
    def run_workflow(self, key, params, existing=None):
        backend = FakeBackend(existing_layers=existing or [])
        plan = build_plan(key, params)
        result = PlanExecutor(backend).execute(plan)
        return plan, backend, result

    def test_clip_image(self):
        plan, backend, result = self.run_workflow(
            "clip_image",
            {"raster_layer": "影像", "mask_layer": "研究区", "output_name": "裁剪影像"},
            existing=["影像", "研究区"])
        self.assertTrue(result.success, result.log)
        self.assertEqual(plan.steps[0].name, "clip_by_mask")
        self.assertIn("裁剪影像", backend.layers)

    def test_mosaic_images(self):
        plan, backend, result = self.run_workflow(
            "mosaic_images",
            {"layers": ["景1", "景2", "景3"], "output_name": "镶嵌图"},
            existing=["景1", "景2", "景3"])
        self.assertTrue(result.success, result.log)
        self.assertIn("镶嵌图", backend.layers)

    def test_batch_reproject(self):
        plan, backend, result = self.run_workflow(
            "batch_reproject",
            {"layers": ["A", "B"], "target_crs": "EPSG:4326"},
            existing=["A", "B"])
        self.assertEqual(len(plan.steps), 2)  # 每图层一步
        self.assertTrue(result.success, result.log)
        called = [name for name, _ in backend.calls if name == "reproject"]
        self.assertEqual(len(called), 2)

    def test_index_ndvi(self):
        plan, backend, result = self.run_workflow(
            "band_composite_index",
            {"layer": "哨兵影像", "mode": "index", "index": "NDVI",
             "band_map": {"NIR": 4, "RED": 3}, "output_name": "NDVI结果"},
            existing=["哨兵影像"])
        self.assertTrue(result.success, result.log)
        step = plan.steps[0]
        self.assertEqual(step.name, "band_math")
        self.assertIn("NIR", step.arguments["expression"])
        self.assertEqual(step.arguments["inputs"]["NIR"], ["哨兵影像", 4])
        self.assertIn("NDVI结果", backend.layers)

    def test_index_ndwi_and_ndbi_presets(self):
        for index, band_map in (("NDWI", {"GREEN": 2, "NIR": 4}),
                                ("NDBI", {"SWIR": 5, "NIR": 4})):
            plan = build_plan("band_composite_index",
                              {"layer": "影像", "mode": "index", "index": index,
                               "band_map": band_map})
            self.assertEqual(plan.steps[0].name, "band_math")

    def test_composite_mode(self):
        plan, backend, result = self.run_workflow(
            "band_composite_index",
            {"layer": "影像", "mode": "composite",
             "red_band": 4, "green_band": 3, "blue_band": 2},
            existing=["影像"])
        self.assertTrue(result.success, result.log)
        self.assertEqual(plan.steps[0].name, "band_composite")

    def test_zonal_statistics(self):
        plan, backend, result = self.run_workflow(
            "zonal_statistics",
            {"raster": "人口栅格", "zones": "县界", "stats": ["mean", "sum"],
             "output_name": "县统计"},
            existing=["人口栅格", "县界"])
        self.assertTrue(result.success, result.log)
        self.assertIn("县统计", backend.layers)

    def test_layout_export_plan(self):
        plan, backend, result = self.run_workflow(
            "layout_export",
            {"title": "研究区遥感图", "output_path": "/tmp/out.pdf",
             "paper": "A4", "show_north_arrow": True, "dpi": 300})
        self.assertEqual(plan.steps[0].name, "export_layout")
        args = plan.steps[0].arguments
        self.assertEqual(args["title"], "研究区遥感图")
        self.assertTrue(args["show_scale_bar"])
        self.assertTrue(result.success, result.log)  # FakeBackend 记录导出调用

    def test_missing_params_raise_chinese_error(self):
        with self.assertRaises(ValueError):
            build_plan("clip_image", {"raster_layer": "影像"})
        with self.assertRaises(ValueError):
            build_plan("mosaic_images", {"layers": ["只有一幅"]})
        with self.assertRaises(KeyError):
            build_plan("不存在的模板", {})

    def test_all_workflows_registered(self):
        self.assertEqual(set(WORKFLOWS), {
            "clip_image", "mosaic_images", "batch_reproject",
            "band_composite_index", "zonal_statistics", "layout_export",
            "gf12_pms_chain", "gf6_rededge_chain", "jilin1_chain",
            "sentinel2_chain", "landsat89_lst", "multitemporal_composite",
            "change_detection"})
        self.assertEqual(len(WORKFLOWS), 13)

    # ------------------------------------------------ 传感器处理链模板（新增）
    def test_gf12_pms_chain(self):
        plan, backend, result = self.run_workflow(
            "gf12_pms_chain",
            {"layer": "高分影像", "index": "NDVI", "composite": "真彩色",
             "zones": "县界", "output_name": "GF_NDVI"},
            existing=["高分影像", "县界"])
        self.assertTrue(result.success, result.log)
        self.assertEqual([s.name for s in plan.steps],
                         ["band_composite", "band_math", "zonal_stats"])
        idx = plan.steps[1].arguments
        self.assertEqual(idx["inputs"], {"NIR": ["高分影像", 4], "RED": ["高分影像", 3]})
        self.assertIn("GF_NDVI", backend.layers)

    def test_gf12_rejects_index_without_band(self):
        with self.assertRaises(ValueError):  # PMS 无 SWIR，不能算 NDMI
            build_plan("gf12_pms_chain", {"layer": "高分影像", "index": "NDMI"})

    def test_gf6_rededge_default_ndre(self):
        plan = build_plan("gf6_rededge_chain", {"layer": "高分六号"})
        step = plan.steps[0]
        self.assertEqual(step.name, "band_math")
        self.assertEqual(step.arguments["inputs"],
                         {"NIR": ["高分六号", 4], "REDEDGE": ["高分六号", 5]})

    def test_jilin1_chain(self):
        plan, backend, result = self.run_workflow(
            "jilin1_chain", {"layer": "吉林影像", "index": "GNDVI"},
            existing=["吉林影像"])
        self.assertTrue(result.success, result.log)
        self.assertEqual(plan.steps[0].arguments["inputs"]["GREEN"], ["吉林影像", 2])

    def test_sentinel2_chain_with_scl_mask(self):
        plan, backend, result = self.run_workflow(
            "sentinel2_chain",
            {"layer": "哨兵影像", "scl_layer": "SCL", "index": "MNDWI"},
            existing=["哨兵影像", "SCL"])
        self.assertTrue(result.success, result.log)
        mask = plan.steps[0]
        self.assertIn("SCL != 8", mask.arguments["expression"])  # 云类别剔除
        idx = plan.steps[1].arguments
        self.assertEqual(idx["inputs"]["SWIR"], ["哨兵影像", 11])  # B11 原始编号

    def test_landsat_lst_chain(self):
        plan, backend, result = self.run_workflow(
            "landsat89_lst", {"layer": "LC影像", "bt_layer": "亮温"},
            existing=["LC影像", "亮温"])
        self.assertTrue(result.success, result.log)
        self.assertEqual(len(plan.steps), 4)
        self.assertEqual(plan.steps[0].arguments["inputs"]["NIR"], ["LC影像", 5])
        # 链式引用：后一步引用前一步的输出图层名
        self.assertEqual(plan.steps[1].arguments["inputs"]["NDVI"], ["LST_NDVI", 1])
        self.assertEqual(plan.steps[3].arguments["inputs"]["BT"], ["亮温", 1])
        self.assertIn("LST", backend.layers)

    def test_multitemporal_composite(self):
        plan = build_plan("multitemporal_composite",
                          {"layers": ["NDVI_5月", "NDVI_6月", "NDVI_7月"],
                           "method": "mean", "output_name": "合成NDVI"})
        step = plan.steps[0]
        self.assertEqual(step.arguments["expression"], "(L1 + L2 + L3) / 3")
        plan = build_plan("multitemporal_composite",
                          {"layers": ["A", "B"], "method": "max"})
        self.assertEqual(plan.steps[0].arguments["expression"],
                         "(L1 + L2 + abs(L1 - L2)) / 2")
        with self.assertRaises(ValueError):
            build_plan("multitemporal_composite",
                       {"layers": ["期{}".format(i) for i in range(7)]})

    def test_change_detection(self):
        plan, backend, result = self.run_workflow(
            "change_detection",
            {"layer_before": "NDVI_2020", "layer_after": "NDVI_2025",
             "threshold": "0.2"},
            existing=["NDVI_2020", "NDVI_2025"])
        self.assertTrue(result.success, result.log)
        self.assertEqual(len(plan.steps), 2)
        self.assertIn("DIFF > 0.2", plan.steps[1].arguments["expression"])
        with self.assertRaises(ValueError):
            build_plan("change_detection",
                       {"layer_before": "A", "layer_after": "B", "threshold": "-1"})


if __name__ == "__main__":
    unittest.main()
