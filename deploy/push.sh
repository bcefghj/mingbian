#!/usr/bin/env bash
# 从本机把明辨推到 47.119.112.225 并跑 deploy.sh。
# 用法：
#   export MB_SSH_PASS='...'
#   bash deploy/push.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="47.119.112.225"
REMOTE_DIR="/opt/projects/mingbian"
STAGE="/tmp/mingbian-stage-$$"
PASS="${MB_SSH_PASS:?请先 export MB_SSH_PASS=服务器密码}"

cd "$ROOT"
rm -rf "$STAGE"
mkdir -p "$STAGE"

echo "==> 打包代码（不含 .venv / .env / reports / 本地缓存）"
rsync -a \
  --exclude='.venv' --exclude='.env' --exclude='.git' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
  --exclude='reports' --exclude='*.log' --exclude='*.tar.gz' \
  "$ROOT"/ "$STAGE"/

TAR="/tmp/mingbian-push-$$.tar.gz"
tar -C "$STAGE" -czf "$TAR" .
echo "   包大小：$(du -h "$TAR" | awk '{print $1}')"

echo "==> 上传到服务器"
export MB_SSH_PASS="$PASS"
"$ROOT/deploy/scp.exp" "$TAR" "/tmp/mingbian-push.tar.gz"

echo "==> 远程解压并执行 deploy.sh"
"$ROOT/deploy/ssh.exp" "set -e
  mkdir -p $REMOTE_DIR
  # 保留密钥与已有报告
  if [ -f $REMOTE_DIR/.env ]; then cp $REMOTE_DIR/.env /tmp/mingbian.env.bak; fi
  tar -xzf /tmp/mingbian-push.tar.gz -C $REMOTE_DIR
  if [ -f /tmp/mingbian.env.bak ]; then mv /tmp/mingbian.env.bak $REMOTE_DIR/.env; fi
  mkdir -p $REMOTE_DIR/reports
  cd $REMOTE_DIR && bash deploy.sh
  rm -f /tmp/mingbian-push.tar.gz
"

echo "==> 公网健康检查"
sleep 2
curl -s -o /dev/null -w "hub: %{http_code}\n" "http://$HOST/"
curl -s -o /dev/null -w "mingbian: %{http_code}\n" "http://$HOST/mingbian/"
curl -s -o /dev/null -w "health: %{http_code}\n" "http://$HOST/mingbian/api/health"
curl -s "http://$HOST/mingbian/api/health"; echo
curl -s "http://$HOST/mingbian/api/demos" | python3 -c "import json,sys;d=json.load(sys.stdin);print('demos:',len(d.get('demos',[])),[x['id'] for x in d.get('demos',[])])"

rm -rf "$STAGE" "$TAR"
echo "==> 推送完成：http://$HOST/mingbian/"
