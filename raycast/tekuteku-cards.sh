#!/bin/bash

# Raycast のスクリプトコマンド。てくてくに trigger.sh cards を送る。

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title てくてく 学びカードを振り返る
# @raycast.mode silent

# Optional parameters:
# @raycast.icon 📚
# @raycast.packageName てくてく

# Documentation:
# @raycast.description 今日の学びカード（Scrapboxの学びを問いにしたもの）を1枚ずつ振り返る
# @raycast.author mikio_kamura

"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/trigger.sh" cards
