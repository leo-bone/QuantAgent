#!/bin/bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# QuantAgent 一键启动脚本
#
# 用法:
#   ./start.sh              # 全栈启动 (Agent + API + Dashboard)
#   ./start.sh agent        # 只启动 Agent (终端模式)
#   ./start.sh dashboard    # 只启动 Dashboard
#   ./start.sh stop         # 停止所有服务
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -e

# ─── 项目路径 ───
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# ─── 激活虚拟环境 ───
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "❌ 未找到 .venv，请先运行: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# ─── 清除代理（Clash 等代理会拦截 localhost 请求）───
export QUANTAGENT_KEEP_PROXY=0
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy no_proxy NO_PROXY
echo "✅ 已清除代理环境变量"

# ─── 检查 .env ───
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "⚠️  已从 .env.example 创建 .env，请填写 API key 后重新启动"
fi

# ─── PID 文件 ───
PID_DIR="/tmp/quantagent"
mkdir -p "$PID_DIR"

stop_all() {
    echo "🛑 正在停止所有 QuantAgent 服务..."
    for pidfile in "$PID_DIR"/*.pid; do
        if [ -f "$pidfile" ]; then
            pid=$(cat "$pidfile")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null && echo "  已停止 PID $pid ($(basename "$pidfile" .pid))"
            fi
            rm -f "$pidfile"
        fi
    done
    echo "✅ 所有服务已停止"
}

case "${1:-all}" in
    stop)
        stop_all
        exit 0
        ;;
    agent)
        echo "🤖 启动 Agent (终端模式, paper)..."
        python scripts/start_agent.py --mode paper --poll 60
        ;;
    dashboard)
        echo "📊 启动 Dashboard..."
        python scripts/run_dashboard.py &
        DASH_PID=$!
        echo "$DASH_PID" > "$PID_DIR/dashboard.pid"
        sleep 5
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "📊 Dashboard 已启动!"
        echo ""
        echo "  👉 在浏览器中打开: http://127.0.0.1:8050"
        echo ""
        echo "  ⚠️  重要: 必须用 127.0.0.1，不要用 localhost"
        echo "     (Clash 代理会把 localhost 重定向到 www.localhost.com)"
        echo ""
        echo "  按 Ctrl+C 停止"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        wait $DASH_PID
        ;;
    all|*)
        echo "🚀 启动 QuantAgent 全栈 (Agent + API + Dashboard)..."

        # 1. 启动 API + Agent (后台)
        python -c "
import uvicorn, sys, os
sys.path.insert(0, '$PROJECT_ROOT')
# 再次确保代理被清除
for k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','all_proxy'):
    os.environ.pop(k, None)
from quantagent.api.app import app
uvicorn.run(app, host='0.0.0.0', port=8000, log_level='info')
" &
        API_PID=$!
        echo "$API_PID" > "$PID_DIR/api.pid"
        echo "✅ API 服务器已启动 (PID: $API_PID)"

        # 2. 等待 API 就绪
        echo "⏳ 等待 API 就绪..."
        for i in $(seq 1 15); do
            if curl -s http://127.0.0.1:8000/ > /dev/null 2>&1; then
                echo "✅ API 已就绪"
                break
            fi
            sleep 1
        done

        # 3. 启动 Dashboard (后台)
        python scripts/run_dashboard.py --with-api &
        DASH_PID=$!
        echo "$DASH_PID" > "$PID_DIR/dashboard.pid"
        echo "✅ Dashboard 已启动 (PID: $DASH_PID)"

        sleep 5

        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "🚀 QuantAgent 全栈已启动!"
        echo ""
        echo "  📊 Dashboard:  http://127.0.0.1:8050"
        echo "  🔌 API:        http://127.0.0.1:8000"
        echo "  📖 API 文档:   http://127.0.0.1:8000/docs"
        echo ""
        echo "  ⚠️  重要: 必须用 127.0.0.1，不要用 localhost!"
        echo "     (Clash 代理会把 localhost 重定向到 www.localhost.com)"
        echo ""
        echo "  🛑 停止服务: ./scripts/start.sh stop"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

        # 等待任意进程退出
        wait -n $API_PID $DASH_PID 2>/dev/null || wait $DASH_PID
        ;;
esac
