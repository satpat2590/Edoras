#!/bin/bash
source "$(dirname "$0")/lib_telegram.sh"
# Daily crypto data collection and analysis wrapper
# Runs full data collection, indicator calculation, and analysis

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDORAS_DIR="$(dirname "$SCRIPT_DIR")"
cd "$EDORAS_DIR"
export PYTHONPATH="$EDORAS_DIR/src:$PYTHONPATH"

# Log file with rotation
mkdir -p logs
LOG_DATE=$(date '+%Y%m%d')
LOG_FILE="logs/daily_analysis_${LOG_DATE}.log"
find logs -name "daily_analysis_*.log" -mtime +7 -delete 2>/dev/null || true
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Create structured log header
echo "=== Daily Crypto Analysis - $TIMESTAMP ===" >> "$LOG_FILE"
echo "{\"timestamp\": \"$TIMESTAMP\", \"event\": \"analysis_start\", \"log_file\": \"$LOG_FILE\"}" >> "$LOG_FILE"

# Set up environment
echo "{\"timestamp\": \"$TIMESTAMP\", \"event\": \"environment_setup\", \"message\": \"Setting up environment...\"}" >> "$LOG_FILE"
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"

# Run pre-flight verification
echo "{\"timestamp\": \"$TIMESTAMP\", \"event\": \"verification_start\", \"message\": \"Running pre-flight verification...\"}" >> "$LOG_FILE"
if ! python3 "$SCRIPT_DIR/verify_daily_env.py" --quiet; then
    VERIFY_EXIT_CODE=$?
    echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"verification_failed\", \"exit_code\": $VERIFY_EXIT_CODE, \"message\": \"Pre-flight verification failed\"}" >> "$LOG_FILE"
    echo "❌ Pre-flight verification failed. Check verification_results.json for details."
    
    # Send failure alert
    if command -v curl &> /dev/null; then
        ALERT_MESSAGE="🚨 Daily crypto analysis PRE-FLIGHT VERIFICATION FAILED. Check verification_results.json"
        send_telegram "$ALERT_MESSAGE" >> "$LOG_FILE" 2>&1 || true
    fi
    exit $VERIFY_EXIT_CODE
fi
echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"verification_passed\", \"message\": \"Pre-flight verification passed\"}" >> "$LOG_FILE"
echo "✓ Pre-flight verification passed"

# Run daily collection with timeout (15 minutes - increased for retries)
echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"collection_start\", \"message\": \"Running crypto_data_collector.py --daily (15min timeout)...\"}" >> "$LOG_FILE"
START_TIME=$(date +%s)
timeout 900 python3 -m data.crypto_data_collector --daily >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

if [ $EXIT_CODE -eq 124 ]; then
    echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"timeout\", \"duration\": $DURATION, \"message\": \"Daily analysis timed out after 900 seconds\"}" >> "$LOG_FILE"
    echo "❌ Daily analysis timed out"
fi

if [ $EXIT_CODE -eq 0 ]; then
    echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"collection_completed\", \"duration\": $DURATION, \"message\": \"Daily analysis completed successfully\"}" >> "$LOG_FILE"
    
    # Run post-collection health check
    echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"health_check_start\", \"message\": \"Running post-collection health check...\"}" >> "$LOG_FILE"
    if python3 -c "
import sqlite3
import json
from datetime import datetime, timedelta
conn = sqlite3.connect('$EDORAS_DIR/crypto_data.db')
cursor = conn.cursor()

# Check candles inserted today
today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
today_ts = int(today.timestamp())
cursor.execute('SELECT COUNT(*) FROM candlesticks WHERE timestamp >= ?', (today_ts,))
candles_today = cursor.fetchone()[0]

# Check freshness of latest candles
cursor.execute('SELECT symbol, MAX(timestamp) FROM candlesticks WHERE timeframe=\"1d\" GROUP BY symbol')
latest_timestamps = cursor.fetchall()

fresh_symbols = []
stale_symbols = []
for symbol, ts in latest_timestamps:
    if ts and (datetime.now().timestamp() - ts) < 86400 * 2:  # Less than 2 days old
        fresh_symbols.append(symbol)
    else:
        stale_symbols.append(symbol)

conn.close()

health_result = {
    'candles_today': candles_today,
    'fresh_symbols_count': len(fresh_symbols),
    'stale_symbols_count': len(stale_symbols),
    'stale_symbols': stale_symbols[:10],  # Limit output
    'timestamp': datetime.now().isoformat()
}

