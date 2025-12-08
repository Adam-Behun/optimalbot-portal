#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PORTAL_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$PORTAL_DIR/.dev-logs"
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

# Run pre-flight validation (quiet mode - only show errors)
if [ "$SKIP_VALIDATE" = false ] && [ -f "validate-local.sh" ]; then
    if ! ./validate-local.sh > "$LOG_DIR/validate.log" 2>&1; then
        echo -e "${RED}Validation failed${NC}"
        # Show only ERROR lines
        grep -E "\[ERROR\]" "$LOG_DIR/validate.log" 2>/dev/null || cat "$LOG_DIR/validate.log"
        echo ""
        echo "Full log: $LOG_DIR/validate.log"
        echo "Or run: ./dev.sh --skip-validate"
        exit 1
    fi
fi

# Quick checks
[ ! -d "$PORTAL_DIR/.venv" ] && echo -e "${RED}.venv not found. Run: ./setup-local.sh${NC}" && exit 1
[ ! -d "$PORTAL_DIR/frontend/node_modules" ] && echo -e "${RED}frontend/node_modules not found. Run: cd frontend && npm install${NC}" && exit 1
[ ! -d "$PORTAL_DIR/../marketing/node_modules" ] && echo -e "${RED}marketing/node_modules not found. Run: cd ../marketing && npm install${NC}" && exit 1

source "$PORTAL_DIR/.venv/bin/activate"
cd "$PORTAL_DIR"

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
    echo ""
    echo "Stopping..."
    kill $TAIL_PID $BACKEND_PID $BOT_PID $MARKETING_PID $FRONTEND_PID 2>/dev/null
    rm -rf "$LOG_DIR"
    exit 0
}
trap cleanup SIGINT SIGTERM

wait
