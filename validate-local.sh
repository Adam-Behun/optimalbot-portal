#!/bin/bash
# =============================================================================
# Local Development Pre-Flight Validation
# Run before dev.sh to catch errors before they propagate to test deployment
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ERRORS=0
WARNINGS=0

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    ((ERRORS++))
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
    ((WARNINGS++))
}

ok() {
    echo -e "${GREEN}[OK]${NC} $1"
}

info() {
    echo -e "     $1"
}

echo "============================================================"
echo "Local Development Pre-Flight Validation"
echo "============================================================"
echo ""

# -----------------------------------------------------------------------------
# 1. Check uv package manager
# -----------------------------------------------------------------------------
echo "Checking tooling..."

if ! command -v uv &> /dev/null; then
    error "uv not found"
    info "Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
else
    UV_VERSION=$(uv --version 2>/dev/null | head -1)
    ok "uv installed ($UV_VERSION)"
fi

# -----------------------------------------------------------------------------
# 2. Check Python version (>=3.10)
# -----------------------------------------------------------------------------
if ! command -v python3 &> /dev/null; then
    error "python3 not found"
else
    PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo $PY_VERSION | cut -d. -f1)
    PY_MINOR=$(echo $PY_VERSION | cut -d. -f2)

    if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
        error "Python $PY_VERSION found, requires >=3.10"
        info "Install Python 3.10+ via pyenv or system package manager"
    else
        ok "Python $PY_VERSION"
    fi
fi

# -----------------------------------------------------------------------------
# 3. Check Node.js version (>=18)
# -----------------------------------------------------------------------------
if ! command -v node &> /dev/null; then
    error "node not found"
    info "Install Node.js 18+ via nvm or system package manager"
else
    NODE_VERSION=$(node --version | sed 's/v//')
    NODE_MAJOR=$(echo $NODE_VERSION | cut -d. -f1)

    if [ "$NODE_MAJOR" -lt 18 ]; then
        error "Node.js $NODE_VERSION found, requires >=18"
        info "Install Node.js 18+ via nvm: nvm install 18"
    else
        ok "Node.js v$NODE_VERSION"
    fi
fi

if ! command -v npm &> /dev/null; then
    error "npm not found"
else
    NPM_VERSION=$(npm --version)
    ok "npm $NPM_VERSION"
fi

echo ""

# -----------------------------------------------------------------------------
# 4. Check .env file exists and has required variables
# -----------------------------------------------------------------------------
echo "Checking environment configuration..."

if [ ! -f ".env" ]; then
    error ".env file not found"
    info "Copy .env.example to .env and fill in values"
