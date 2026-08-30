#!/bin/sh
set -e
cd /app
# cron 잡 자체는 root로 도는데(crontab 주석 참고 - /proc/1/fd/1 리다이렉션 때문),
# 실제 수집/파싱/Drive 업로드는 su로 nonroot로 낮춰서 실행한다. 리다이렉션은
# 부모(root) 셸이 이미 열어놓은 fd를 su의 자식 프로세스가 그대로 물려받으므로
# (open 시점에만 권한 체크) nonroot로 내려도 로그는 정상적으로 계속 써진다.
# days=30: 컨테이너가 최대 1달 멈췄다 재기동해도 누락 영업일을 자동 복구하기 위함.
# missing_dates 필터링 덕에 실제로 빠진 날짜만 재요청하므로 평소엔 비용 증가 없음
# (db_ssot_guide.md §9 "원격 DB 커버리지" - 자동 백필 창을 넓혀 갭을 방지).
exec su -s /bin/sh -c '/app/.venv/bin/python /app/cli.py today --days 30 --storage drive' nonroot
