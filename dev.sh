#!/bin/bash
set -e

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

# Run pre-flight validation unless skipped
if [ "$SKIP_VALIDATE" = false ] && [ -f "validate-local.sh" ]; then
    echo "Running pre-flight validation..."
    echo ""
    if ! ./validate-local.sh; then
        echo ""
        echo "Validation failed. Fix errors above or run: ./dev.sh --skip-validate"
        exit 1
    fi
    echo ""
fi

# Store the portal directory for later use
PORTAL_DIR="$(cd "$(dirname "$0")" && pwd)"

[ ! -d "$PORTAL_DIR/.venv" ] && echo ".venv not found. Run: ./setup-local.sh" && exit 1
[ ! -d "$PORTAL_DIR/frontend/node_modules" ] && echo "frontend/node_modules not found. Run: cd frontend && npm install" && exit 1
[ ! -d "$PORTAL_DIR/../marketing/node_modules" ] && echo "marketing/node_modules not found. Run: cd ../marketing && npm install" && exit 1

source "$PORTAL_DIR/.venv/bin/activate"

cleanup() {
    echo "Stopping..."
    kill $BACKEND_PID $BOT_PID $MARKETING_PID $FRONTEND_PID 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

cd "$PORTAL_DIR"

ENV=local python app.py &
BACKEND_PID=$!

ENV=local python bot.py &
BOT_PID=$!

# Start marketing site (login portal)
(cd "$PORTAL_DIR/../marketing" && npm run dev) &
MARKETING_PID=$!

# Start frontend
(cd "$PORTAL_DIR/frontend" && npm run dev) &
FRONTEND_PID=$!

sleep 3
echo ""
echo "=========================================="
echo "Services running:"
echo "  Login:    http://localhost:4321/login"
echo "  Portal:   http://localhost:3000"
echo "  Backend:  http://localhost:8000"
echo "  Bot:      http://localhost:7860"
echo "=========================================="
echo ""

wait
