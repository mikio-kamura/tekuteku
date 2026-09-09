#!/bin/bash
# 互換用：trigger.sh checkin と同じ
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/trigger.sh" checkin
