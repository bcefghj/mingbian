# -*- coding: utf-8 -*-
"""报告存储：reports/*.json 落盘，用于分享短链。"""
import os
import json
import time
import uuid

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
os.makedirs(REPORT_DIR, exist_ok=True)


def save_report(question, markdown, meta=None, task_id=None, share_url="", engine="infini", rid=None):
    rid = rid or uuid.uuid4().hex[:12]
    obj = {"id": rid, "question": question, "markdown": markdown, "meta": meta or {},
           "taskId": task_id, "share_url": share_url, "engine": engine,
           "created_at": int(time.time())}
    with open(os.path.join(REPORT_DIR, f"{rid}.json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return rid


def get_report(rid):
    p = os.path.join(REPORT_DIR, f"{rid}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)
