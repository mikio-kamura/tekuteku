#!/bin/bash

# Raycast のスクリプトコマンド。てくてくに「今すぐチェックイン」を起こす。
# Raycast → Settings → Extensions → 「+」→ Add Script Directory でこのフォルダ（raycast/）を追加し、
# 「てくてく チェックイン」にホットキーを割り当てる。

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title てくてく チェックイン
# @raycast.mode silent

# Optional parameters:
# @raycast.icon 🔄
# @raycast.packageName てくてく

# Documentation:
# @raycast.description 今すぐチェックインの画面を開く
# @raycast.author mikio_kamura

"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/trigger.sh" checkin
