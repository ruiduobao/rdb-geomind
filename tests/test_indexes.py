# -*- coding: utf-8 -*-
"""遥感指数预置测试：10 种指数的公式、波段口径与 GDAL 翻译层锁定。

沿用 test_backend_calc 的翻译层测试模式：每个指数的变量表达式经
build_gdal_raster_calc 翻译后，公式与 (字母, 图层, 波段) 映射必须与
手写期望逐字一致，防止公式或波段顺序被无意改动。
"""
import re
import unittest

from rdb_geomind.core.backend import build_gdal_raster_calc
from rdb_geomind.core.tools import INDEX_PRESETS
from rdb_geomind.core.workflows import build_plan

# 手写期望：指数 -> (bands 顺序, 翻译后公式)。波段号按 bands 顺序取 11,12,...
EXPECTED = {
    "NDVI": (["NIR", "RED"], "(A - B) / (A + B)"),
    "NDWI": (["GREEN", "NIR"], "(A - B) / (A + B)"),
    "NDBI": (["SWIR", "NIR"], "(A - B) / (A + B)"),
    "EVI": (["NIR", "RED", "BLUE"], "2.5 * (A - B) / (A + 6 * B - 7.5 * C + 1)"),
    "SAVI": (["NIR", "RED"], "1.5 * (A - B) / (A + B + 0.5)"),
    "NDMI": (["NIR", "SWIR"], "(A - B) / (A + B)"),
    "MNDWI": (["GREEN", "SWIR"], "(A - B) / (A + B)"),
    "GNDVI": (["NIR", "GREEN"], "(A - B) / (A + B)"),
    "NDRE": (["NIR", "REDEDGE"], "(A - B) / (A + B)"),
    "BSI": (["SWIR", "RED", "NIR", "BLUE"],
            "((A + B) - (C + D)) / ((A + B) + (C + D))"),
}


class TestIndexPresets(unittest.TestCase):
    def test_ten_indexes_registered(self):
        self.assertEqual(len(INDEX_PRESETS), 10)
        self.assertEqual(set(INDEX_PRESETS), set(EXPECTED))

    def test_each_index_has_source_and_matching_bands(self):
        for name, preset in INDEX_PRESETS.items():
            self.assertTrue(preset.get("source"), name)   # 公式来源必填
            self.assertTrue(preset.get("note"), name)     # 波段口径说明必填
            used = set(re.findall(r"[A-Z][A-Z0-9]*", preset["expression"]))
            self.assertEqual(used, set(preset["bands"]), name)

    def test_translation_locked_for_all_indexes(self):
        for name, (bands, expected_formula) in EXPECTED.items():
            preset = INDEX_PRESETS[name]
            self.assertEqual(preset["bands"], bands, name)
            inputs = {var: ["影像", 11 + i] for i, var in enumerate(bands)}
            formula, entries = build_gdal_raster_calc(preset["expression"], inputs)
            self.assertEqual(formula, expected_formula, name)
            self.assertEqual(
                entries,
                [(chr(ord("A") + i), "影像", 11 + i) for i in range(len(bands))],
                name)

    def test_numeric_sanity(self):
        # 用已知反射率手算核对公式数值口径
        values = {"NIR": 0.8, "RED": 0.2, "GREEN": 0.3, "BLUE": 0.1,
                  "SWIR": 0.25, "REDEDGE": 0.5}
        cases = {"NDVI": 0.6, "SAVI": 0.6, "EVI": 1.5 / 2.25,
                 "GNDVI": 0.5 / 1.1, "MNDWI": 0.05 / 0.55, "NDRE": 0.3 / 1.3}
        for name, expected in cases.items():
            got = eval(INDEX_PRESETS[name]["expression"], {}, dict(values))
            self.assertAlmostEqual(got, expected, places=9, msg=name)

    def test_new_indexes_usable_via_generic_workflow(self):
        plan = build_plan("band_composite_index",
                          {"layer": "影像", "mode": "index", "index": "EVI",
                           "band_map": {"NIR": 8, "RED": 4, "BLUE": 2}})
        step = plan.steps[0]
        self.assertEqual(step.arguments["inputs"]["BLUE"], ["影像", 2])
        self.assertIn("7.5", step.arguments["expression"])
        plan = build_plan("band_composite_index",
                          {"layer": "影像", "mode": "index", "index": "BSI",
                           "band_map": {"SWIR": 11, "RED": 4, "NIR": 8, "BLUE": 2}})
        self.assertEqual(len(plan.steps[0].arguments["inputs"]), 4)

    def test_missing_band_for_index_raises(self):
        with self.assertRaises(ValueError):
            build_plan("band_composite_index",
                       {"layer": "影像", "mode": "index", "index": "NDRE",
                        "band_map": {"NIR": 8}})  # 缺 REDEDGE


if __name__ == "__main__":
    unittest.main()
