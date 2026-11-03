#!/bin/bash
PIDFILE="/data/medimaging-analysis/backend/backend.pid"
LOGFILE="/data/medimaging-analysis/backend/backend.log"
PYTHON="/data/miniconda3/envs/medimaging-analysis/bin/python"

start() {
    if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
        echo "Backend is already running with PID $(cat $PIDFILE)"
        return 0
    fi
    echo "Starting Backend on port 8020..."
    cd /data/medimaging-analysis/backend
    export PYTHONPATH=/data/medimaging-analysis/backend:/data/medimaging-analysis
    nohup $PYTHON -m uvicorn main:app --host 0.0.0.0 --port 8020 > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 2
    if kill -0 $(cat "$PIDFILE") 2>/dev/null; then
        echo "Backend started successfully (PID: $(cat $PIDFILE))"
    else
        echo "Backend failed to start. Logs:"
        cat "$LOGFILE"
    fi
}

stop() {
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        echo "Stopping Backend (PID: $PID)..."
        kill -15 $PID 2>/dev/null
        sleep 2
        kill -9 $PID 2>/dev/null
        rm -f "$PIDFILE"
        echo "Backend stopped."
    else
        echo "No PID file found."
    fi
}

status() {
    if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
        echo "Backend is running (PID: $(cat $PIDFILE))"
    else
        echo "Backend is stopped"
    fi
}

case "$1" in
    start) start ;;
    stop) stop ;;
    restart) stop; sleep 1; start ;;
    status) status ;;
    *) echo "Usage: $0 {start|stop|restart|status}" ;;
esac
