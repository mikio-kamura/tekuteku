#!/bin/bash

# Raycast のスクリプトコマンド。てくてくに trigger.sh report を送る。

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title てくてく 日報（KPT）を編集
# @raycast.mode silent

# Optional parameters:
# @raycast.icon 📝
# @raycast.packageName てくてく

# Documentation:
# @raycast.description 今日の日報（KPT・今日やったこと・次やること・困っていること）を書く画面を開く
# @raycast.author mikio_kamura

"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/trigger.sh" kpt
