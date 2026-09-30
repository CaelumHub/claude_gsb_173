#!/usr/bin/env bash
# 启动 MiniLang 在线编译器与解释器调试平台
#
# 后端是纯标准库的 HTTP 服务，同时托管前端静态页面（frontend/），
# 因此一个进程即可同时打开「后端接口 + 前端页面」。
#
# 用法：
#   ./start.sh                 # 默认 http://127.0.0.1:8080
#   ./start.sh 8099            # 指定端口
#   ./start.sh --seed          # 首次启动带上，写入演示项目
#   GSB_PORT=9000 ./start.sh   # 环境变量方式指定端口

set -euo pipefail

# 切到脚本所在目录（项目根目录 gsb5/），保证无论从哪里调用都能找到 backend/
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[start] 错误：未找到 python3，请先安装 Python 3" >&2
  exit 1
fi

# 解析端口与主机：命令行参数 > 环境变量 > 默认值
PORT="${GSB_PORT:-8080}"
HOST="${GSB_HOST:-127.0.0.1}"
EXTRA=()
for a in "$@"; do
  case "$a" in
    --port=*) PORT="${a#--port=}" ;;
    --host=*) HOST="${a#--host=}" ;;
    --*)      EXTRA+=("$a") ;;
    *)
      # 纯数字的位置参数视为端口号
      if [[ "$a" =~ ^[0-9]+$ ]]; then PORT="$a"; else EXTRA+=("$a"); fi
      ;;
  esac
done

export GSB_PORT="$PORT"
export GSB_HOST="$HOST"

URL="http://${HOST}:${PORT}/"

echo "======================================================"
echo "  MiniLang 在线编译器与解释器调试平台"
echo "======================================================"
echo "  前端地址：${URL}"
echo "  后端接口：${URL}api/projects"
echo "  停止服务：按 Ctrl+C"
echo "======================================================"
echo ""

exec python3 -m backend.run "${EXTRA[@]}"
