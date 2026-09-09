#!/bin/bash
# てくてくを外から操作する。起動中の app.py が NSDistributedNotificationCenter で受け取る。
#   trigger.sh checkin      今すぐチェックイン
#   trigger.sh edit-today   細分タスク編集を開く
# 送信時刻を添えるので、ダイアログ表示中に押した分は（閉じた後に配送されても）捨てられる。
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/.venv/bin/python"
[ -x "$PY" ] || PY=python3
case "${1:-checkin}" in
  checkin)    NAME="jp.tekuteku.checkin-now" ;;
  edit-today) NAME="jp.tekuteku.edit-today" ;;
  *) echo "usage: trigger.sh checkin|edit-today" >&2; exit 2 ;;
esac
exec "$PY" - "$NAME" <<'PYEOF'
import sys, time
from Foundation import NSDistributedNotificationCenter
NSDistributedNotificationCenter.defaultCenter().postNotificationName_object_userInfo_deliverImmediately_(
    sys.argv[1], None, {"t": time.time()}, True)
PYEOF
