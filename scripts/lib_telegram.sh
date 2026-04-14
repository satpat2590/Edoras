#!/bin/bash
# Telegram message sending helper for Edoras scripts
# Usage: source lib_telegram.sh; send_telegram "message text"

TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-8724014451:AAGpisAWj86i8qmkOtfb4mCBSpiPfZd0ROI}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-1806720995}"

send_telegram() {
    local message="$1"
    local chat_id="${2:-$TELEGRAM_CHAT_ID}"
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$chat_id" \
        -d text="$message" \
        -d parse_mode="Markdown" \
        --connect-timeout 10 \
        --max-time 30 > /dev/null 2>&1
}
