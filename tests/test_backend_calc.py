# -*- coding: utf-8 -*-
"""QgisBackend 波段计算参数翻译测试（纯函数层，无需 QGIS）。

背景：gdal:rastercalculator 只认单字母变量 A–F 与 INPUT_A/BAND_A 参数，
工具层却用变量名（NIR/RED…）写表达式。build_gdal_raster_calc 负责翻译，
本组用例锁定翻译口径，防止再退回「图层id@波段」式的错误公式。
"""
import unittest

from rdb_geomind.core.backend import BackendError, build_gdal_raster_calc


class TestBuildGdalRasterCalc(unittest.TestCase):
    def test_ndvi_two_inputs(self):
        formula, entries = build_gdal_raster_calc(
            "(NIR-RED)/(NIR+RED)", {"NIR": ["影像", 4], "RED": ["影像", 3]})
        self.assertEqual(formula, "(A-B)/(A+B)")
        self.assertEqual(entries, [("A", "影像", 4), ("B", "影像", 3)])

    def test_single_input_default_band(self):
        formula, entries = build_gdal_raster_calc("DEM*2", {"DEM": "高程"})
        self.assertEqual(formula, "A*2")
        self.assertEqual(entries, [("A", "高程", 1)])

    def test_no_cascade_when_var_names_are_letters(self):
        # 变量名本身是 B、A 时，单趟替换不能把已替换出的字母再次串改
        formula, entries = build_gdal_raster_calc(
            "A-B", {"B": ["甲", 1], "A": ["乙", 2]})
        self.assertEqual(entries, [("A", "甲", 1), ("B", "乙", 2)])
        self.assertEqual(formula, "B-A")

    def test_longest_name_first(self):
        formula, _ = build_gdal_raster_calc(
            "NIR2-NIR", {"NIR": ["影像", 4], "NIR2": ["影像", 5]})
        self.assertEqual(formula, "B-A")

    def test_empty_inputs_rejected(self):
        with self.assertRaises(BackendError):
            build_gdal_raster_calc("A*2", {})

    def test_too_many_inputs_rejected(self):
        inputs = {"V{}".format(i): ["影像", 1] for i in range(7)}
        with self.assertRaises(BackendError):
            build_gdal_raster_calc("V0+V1", inputs)


if __name__ == "__main__":
    unittest.main()