print(json.dumps(health_result))
" >> "$LOG_FILE" 2>&1; then
        echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"health_check_completed\", \"message\": \"Post-collection health check completed\"}" >> "$LOG_FILE"
    else
        echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"health_check_failed\", \"message\": \"Post-collection health check failed\"}" >> "$LOG_FILE"
    fi
    
    # Enhanced report detection
    echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"report_detection\", \"message\": \"Checking for generated reports...\"}" >> "$LOG_FILE"
    REPORT_FOUND=false
    REPORT_PATHS=(
        "reports/portfolio_technical_report.txt"
        "./portfolio_technical_report.txt"
        "portfolio_technical_report.txt"
    )
    
    REPORT_FILE=""
    for path in "${REPORT_PATHS[@]}"; do
        if [ -f "$path" ]; then
            REPORT_FILE="$path"
            echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"report_found\", \"path\": \"$path\", \"message\": \"Technical report found\"}" >> "$LOG_FILE"
            REPORT_FOUND=true
            break
        fi
    done
    
    if [ "$REPORT_FOUND" = true ]; then
        # Archive old report
        ARCHIVE_DIR="reports/archive"
        mkdir -p "$ARCHIVE_DIR"
        ARCHIVE_NAME="portfolio_technical_report_$(date '+%Y%m%d_%H%M%S').txt"
        cp "$REPORT_FILE" "$ARCHIVE_DIR/$ARCHIVE_NAME"
        echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"report_archived\", \"archive_path\": \"$ARCHIVE_DIR/$ARCHIVE_NAME\"}" >> "$LOG_FILE"
        
        # Validate report content before sending
        if [ -s "$REPORT_FILE" ]; then
            REPORT_LINES=$(wc -l < "$REPORT_FILE")
            REPORT_SIZE=$(wc -c < "$REPORT_FILE")
            echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"report_validated\", \"lines\": $REPORT_LINES, \"size\": $REPORT_SIZE}" >> "$LOG_FILE"
            
            # Send report via Telegram if OpenClaw is available
            if command -v curl &> /dev/null; then
                echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"telegram_send_start\", \"message\": \"Sending report to Telegram...\"}" >> "$LOG_FILE"
                REPORT_CONTENT=$(head -c 4000 "$REPORT_FILE")  # Limit to Telegram max
                if send_telegram "$REPORT_CONTENT" >> "$LOG_FILE" 2>&1; then
                    echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"telegram_sent\", \"message\": \"Report sent to Telegram\"}" >> "$LOG_FILE"
                    echo "✅ Daily analysis report sent to Telegram"
                else
                    echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"telegram_failed\", \"message\": \"Failed to send report to Telegram\"}" >> "$LOG_FILE"
                    echo "⚠️ Analysis completed but Telegram send failed"
                fi
            else
                echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"telegram_unavailable\", \"message\": \"telegram helper not found, report not sent\"}" >> "$LOG_FILE"
                echo "⚠️ Analysis completed but send_telegram not available"
            fi
        else
            echo "{\"timestamp\": \"$(date '+%Y-%m-%m-%d %H:%M:%S')\", \"event\": \"report_empty\", \"message\": \"Report file is empty\"}" >> "$LOG_FILE"
            echo "⚠️ Report file exists but is empty"
        fi
    else
        echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"report_not_found\", \"message\": \"Technical report not found in any expected location\"}" >> "$LOG_FILE"
        echo "⚠️ Analysis completed but report file not generated"
    fi
else
    echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"collection_failed\", \"exit_code\": $EXIT_CODE, \"duration\": $DURATION, \"message\": \"Daily analysis failed\"}" >> "$LOG_FILE"
    echo "❌ Daily analysis failed"
    
    # Send failure alert via Telegram if OpenClaw is available
    if command -v curl &> /dev/null; then
        echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"failure_alert_start\", \"message\": \"Sending failure alert to Telegram...\"}" >> "$LOG_FILE"
        ALERT_MESSAGE="🚨 Daily crypto analysis FAILED with exit code $EXIT_CODE (duration: ${DURATION}s). Check logs: $LOG_FILE"
        if send_telegram "$ALERT_MESSAGE" >> "$LOG_FILE" 2>&1; then
            echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"failure_alert_sent\", \"message\": \"Failure alert sent to Telegram\"}" >> "$LOG_FILE"
        else
            echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"failure_alert_failed\", \"message\": \"Failed to send failure alert to Telegram\"}" >> "$LOG_FILE"
        fi
    fi
fi

# Log completion
echo "{\"timestamp\": \"$(date '+%Y-%m-%d %H:%M:%S')\", \"event\": \"analysis_complete\", \"exit_code\": $EXIT_CODE, \"total_duration\": $DURATION}" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

# Exit with the Python script's exit code
exit $EXIT_CODE