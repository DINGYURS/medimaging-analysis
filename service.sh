#!/bin/bash
BASE_DIR="/data/medimaging-analysis"

case "$1" in
    start)
        echo "=== 启动后端服务 ==="
        $BASE_DIR/backend/service.sh start
        echo "=== 启动前端服务 ==="
        $BASE_DIR/frontend/service.sh start
        ;;
    stop)
        echo "=== 停止前端服务 ==="
        $BASE_DIR/frontend/service.sh stop
        echo "=== 停止后端服务 ==="
        $BASE_DIR/backend/service.sh stop
        ;;
    restart)
        echo "=== 重启服务 ==="
        $BASE_DIR/frontend/service.sh stop
        $BASE_DIR/backend/service.sh stop
        sleep 1
        $BASE_DIR/backend/service.sh start
        $BASE_DIR/frontend/service.sh start
        ;;
    status)
        echo "=== 检查后端状态 ==="
        $BASE_DIR/backend/service.sh status
        echo "=== 检查前端状态 ==="
        $BASE_DIR/frontend/service.sh status
        ;;
    *)
        echo "用法: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac