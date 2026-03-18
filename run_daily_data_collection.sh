#!/bin/bash
# Daily data collection wrapper script
# Runs technical data collection and analysis pipeline

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Log file
LOG_FILE="data_collection_run.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "=== Daily Data Collection Run - $TIMESTAMP ===" >> "$LOG_FILE"

# Set up environment for nvm and node
echo "Setting up environment..." >> "$LOG_FILE"

# Load nvm for Node.js version management
export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then
    . "$NVM_DIR/nvm.sh"
    # Use default node version (v22.22.1)
    nvm use default 2>/dev/null || true
    # Set PATH to include nvm's node
    export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"
    echo "NVM loaded, Node version: $(node --version 2>&1)" >> "$LOG_FILE"
else
    echo "ERROR: nvm.sh not found at $NVM_DIR/nvm.sh" >> "$LOG_FILE"
    echo "ERROR: nvm.sh not found"
    exit 1
fi

# Extract Coinbase environment variables from .zshrc
echo "Loading Coinbase credentials..." >> "$LOG_FILE"
if [ -f ~/.zshrc ]; then
    export COINBASE_API_KEY=$(grep '^export COINBASE_API_KEY=' ~/.zshrc | cut -d= -f2- | tr -d '"'"'")
    export COINBASE_API_SECRET=$(grep '^export COINBASE_API_SECRET=' ~/.zshrc | cut -d= -f2- | tr -d '"'"'")
    
    if [ -z "$COINBASE_API_KEY" ] || [ -z "$COINBASE_API_SECRET" ]; then
        echo "ERROR: Could not extract Coinbase credentials from .zshrc" >> "$LOG_FILE"
        echo "ERROR: Could not extract Coinbase credentials from .zshrc"
        exit 1
    fi
    
    echo "Coinbase credentials loaded" >> "$LOG_FILE"
else
    echo "ERROR: .zshrc not found" >> "$LOG_FILE"
    echo "ERROR: .zshrc not found"
    exit 1
fi

# Check Python availability
echo "Checking Python..." >> "$LOG_FILE"
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found" >> "$LOG_FILE"
    echo "ERROR: python3 not found"
    exit 1
fi

# Run the daily data collection pipeline
echo "Running daily data collection pipeline..." >> "$LOG_FILE"
python3 daily_data_collection.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "SUCCESS: Daily data collection completed at $TIMESTAMP" >> "$LOG_FILE"
    echo "✅ Daily data collection completed at $TIMESTAMP"
else
    echo "ERROR: Data collection failed with exit code $EXIT_CODE at $TIMESTAMP" >> "$LOG_FILE"
    echo "❌ Data collection failed at $TIMESTAMP"
fi

echo "" >> "$LOG_FILE"

# Exit with the Python script's exit code
exit $EXIT_CODE