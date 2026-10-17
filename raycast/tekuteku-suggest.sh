#!/bin/bash

# Raycast のスクリプトコマンド。てくてくに trigger.sh suggest を送る。

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title てくてく 細分タスクを提案
# @raycast.mode silent

# Optional parameters:
# @raycast.icon 🤖
# @raycast.packageName てくてく

# Documentation:
# @raycast.description Claude に今日の細分タスク（5〜10分の粒度）を提案させて今日の目標に足す
# @raycast.author mikio_kamura

"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/trigger.sh" suggest
