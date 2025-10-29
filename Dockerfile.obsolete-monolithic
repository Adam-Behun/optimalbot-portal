# Use Python 3.11 slim bookworm for better compatibility
FROM python:3.11-slim-bookworm

# Set working directory
WORKDIR /app

# Install system dependencies including Node.js 20
RUN apt-get update && apt-get install -y \
    ffmpeg \
    build-essential \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Update npm to latest version
RUN npm install -g npm@11.6.2

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install CPU-only torch (much smaller, no CUDA/NVIDIA dependencies)
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Copy the install_deps.py script for model pre-caching
COPY install_deps.py .

# Pre-cache the Silero VAD model during build
RUN python install_deps.py

# Copy the rest of the application
COPY . .

# Build React frontend
WORKDIR /app/frontend
RUN npm install
RUN npm run build

# Return to app root
WORKDIR /app

# Use PORT environment variable with default fallback to 8000
ENV PORT=8000

# Expose the port (informational)
EXPOSE 8000

# Run the FastAPI application
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]