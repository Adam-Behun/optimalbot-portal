#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PORTAL_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$PORTAL_DIR/.dev-logs"

# Kill any stale processes from previous runs
pkill -f "tail -f.*\.dev-logs" 2>/dev/null || true
for port in 8000 7860 3000 3001 4321 4322; do
    lsof -ti :$port 2>/dev/null | xargs -r kill -9 2>/dev/null || true
done
rm -rf "$LOG_DIR"
sleep 0.5

mkdir -p "$LOG_DIR"

# Parse arguments
SKIP_VALIDATE=false
for arg in "$@"; do
    case $arg in
        --skip-validate|-s)
            SKIP_VALIDATE=true
            shift
            ;;
    esac
done

# Quick checks first
[ ! -d "$PORTAL_DIR/.venv" ] && echo -e "${RED}.venv not found. Run: ./setup-local.sh${NC}" && exit 1
[ ! -d "$PORTAL_DIR/frontend/node_modules" ] && echo -e "${RED}frontend/node_modules not found. Run: cd frontend && npm install${NC}" && exit 1
[ ! -d "$PORTAL_DIR/../marketing/node_modules" ] && echo -e "${RED}marketing/node_modules not found. Run: cd ../marketing && npm install${NC}" && exit 1

# Run validation
if [ "$SKIP_VALIDATE" = false ]; then
    source "$PORTAL_DIR/.venv/bin/activate"
    if ! python validate.py --quick > "$LOG_DIR/validate.log" 2>&1; then
        echo -e "${RED}Validation failed${NC}"
        grep -E "\[ERROR\]" "$LOG_DIR/validate.log" 2>/dev/null || cat "$LOG_DIR/validate.log"
        echo ""
        echo "Full log: $LOG_DIR/validate.log"
        echo "Or run: ./dev.sh --skip-validate"
        exit 1
    fi
    deactivate 2>/dev/null || true
fi

source "$PORTAL_DIR/.venv/bin/activate"
cd "$PORTAL_DIR"

# Reset eval patients
echo ""
echo "Eval Patients:"
python scripts/insert_eval_patients.py 2>/dev/null || echo -e "${RED}Failed to reset eval patients${NC}"

# Start services with logs captured to files
ENV=local python app.py > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

ENV=local python bot.py > "$LOG_DIR/bot.log" 2>&1 &
BOT_PID=$!

(cd "$PORTAL_DIR/../marketing" && npm run dev > "$LOG_DIR/marketing.log" 2>&1) &
MARKETING_PID=$!

(cd "$PORTAL_DIR/frontend" && npm run dev > "$LOG_DIR/frontend.log" 2>&1) &
FRONTEND_PID=$!

# Wait for services to start and check health
check_service() {
    local name=$1
    local port=$2
    local pid=$3
    local log=$4
    local attempts=0

    while [ $attempts -lt 30 ]; do
        # Check if process died
        if ! kill -0 $pid 2>/dev/null; then
            echo -e "${RED}$name failed${NC}"
            tail -20 "$log" | grep -iE "(error|exception|failed|traceback)" 2>/dev/null || tail -10 "$log"
            return 1
        fi
        # Check if port is listening
        if command -v lsof &>/dev/null && lsof -i :$port -sTCP:LISTEN &>/dev/null; then
            echo -e "${GREEN}$name${NC} Ok"
            return 0
        fi
        sleep 0.5
        ((attempts++))
    done

    echo -e "${RED}$name timeout${NC}"
    tail -10 "$log"
    return 1
}

echo ""
check_service "Backend " 8000 $BACKEND_PID "$LOG_DIR/backend.log" || exit 1
check_service "Bot     " 7860 $BOT_PID "$LOG_DIR/bot.log" || exit 1
check_service "Frontend" 3000 $FRONTEND_PID "$LOG_DIR/frontend.log" || exit 1
check_service "Login   " 4321 $MARKETING_PID "$LOG_DIR/marketing.log" || exit 1

echo ""
echo "Ready: localhost:3000 (portal) | localhost:4321/login"
echo ""

# Stream all logs to terminal
tail -f "$LOG_DIR/backend.log" "$LOG_DIR/bot.log" "$LOG_DIR/frontend.log" "$LOG_DIR/marketing.log" &
TAIL_PID=$!

cleanup() {
    # Disable trap to prevent re-entry on second Ctrl+C
    trap - SIGINT SIGTERM

    echo ""
    echo "Stopping..."

    # Kill tail first to stop log output
    if [ -n "$TAIL_PID" ]; then
        kill $TAIL_PID 2>/dev/null
        wait $TAIL_PID 2>/dev/null
    fi

    # Graceful shutdown: kill children first, then parents
    for pid in $BACKEND_PID $BOT_PID $MARKETING_PID $FRONTEND_PID; do
        if [ -n "$pid" ] && kill -0 $pid 2>/dev/null; then
            pkill -P $pid 2>/dev/null
            kill $pid 2>/dev/null
        fi
    done

    # Give processes time to exit gracefully
    sleep 0.3

    # Force kill any stubborn processes
    for pid in $BACKEND_PID $BOT_PID $MARKETING_PID $FRONTEND_PID; do
        if [ -n "$pid" ] && kill -0 $pid 2>/dev/null; then
            pkill -9 -P $pid 2>/dev/null
            kill -9 $pid 2>/dev/null
        fi
    done

    # Final cleanup: kill anything still on dev ports
    for port in 8000 7860 3000 3001 4321 4322; do
        lsof -ti :$port 2>/dev/null | xargs -r kill -9 2>/dev/null || true
    done

    rm -rf "$LOG_DIR"
    exit 0
}
trap cleanup SIGINT SIGTERM

wait
