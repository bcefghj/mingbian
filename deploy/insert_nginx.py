# -*- coding: utf-8 -*-
"""把 nginx 片段幂等地插入到根 location / 之前。
用法: python3 insert_nginx.py <nginx_conf> <snippet>
- 识别片段里第一个 `location /xxx/` 作为幂等标记
- 插入前清掉旧司南 /sinan 代理块，避免与明辨里的 /sinan 重定向冲突
打印 CHANGED / ALREADY；出错非零退出。"""
import io
import re
import sys

conf, snippet = sys.argv[1], sys.argv[2]
s = io.open(conf, encoding="utf-8").read()
block = io.open(snippet, encoding="utf-8").read()

# 清掉旧司南代理（与新片段里的 /sinan 重定向冲突）
s2, n = re.subn(
    r"\n?[ \t]*# ---- 项目三：司南 SINAN[^\n]*\n"
    r"(?:[ \t]*location[^\n]*\n|[ \t]*\{[^\n]*\n|[ \t]*[^/\n][^\n]*\n|[ \t]*\}\n?)+",
    "\n",
    s,
    count=1,
)
if n:
    s = s2
else:
    # 兜底：按 location /sinan/app/ 块删
    s2, n = re.subn(
        r"\n?[ \t]*location = /sinan \{[^}]*\}\n"
        r"[ \t]*location = /sinan/ \{[^}]*\}\n"
        r"[ \t]*location = /sinan/app \{[^}]*\}\n"
        r"[ \t]*location /sinan/app/ \{[^}]*\}\n?",
        "\n",
        s,
        count=1,
    )
    if n:
        s = s2

m = re.search(r"location\s+(=\s+)?(/\S+?/)", block)
marker = None
if m:
    marker = "location /mingbian/" if "/mingbian/" in block else (
        f"location {m.group(2)}"
    )

if marker and marker in s and "location /mingbian/" in s:
    # 已含明辨；若本轮清掉了旧司南也要写回
    if n:
        io.open(conf, "w", encoding="utf-8").write(s)
        print("CHANGED")
    else:
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
