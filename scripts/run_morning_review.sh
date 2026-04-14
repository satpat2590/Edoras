#!/bin/bash
# Run the morning strategic review

EDORAS_DIR="$HOME/edoras"
cd "$EDORAS_DIR"

export PYTHONPATH="$EDORAS_DIR/src:$PYTHONPATH"

# Load environment variables
source load_env.sh

echo "================================================"
echo "Running Morning Strategic Review"
echo "Date: $(date)"
echo "================================================"
echo ""

# Run the trading agent in morning mode
python3 -m llm.trading_agent --run 2>&1 | tee /tmp/morning_review_output.txt

echo ""
echo "================================================"
echo "Review completed at: $(date)"
echo "================================================"

# Check if Telegram message was sent
if grep -q "Telegram message sent" /tmp/morning_review_output.txt; then
    echo "✓ Telegram report was sent"
else
    echo "✗ Telegram report may not have been sent"
fi

# Check LLM usage
echo ""
echo "LLM Usage Summary:"
if grep -q "DeepSeek Reasoner responded" /tmp/morning_review_output.txt; then
    echo "✓ Used DeepSeek Reasoner (primary)"
elif grep -q "Nous Research responded" /tmp/morning_review_output.txt; then
    echo "✓ Used Nous Research Hermes (fallback)"
elif grep -q "Claude responded" /tmp/morning_review_output.txt; then
    echo "✓ Used Claude Sonnet (fallback)"
elif grep -q "OpenAI GPT-4o responded" /tmp/morning_review_output.txt; then
    echo "✓ Used OpenAI GPT-4o (fallback)"
elif grep -q "MLX responded" /tmp/morning_review_output.txt; then
    echo "✓ Used MLX local server (last resort)"
else
    echo "✗ No LLM response detected"
fi

# Check for errors
echo ""
echo "Error Summary:"
if grep -q "ERROR\|Error\|error" /tmp/morning_review_output.txt; then
    grep -i "error" /tmp/morning_review_output.txt | head -5
else
    echo "✓ No errors detected"
fi