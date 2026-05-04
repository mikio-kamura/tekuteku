#!/bin/bash
# Register てくてく as a login item via launchd
set -e

LABEL="com.user.tekuteku"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "❌ .venv が見つかりません。先に bash setup.sh を実行してください。"
    exit 1
fi

# Remove old progress-checker launchd entry if it exists
OLD_PLIST="$HOME/Library/LaunchAgents/com.user.progress-checker.plist"
if [ -f "$OLD_PLIST" ]; then
    launchctl unload "$OLD_PLIST" 2>/dev/null || true
    rm "$OLD_PLIST"
fi

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT_DIR/app.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/.tekuteku.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.tekuteku.log</string>
</dict>
</plist>
EOF

# Unload if already loaded, then load fresh
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "✅ ログイン時に自動起動するよう設定しました"
echo "   ログ: ~/.tekuteku.log"
echo ""
echo "解除したい場合:"
echo "  launchctl unload $PLIST && rm $PLIST"
