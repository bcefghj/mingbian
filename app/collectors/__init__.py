# -*- coding: utf-8 -*-
"""取证器（博学阶段）。

四类：
  market —— 公开行情接口，拿确定性数字
  bocha  —— 博查全网检索，主通道，结构化结果带发布时间与长摘要
  search —— 检索调度：博查优先，HTML 抓取兜底
  web    —— 逐条访问 URL 做真实性核验，拿标题与摘录

所有取证器都遵守同一条纪律：失败就如实标状态，绝不返回空数组假装「没有」。
"""
from . import bocha
from .market import collect_market, market_block
from .search import search_web
from .web import verify_evidence, verify_many, verify_url

__all__ = ["bocha", "collect_market", "market_block", "search_web",
           "verify_url", "verify_many", "verify_evidence"]
