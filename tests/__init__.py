# -*- coding: utf-8 -*-
"""测试包。用标准库 unittest：python -m unittest discover -s tests -t ."""
from hgdb import dbio

# 测试一律不碰真实 output/：默认配置不绑定任何路径，用例自己用 Config(临时路径)
# 建库。否则 ED2K 的 clear() 用例可能删到真实数据。
dbio.set_default(dbio.Config())
