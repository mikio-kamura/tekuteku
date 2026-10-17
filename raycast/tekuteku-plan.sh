#!/bin/bash

# Raycast のスクリプトコマンド。てくてくに trigger.sh plan を送る。

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title てくてく 手順書を編集
# @raycast.mode silent

# Optional parameters:
# @raycast.icon 🧭
# @raycast.packageName てくてく

# Documentation:
# @raycast.description Slackに貼る手順書（次回までに何をやるか）を編集・コピーする
# @raycast.author mikio_kamura

"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/trigger.sh" focus
