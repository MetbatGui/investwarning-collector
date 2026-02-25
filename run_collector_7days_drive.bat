@echo off
:: 투자경고종목 수집기 - 최근 7일 수집 및 구글 드라이브 업로드 전용 배치 파일

:: 프로젝트 디렉토리로 이동
cd /d "%~dp0"

echo [START] %date% %time% - 투자경고종목 최근 7일 수집 시작 (Google Drive)

:: 수집기 실행 (7일치, 구글 드라이브 저장)
uv run python cli.py today --days 7 --storage drive

if %ERRORLEVEL% equ 0 (
    echo [SUCCESS] %date% %time% - 수집 및 업로드 완료
) else (
    echo [ERROR] %date% %time% - 수집 과정 중 오류 발생
)

:: 로그를 위해 잠시 대기 (필요시 주석 해제)
:: pause
