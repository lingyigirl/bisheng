#!/usr/bin/env bash
# =============================================================
# BiSheng 源码运行管理脚本（后端/Worker/前端）
# 中间件（mysql/redis/es/minio/milvus/etcd/openfga）用 docker compose 管理：
#   cd /root/bisheng/bisheng/docker && docker compose -p bisheng up -d <service>
# 用法: ./manage.sh {status|start|stop|restart|logs}
# =============================================================
set -euo pipefail

BACKEND_DIR="/root/bisheng/bisheng/src/backend"
FRONTEND_DIR="/root/bisheng/bisheng/src/frontend"
DATA_DIR="/root/bisheng/bisheng/data"
ENV_FILE="$DATA_DIR/backend.env"
LOG_DIR="$DATA_DIR/logs"
VENV="$BACKEND_DIR/.venv/bin"

start_backend() {
  echo "[start] backend API (:7860)"
  ( cd "$BACKEND_DIR" && set -a && source "$ENV_FILE" && set +a && \
    nohup "$VENV/uvicorn" bisheng.main:app --host 0.0.0.0 --port 7860 --workers 2 --no-access-log \
      > "$LOG_DIR/api.log" 2>&1 & echo $! > "$DATA_DIR/api.pid" )
}

start_workers() {
  echo "[start] celery workers + beat"
  ( cd "$BACKEND_DIR" && set -a && source "$ENV_FILE" && set +a && \
    nohup "$VENV/celery" -A bisheng.worker.main worker -l info -c 20 -P threads -Q knowledge_celery -n knowledge@%h \
      > "$LOG_DIR/celery-knowledge.log" 2>&1 & echo $! > "$DATA_DIR/knowledge.pid" )
  ( cd "$BACKEND_DIR" && set -a && source "$ENV_FILE" && set +a && \
    nohup "$VENV/celery" -A bisheng.worker.main worker -l info -c 100 -P threads -Q workflow_celery -n workflow@%h \
      > "$LOG_DIR/celery-workflow.log" 2>&1 & echo $! > "$DATA_DIR/workflow.pid" )
  ( cd "$BACKEND_DIR" && set -a && source "$ENV_FILE" && set +a && \
    nohup "$VENV/celery" -A bisheng.worker.main worker -l info -c 100 -P threads -Q celery -n default@%h \
      > "$LOG_DIR/celery-default.log" 2>&1 & echo $! > "$DATA_DIR/default.pid" )
  ( cd "$BACKEND_DIR" && set -a && source "$ENV_FILE" && set +a && \
    nohup "$VENV/celery" -A bisheng.worker.main beat -l info \
      > "$LOG_DIR/celery-beat.log" 2>&1 & echo $! > "$DATA_DIR/beat.pid" )
  ( cd "$BACKEND_DIR" && set -a && source "$ENV_FILE" && set +a && \
    nohup "$VENV/python" bisheng/linsight/worker.py --worker_num 2 --max_concurrency 5 \
      > "$LOG_DIR/linsight.log" 2>&1 & echo $! > "$DATA_DIR/linsight.pid" )
}

start_frontend() {
  # 构建产物 + vite preview 静态服务（2026-09-05 起替代 dev server，页面加载从 ~1000 请求降到几十个）
  # 构建见 build_frontend；preview 继承 server.proxy/server.https（读取 .env.production）
  echo "[start] frontend platform (:3001) + client (:4001)"
  ( cd "$FRONTEND_DIR/platform" && nohup ./node_modules/.bin/vite preview --host 0.0.0.0 --port 3001 --strictPort --outDir build > "$LOG_DIR/frontend-platform.log" 2>&1 & echo $! > "$DATA_DIR/platform.pid" )
  ( cd "$FRONTEND_DIR/client" && nohup ./node_modules/.bin/vite preview --host 0.0.0.0 --port 4001 --strictPort --outDir build > "$LOG_DIR/frontend-client.log" 2>&1 & echo $! > "$DATA_DIR/client.pid" )
}

build_frontend() {
  # 生产构建（先构建 locales；需要 pnpm：corepack enable 后 node bin 目录里有 shim）
  echo "[build] frontend platform + client"
  export PATH="/root/.nvm/versions/node/v24.19.0/bin:$PATH"
  ( cd "$FRONTEND_DIR" && pnpm --filter bisheng build && pnpm --filter bishengchat build )
}

stop_all() {
  echo "[stop] source services"
  for f in "$DATA_DIR"/*.pid; do
    [ -f "$f" ] || continue
    pid=$(cat "$f" 2>/dev/null || true)
    [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null && echo "  stopped $(basename "$f" .pid) pid=$pid" || true
    rm -f "$f"
  done
  pkill -x uvicorn 2>/dev/null || true
  pkill -x celery 2>/dev/null || true
  pkill -f "node_modules/.bin/vite preview" 2>/dev/null || true
  sleep 2
}

status() {
  echo "== source services =="
  for f in "$DATA_DIR"/*.pid; do
    [ -f "$f" ] || continue
    pid=$(cat "$f" 2>/dev/null || true)
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      echo "  RUNNING $(basename "$f" .pid) pid=$pid"
    else
      echo "  STOPPED $(basename "$f" .pid)"
    fi
  done
  echo "== docker middleware =="
  docker ps --filter "name=bisheng-" --format "  {{.Names}}\t{{.Status}}"
}

case "${1:-status}" in
  start)   start_backend; start_workers; start_frontend; sleep 5; status ;;
  stop)    stop_all ;;
  restart) stop_all; sleep 2; start_backend; start_workers; start_frontend; sleep 5; status ;;
  status)  status ;;
  build)   build_frontend ;;
  logs)    shift || true; tail -f "$LOG_DIR/${1:-api}.log" ;;
  *) echo "用法: $0 {start|stop|restart|status|build|logs [api|celery-knowledge|celery-workflow|celery-default|celery-beat|linsight|frontend-platform|frontend-client]}"; exit 1 ;;
esac
