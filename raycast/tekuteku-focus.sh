#!/bin/bash

# Raycast のスクリプトコマンド。てくてくに trigger.sh focus を送る。

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title てくてく いま集中すること を編集
# @raycast.mode silent

# Optional parameters:
# @raycast.icon 🎯
# @raycast.packageName てくてく

# Documentation:
# @raycast.description Claude-Code の Task/いま集中すること.md の「いまの集中」節を編集する
# @raycast.author mikio_kamura

"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/trigger.sh" focus
