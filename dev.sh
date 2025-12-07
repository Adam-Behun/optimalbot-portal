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

[ ! -d ".venv" ] && echo ".venv not found. Run: ./setup-local.sh" && exit 1
[ ! -d "frontend/node_modules" ] && echo "frontend/node_modules not found. Run: cd frontend && npm install" && exit 1

source .venv/bin/activate

cleanup() {
    echo "Stopping..."
    kill $BACKEND_PID $BOT_PID 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

ENV=local python app.py &
BACKEND_PID=$!

ENV=local python bot.py &
BOT_PID=$!

sleep 2
echo "Backend: http://localhost:8000"
echo "Bot: http://localhost:7860"
echo "Frontend: http://localhost:3000"
echo ""

cd frontend && npm run dev
