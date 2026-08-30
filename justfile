# Use PowerShell on Windows
set shell := ["powershell", "-c"]

# 도커 이미지 빌드
build:
    docker compose -f docker/docker-compose.yml build

# 컨테이너 내장 cron 서비스를 백그라운드로 기동 (스케줄: docker/crontab)
cron-up:
    docker compose -f docker/docker-compose.yml up -d --build investwarning-collector-cron

setup-release:
    git checkout master
    git remote add employers-investwarning-collector https://github.com/guruta71/investwarning-collector.git

# Release to employers-investwarning-collector
# Usage: just release
release:
    git checkout -B release master
    git push -u employers-investwarning-collector release:main
    git checkout master
