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
# @raycast.description KPT画面に書いた今日の日報を、そのままSlackに貼れる形でクリップボードへ
# @raycast.author mikio_kamura

"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/trigger.sh" report
