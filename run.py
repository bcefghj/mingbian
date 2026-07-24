# -*- coding: utf-8 -*-
"""入口：加载 .env 并启动 uvicorn。"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from app.envload import load_env
load_env()

import uvicorn

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8766"))
    uvicorn.run("app.main:app", host=host, port=port, workers=1, log_level="info")
