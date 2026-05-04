#!/bin/bash
# てくてく — initial setup
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "=== てくてく セットアップ ==="
echo ""

# Python 3 check
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 が見つかりません。https://python.org からインストールしてください。"
    exit 1
fi
echo "✅ $(python3 --version)"

# Create venv if needed
if [ ! -d ".venv" ]; then
    echo "🔧 仮想環境を作成中..."
    python3 -m venv .venv
fi

# Install dependencies
echo "📦 rumps をインストール中..."
.venv/bin/pip install --quiet -r requirements.txt
echo "✅ rumps インストール完了"

echo ""
echo "準備完了！以下のコマンドで起動できます:"
echo ""
echo "  .venv/bin/python app.py"
echo ""
echo "ログイン時に自動起動したい場合:"
echo ""
echo "  bash autostart.sh"
echo ""
