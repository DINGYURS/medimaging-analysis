#!/bin/bash
PIDFILE="/data/medimaging-analysis/frontend/frontend.pid"
LOGFILE="/data/medimaging-analysis/frontend/frontend.log"

export NVM_DIR="/data/nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
nvm use 24 > /dev/null 2>&1

start() {
    if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
        echo "Frontend is already running with PID $(cat $PIDFILE)"
        return 0
    fi
    echo "Starting Frontend on port 5180..."
    cd /data/medimaging-analysis/frontend
    nohup pnpm preview --host 0.0.0.0 --port 5180 > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 2
    if kill -0 $(cat "$PIDFILE") 2>/dev/null; then
        echo "Frontend started successfully (PID: $(cat $PIDFILE))"
    else
        echo "Frontend failed to start. Logs:"
        cat "$LOGFILE"
    fi
}

stop() {
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        echo "Stopping Frontend (PID: $PID)..."
        kill -15 $PID 2>/dev/null
        sleep 2
        kill -9 $PID 2>/dev/null
        rm -f "$PIDFILE"
        echo "Frontend stopped."
    else
        echo "No PID file found."
    fi
}

status() {
    if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
        echo "Frontend is running (PID: $(cat $PIDFILE))"
    else
        echo "Frontend is stopped"
    fi
}

case "$1" in
    start) start ;;
    stop) stop ;;
    restart) stop; sleep 1; start ;;
    status) status ;;
    *) echo "Usage: $0 {start|stop|restart|status}" ;;
esac