else
    ok ".env file exists"

    # Required backend env vars
    REQUIRED_VARS=(
        "MONGO_URI"
        "JWT_SECRET_KEY"
        "ALLOWED_ORIGINS"
        "OPENAI_API_KEY"
        "GROQ_API_KEY"
        "DEEPGRAM_API_KEY"
        "CARTESIA_API_KEY"
        "DAILY_API_KEY"
        "DAILY_PHONE_NUMBER_ID"
    )

    for VAR in "${REQUIRED_VARS[@]}"; do
        VALUE=$(grep "^${VAR}=" .env 2>/dev/null | cut -d= -f2-)
        if [ -z "$VALUE" ]; then
            error "Missing or empty: $VAR"
        elif [[ "$VALUE" == *"<your-"* ]] || [[ "$VALUE" == *"your_"* ]]; then
            error "$VAR contains placeholder value"
        fi
    done

    # Check JWT_SECRET_KEY length
    JWT_KEY=$(grep "^JWT_SECRET_KEY=" .env 2>/dev/null | cut -d= -f2-)
    if [ -n "$JWT_KEY" ] && [ ${#JWT_KEY} -lt 32 ]; then
        error "JWT_SECRET_KEY must be at least 32 characters (found ${#JWT_KEY})"
        info "Generate new key: openssl rand -hex 32"
    fi

    # Check ENV=local
    ENV_VALUE=$(grep "^ENV=" .env 2>/dev/null | cut -d= -f2-)
    if [ "$ENV_VALUE" != "local" ]; then
        warn "ENV=$ENV_VALUE (expected 'local' for local development)"
        info "Set ENV=local in .env for local bot server instead of Pipecat Cloud"
    else
        ok "ENV=local"
    fi
fi

# -----------------------------------------------------------------------------
# 5. Check frontend .env.development
# -----------------------------------------------------------------------------
if [ ! -f "frontend/.env.development" ]; then
    warn "frontend/.env.development not found (will use Vite proxy)"
    info "Create with: echo 'VITE_API_BASE_URL=http://localhost:8000' > frontend/.env.development"
else
    ok "frontend/.env.development exists"
fi

echo ""

# -----------------------------------------------------------------------------
# 6. Check .venv exists
# -----------------------------------------------------------------------------
echo "Checking Python environment..."

if [ ! -d ".venv" ]; then
    error ".venv not found"
    info "Run: ./setup-local.sh"
else
    ok ".venv directory exists"
fi

# -----------------------------------------------------------------------------
# 7. Check frontend node_modules
# -----------------------------------------------------------------------------
echo "Checking frontend dependencies..."

if [ ! -d "frontend/node_modules" ]; then
    error "frontend/node_modules not found"
    info "Run: cd frontend && npm install"
else
    ok "frontend/node_modules exists"
fi

echo ""

# -----------------------------------------------------------------------------
# 8. Check port availability
# -----------------------------------------------------------------------------
echo "Checking port availability..."

check_port() {
    local PORT=$1
    local SERVICE=$2
    if command -v lsof &> /dev/null; then
        if lsof -i :$PORT -sTCP:LISTEN &> /dev/null; then
            PROCESS=$(lsof -i :$PORT -sTCP:LISTEN | tail -1 | awk '{print $1}')
            warn "Port $PORT ($SERVICE) in use by $PROCESS"
            info "Kill with: lsof -ti :$PORT | xargs kill -9"
        else
            ok "Port $PORT ($SERVICE) available"
        fi
    elif command -v ss &> /dev/null; then
        if ss -tuln | grep -q ":$PORT "; then
            warn "Port $PORT ($SERVICE) in use"
        else
            ok "Port $PORT ($SERVICE) available"
        fi
    else
        info "Cannot check port $PORT (lsof/ss not available)"
    fi
}

check_port 3000 "frontend"
check_port 7860 "bot"
check_port 8000 "backend"

echo ""

# -----------------------------------------------------------------------------
# 9. Run Python validation if venv exists
# -----------------------------------------------------------------------------
if [ -d ".venv" ]; then
    echo "Running Python validation..."

    # Activate venv and run validation script
    if [ -f "validate-local.py" ]; then
        source .venv/bin/activate
        python validate-local.py
        PY_EXIT=$?
        if [ $PY_EXIT -ne 0 ]; then
            ERRORS=$((ERRORS + PY_EXIT))
        fi
        deactivate 2>/dev/null || true
    else
        warn "validate-local.py not found, skipping Python checks"
    fi
fi

echo ""

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo "============================================================"
if [ $ERRORS -gt 0 ]; then
    echo -e "${RED}VALIDATION FAILED${NC}: $ERRORS error(s), $WARNINGS warning(s)"
    echo "Fix errors above before running dev.sh"
    exit 1
elif [ $WARNINGS -gt 0 ]; then
    echo -e "${YELLOW}VALIDATION PASSED WITH WARNINGS${NC}: $WARNINGS warning(s)"
    echo "Consider addressing warnings for smoother development"
    exit 0
else
    echo -e "${GREEN}VALIDATION PASSED${NC}"
    echo "Ready to run: ./dev.sh"
    exit 0
fi
