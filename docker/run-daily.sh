#!/bin/sh
set -e
cd /app
# cron 잡 자체는 root로 도는데(crontab 주석 참고 - /proc/1/fd/1 리다이렉션 때문),
# 실제 수집/파싱/Drive 업로드는 su로 nonroot로 낮춰서 실행한다. 리다이렉션은
# 부모(root) 셸이 이미 열어놓은 fd를 su의 자식 프로세스가 그대로 물려받으므로
# (open 시점에만 권한 체크) nonroot로 내려도 로그는 정상적으로 계속 써진다.
exec su -s /bin/sh -c '/app/.venv/bin/python /app/cli.py today --days 7 --storage drive' nonroot
