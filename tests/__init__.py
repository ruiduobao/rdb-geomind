# 测试包：把 geomind/ 根目录加入 sys.path，使 rdb_geomind 可直接导入。
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
