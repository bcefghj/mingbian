#!/usr/bin/env bash
# ============================================================
# 明辨 MINGBIAN · 一键部署到阿里云（遵循「服务器部署说明.md」多项目隔离规范）
# 作为「项目三」接入：独立目录 + venv + systemd(mingbian.service, 8767) + 独立 nginx location(/mingbian/)
# 绝不修改/影响 Anker（/anker，8766）及其它任何已有项目。
# 用法（在服务器项目目录内）：  sudo bash deploy.sh
# ============================================================
set -euo pipefail

APP="mingbian"
DIR="/opt/projects/$APP"
PORT="8767"    # 项目三槽位；Anker 用 8766，绝不冲突
SVC="$APP.service"
NGINX_CONF="/etc/nginx/sites-available/projects"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> [1/7] 检查环境"
[ "$(id -u)" = "0" ] || { echo "请用 root 运行 (sudo bash deploy.sh)"; exit 1; }
command -v nginx >/dev/null || { echo "未找到 nginx"; exit 1; }
if ss -ltnp 2>/dev/null | grep -q ":$PORT "; then echo "⚠ 端口 $PORT 已被占用，已安全退出，未改动任何东西。请换端口。"; exit 1; fi

echo "==> [2/7] 同步代码到 $DIR（不碰其它项目，保留已有 .env）"
mkdir -p "$DIR"
rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='.git' --exclude='reports/*' \
      --exclude='.env' "$SRC_DIR"/ "$DIR"/
mkdir -p "$DIR/reports"

echo "==> [3/7] 建虚拟环境 + 装依赖（清华镜像加速）"
cd "$DIR"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip -q install --upgrade pip
./.venv/bin/pip -q install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

echo "==> [4/7] 写入 .env（若不存在）"
if [ ! -f "$DIR/.env" ]; then cp "$DIR/.env.example" "$DIR/.env"; echo "   已从 .env.example 生成 .env"; else echo "   .env 已存在，保留不覆盖"; fi
chmod 600 "$DIR/.env" || true

echo "==> [5/7] 创建 systemd 服务 $SVC（端口 $PORT，仅本机监听）"
cat > "/etc/systemd/system/$SVC" <<EOF
[Unit]
Description=MINGBIAN (明辨) multi-agent evidence judgment engine
After=network.target

[Service]
Type=simple
WorkingDirectory=$DIR
EnvironmentFile=-$DIR/.env
Environment=PORT=$PORT
Environment=HOST=127.0.0.1
ExecStart=$DIR/.venv/bin/python run.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SVC" >/dev/null 2>&1 || true
systemctl restart "$SVC"
sleep 2
systemctl is-active "$SVC" >/dev/null && echo "   $SVC 已启动" || { echo "启动失败："; journalctl -u "$SVC" -n 30 --no-pager; exit 1; }
curl -s "http://127.0.0.1:$PORT/healthz" >/dev/null && echo "   本机健康检查通过" || echo "   ⚠ 健康检查未通过，可稍后 journalctl 排查"

echo "==> [6/7] 接入 nginx（幂等；先备份，失败自动回滚，不动 Anker）"
if grep -q "location /$APP/" "$NGINX_CONF"; then
  echo "   nginx 已含 /$APP/ 配置，跳过"
else
  BAK="${NGINX_CONF}.bak.$(date +%s 2>/dev/null || echo manual)"
  cp "$NGINX_CONF" "$BAK"; echo "   已备份 nginx 配置到 $BAK"
  echo "   插入结果：$(python3 "$DIR/deploy/insert_nginx.py" "$NGINX_CONF" "$DIR/deploy/nginx-mingbian.snippet")"
  if nginx -t 2>/dev/null; then systemctl reload nginx; echo "   nginx 校验通过并已 reload";
  else echo "   ✗ nginx -t 失败，回滚到 $BAK"; cp "$BAK" "$NGINX_CONF"; nginx -t && systemctl reload nginx; echo "   已回滚，Anker 等完全不受影响"; exit 1; fi
fi

echo "==> [7/7] 完成"
echo "-----------------------------------------------"
echo "  明辨已上线："
echo "    http://47.119.112.225/mingbian/"
echo "    http://47.119.112.225/mingbian/api/health"
echo "    http://47.119.112.225/mingbian/ledger   （InfiniSynapse 调用台账）"
echo ""
echo "  Anker 不受影响，验证："
echo "    systemctl is-active nginx $SVC"
echo "    curl -s -o /dev/null -w 'anker: %{http_code}\\n' http://47.119.112.225/anker/app/"
echo "-----------------------------------------------"
echo "  下一步（强烈建议）：用真实 API 生成示例数据+真实 taskId："
echo "    cd $DIR && ./.venv/bin/python scripts/seed_demos.py"
echo "-----------------------------------------------"
