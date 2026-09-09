#!/bin/bash

# Raycast のスクリプトコマンド。てくてくの「細分タスク編集」を開く。
# チェックイン画面が開いていればその中の 📝 ボタンと同じ動き、開いていなければ直接エディタが出る。

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title てくてく 細分タスク編集
# @raycast.mode silent

# Optional parameters:
# @raycast.icon 📝
# @raycast.packageName てくてく

# Documentation:
# @raycast.description 今日の細分タスク（3層ツリー）を編集する
# @raycast.author mikio_kamura

"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/trigger.sh" edit-today
