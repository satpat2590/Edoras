#!/bin/bash
# Load environment variables for Edoras trading system
# Source this script before running any Edoras commands

# Load from .zshrc if it exists
if [ -f "$HOME/.zshrc" ]; then
    # Simple approach: source the .zshrc file
    # First, save current environment
    TEMP_ENV=$(mktemp)
    env > "$TEMP_ENV"
    
    # Source .zshrc in a subshell to avoid affecting current shell
    (source "$HOME/.zshrc" 2>/dev/null; env) | grep -E "^(DEEPSEEK|NOUS_RESEARCH|ANTHROPIC|OPENAI|MLX)_" > /tmp/edoras_env.txt
    
    # Load the specific variables we need
    while IFS='=' read -r key value; do
        export "$key"="$value"
    done < /tmp/edoras_env.txt
    
    # Clean up
    rm -f "$TEMP_ENV" /tmp/edoras_env.txt
fi

# Set default MLX URL if not set
if [ -z "${MLX_BASE_URL:-}" ]; then
    export MLX_BASE_URL="http://192.168.1.50:8008/v1"
fi

# Also check for DEEPSEEK_API (without _KEY) and copy to DEEPSEEK_API_KEY
if [ -n "${DEEPSEEK_API:-}" ] && [ -z "${DEEPSEEK_API_KEY:-}" ]; then
    export DEEPSEEK_API_KEY="$DEEPSEEK_API"
fi

# Also check for NOUS_API_KEY (without _RESEARCH) and copy to NOUS_RESEARCH_API_KEY
if [ -n "${NOUS_API_KEY:-}" ] && [ -z "${NOUS_RESEARCH_API_KEY:-}" ]; then
    export NOUS_RESEARCH_API_KEY="$NOUS_API_KEY"
fi

echo "Environment loaded for Edoras:"
[ -n "${DEEPSEEK_API_KEY:-}" ] && echo "  DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY:0:10}..."
[ -n "${NOUS_RESEARCH_API_KEY:-}" ] && echo "  NOUS_RESEARCH_API_KEY: ${NOUS_RESEARCH_API_KEY:0:10}..."
[ -n "${ANTHROPIC_API_KEY:-}" ] && echo "  ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:0:10}..."
[ -n "${OPENAI_API_KEY:-}" ] && echo "  OPENAI_API_KEY: ${OPENAI_API_KEY:0:10}..."
echo "  MLX_BASE_URL: ${MLX_BASE_URL:-http://192.168.1.50:8008/v1}"