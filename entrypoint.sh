#!/bin/bash
# Entrypoint for Hugging Face Spaces with persistent storage
# Persistent storage is mounted at /data/ when enabled in Space Settings

PERSISTENT_DIR="/data"
APP_DATA_DIR="/app/data"

# If persistent storage is available (mounted by HF Spaces)
if [ -d "$PERSISTENT_DIR" ] && mount | grep -q "$PERSISTENT_DIR"; then
    echo "✅ Persistent storage detected at $PERSISTENT_DIR"

    # Copy initial DB to persistent storage if it doesn't exist yet
    if [ ! -f "$PERSISTENT_DIR/military.db" ]; then
        if [ -f "$APP_DATA_DIR/military.db" ]; then
            echo "📦 Copying initial database to persistent storage..."
            cp "$APP_DATA_DIR/military.db" "$PERSISTENT_DIR/military.db"
        fi
        # Create backups dir in persistent storage
        mkdir -p "$PERSISTENT_DIR/backups"
    fi

    # Symlink app/data -> persistent storage so app reads/writes there
    rm -rf "$APP_DATA_DIR"
    ln -sf "$PERSISTENT_DIR" "$APP_DATA_DIR"
    echo "🔗 Linked $APP_DATA_DIR -> $PERSISTENT_DIR"
else
    echo "⚠️  No persistent storage. Data will be lost on restart."
    echo "   Enable it in Space Settings -> Persistent Storage"
fi

echo "🚀 Starting Streamlit..."
exec streamlit run app.py \
    --server.port=7860 \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
