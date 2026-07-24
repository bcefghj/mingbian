# -*- coding: utf-8 -*-
"""把 nginx 片段幂等地插入到根 location / 之前。
用法: python3 insert_nginx.py <nginx_conf> <snippet>
从 snippet 自动识别幂等标记（第一个 `location /xxx/app/`）。
打印 CHANGED / ALREADY；出错非零退出。"""
import io
import re
import sys

conf, snippet = sys.argv[1], sys.argv[2]
s = io.open(conf, encoding="utf-8").read()
block = io.open(snippet, encoding="utf-8").read()

m = re.search(r"location\s+(/\S+?/app/)", block)
marker = "location " + m.group(1) if m else None
if marker and marker in s:
    print("ALREADY")
    sys.exit(0)

idx = s.rfind("location / {")
if idx == -1:
    idx = s.rfind("gzip on;")
if idx == -1:
    idx = s.rstrip().rfind("}")
if idx == -1:
    print("ERROR: no insertion point", file=sys.stderr)
    sys.exit(2)

line_start = s.rfind("\n", 0, idx) + 1
io.open(conf, "w", encoding="utf-8").write(s[:line_start] + block + s[line_start:])
print("CHANGED")
