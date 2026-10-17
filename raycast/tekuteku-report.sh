#!/bin/bash

# Raycast のスクリプトコマンド。てくてくに trigger.sh report を送る。

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title てくてく 日報をコピー
# @raycast.mode silent

# Optional parameters:
# @raycast.icon 📣
# @raycast.packageName てくてく

# Documentation:
# @raycast.description 今日のKPT・今日やったこと・次やること・困っていることをSlack用にコピーする
# @raycast.author mikio_kamura

"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/trigger.sh" report
