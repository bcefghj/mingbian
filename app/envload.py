# -*- coding: utf-8 -*-
"""零依赖的 .env 加载器（避免额外 pip 依赖）。"""
import os


def load_env(path=None):
    path = path or os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            # 不覆盖已存在的环境变量
            os.environ.setdefault(k, v)
