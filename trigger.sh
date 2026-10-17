#!/bin/bash
# てくてくを外から操作する。起動中の app.py が NSDistributedNotificationCenter で受け取る。
#   trigger.sh checkin      今すぐチェックイン
#   trigger.sh edit-today   細分タスク編集を開く
#   trigger.sh suggest      Claude に今日の細分タスクを提案させる（5〜10分の粒度。受信箱経由で足される）
#   trigger.sh focus        「いま集中すること」(md) を編集する
#   trigger.sh reload-inbox 受信箱 ~/.tekuteku.inbox.json を取り込ませる（suggest_tasks.py が使う）
#   trigger.sh plan         手順書（Slackに貼る「次回までに何をやるか」）を編集する
#   trigger.sh suggest-plan Claude に手順書を提案させる（Slackのやりとり＋mdを読む。受信箱経由で届く）
#   trigger.sh report       今日の日報（KPT画面に書いたもの）をSlack用にそのままクリップボードへ
#   trigger.sh kpt          今日の日報（KPT画面）を開く
# 送信時刻を添えるので、ダイアログ表示中に押した分は（閉じた後に配送されても）捨てられる。
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/.venv/bin/python"
[ -x "$PY" ] || PY=python3
case "${1:-checkin}" in
  checkin)    NAME="jp.tekuteku.checkin-now" ;;
  edit-today) NAME="jp.tekuteku.edit-today" ;;
  suggest)    NAME="jp.tekuteku.suggest" ;;
  focus)      NAME="jp.tekuteku.edit-focus" ;;
  reload-inbox) NAME="jp.tekuteku.reload-inbox" ;;
  plan)         NAME="jp.tekuteku.edit-plan" ;;
  suggest-plan) NAME="jp.tekuteku.suggest-plan" ;;
  report)       NAME="jp.tekuteku.copy-report" ;;
  kpt)          NAME="jp.tekuteku.edit-kpt" ;;
  *) echo "usage: trigger.sh checkin|edit-today|suggest|focus|reload-inbox|plan|suggest-plan|report|kpt" >&2; exit 2 ;;
esac
exec "$PY" - "$NAME" <<'PYEOF'
import sys, time
from Foundation import NSDistributedNotificationCenter
NSDistributedNotificationCenter.defaultCenter().postNotificationName_object_userInfo_deliverImmediately_(
    sys.argv[1], None, {"t": time.time()}, True)
PYEOF
