#!/bin/sh
set -e
# cli.py가 load_dotenv()로 /app/.env 파일을 직접 읽어들이므로(OS 환경변수 주입에
# 의존하지 않음), cron 잡이 별도로 환경변수를 상속받을 필요가 없다. env 덤프 없이
# 바로 cron만 기동한다.
exec cron -f
