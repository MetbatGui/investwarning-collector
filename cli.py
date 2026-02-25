import argparse
import os
import subprocess
import sys
import logging
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# 의존성 주입 및 서비스 초기화 (Composition Root)
# ─────────────────────────────────────────────────────────────────────────────

def _get_collection_service(args: argparse.Namespace):
    """설정 인자에 따라 어댑터들을 생성하고 CollectionService를 구성합니다."""
    from investment_hub.infrastructure.adapters.local_storage_adapter import LocalStorageAdapter
    from investment_hub.infrastructure.adapters.investment_warning_repository_adapter import CSVInvestmentWarningRepositoryAdapter
    from investment_hub.infrastructure.adapters.krx_market_data_adapter import KrxMarketDataAdapter
    from investment_hub.infrastructure.adapters.krx_warning_adapter import KrxWarningAdapter
    from investment_hub.application.collection_service import CollectionService

    # 1. 스토리지 어댑터 선택
    storage_type = getattr(args, "storage", "local")
    if storage_type == "drive":
        from investment_hub.infrastructure.adapters.google_drive_adapter import GoogleDriveAdapter
        print(f"[설정] Google Drive 저장소 사용 (Token: {args.token_file})")
        storage = GoogleDriveAdapter(
            token_file=args.token_file,
            root_folder_name=args.drive_folder,
            client_secret_file=args.client_secret
        )
    else:
        storage = LocalStorageAdapter()
        print("[설정] 로컬 저장소 사용")

    # 2. 도메인 포트 구현체(어댑터) 생성
    repository = CSVInvestmentWarningRepositoryAdapter(storage)
    market_data = KrxMarketDataAdapter()
    warning_info = KrxWarningAdapter()

    # 3. 애플리케이션 서비스 구성
    return CollectionService(
        repository=repository,
        market_data_port=market_data,
        warning_port=warning_info
    )


# ─────────────────────────────────────────────────────────────────────────────
# 서브커맨드 핸들러
# ─────────────────────────────────────────────────────────────────────────────

def cmd_today(args: argparse.Namespace) -> int:
    """오늘(또는 지정 날짜) 증분 수집"""
    service = _get_collection_service(args)
    
    target_date = args.date
    try:
        target_date = datetime.strptime(target_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        print(f"[오류] 날짜 형식이 올바르지 않습니다: {target_date} (YYYY-MM-DD)")
        return 1

    ok = service.collect_today_incremental(
        target_date, 
        days=args.days, 
        include_active=args.include_active
    )
    return 0 if ok else 1


def cmd_year(args: argparse.Namespace) -> int:
    """연도 범위 수집"""
    service = _get_collection_service(args)
    from collect_yearly_warnings import YEAR_RANGES

    if args.year:
        years = [args.year]
    else:
        years = list(range(args.start, args.end + 1))

    valid_years = [y for y in years if y in YEAR_RANGES]
    if not valid_years:
        print("[오류] 수집할 연도가 없습니다.")
        return 1

    print(f"\n{'='*60}")
    print(f"  연도별 데이터 수집/갱신: {valid_years}")
    print(f"{'='*60}")

    for year in valid_years:
        print(f"\n--- {year}년 데이터 처리 중 ---")
        
        # 1. 기존 데이터가 있다면 먼저 정제 (유저가 보고한 중복 문제 해결)
        service.reprocess_year_data(year)
        
        # 2. 필요시 신규 수집 (기존 legacy 로직 활용)
        from collect_yearly_warnings import set_storage, collect_year
        set_storage(service.repository.storage) 
        collect_year(year, include_active=args.include_active)
        
        # 3. 수집 완료 후 최종 정제 및 저장 (정합성 보장)
        service.reprocess_year_data(year)

    return 0


def cmd_scheduler(args: argparse.Namespace) -> int:
    """Windows 작업 스케줄러 관리"""
    task_name = "InvestWarningCollector"
    ps1 = Path(__file__).parent / "setup_scheduler.ps1"

    if not ps1.exists():
        print(f"[오류] setup_scheduler.ps1 파일을 찾을 수 없습니다: {ps1}")
        return 1

    action = args.action

    if action == "install":
        print(f"[스케줄러] '{task_name}' 등록 중 (매일 15:50)...")
        # 스케줄 등록 시 현재 인자들을 그대로 전달하게 하려면 ps1 수정이 필요할 수 있으나,
        # 기본적으로는 `uv run cli.py today`만 등록됨.
        result = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
            capture_output=False,
        )
        return result.returncode

    elif action == "uninstall":
        print(f"[스케줄러] '{task_name}' 등록 해제 중...")
        result = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-Uninstall"],
            capture_output=False,
        )
        return result.returncode

    elif action == "status":
        result = subprocess.run(
            ["powershell", "-Command",
             f"$t = Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue; "
             f"if ($t) {{ Write-Host '[등록됨]' $t.TaskName $t.State }} "
             f"else {{ Write-Host '[미등록] {task_name} 스케줄이 없습니다.' }}"],
            capture_output=False,
        )
        return result.returncode

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# CLI 파서 정의
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli",
        description="투자경고종목 수집기",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
커맨드:
  today      오늘(또는 지정 날짜) 시세를 증분 수집합니다.
  year       연도 범위 전체를 수집·재생성합니다.
  scheduler  Windows 작업 스케줄러를 관리합니다.

예시:
  uv run python cli.py today
  uv run python cli.py today --storage drive
  uv run python cli.py year --year 2025
  uv run python cli.py scheduler install
""",
    )

    # 공통 저장소 인자 부모 파서
    storage_parser = argparse.ArgumentParser(add_help=False)
    storage_parser.add_argument(
        "--storage", choices=["local", "drive"], default="local",
        help="저장 방식 선택 (local: 로컬 파일, drive: 구글 드라이브)"
    )
    storage_parser.add_argument(
        "--token-file", default="secrets/token.json",
        help="Google Drive 전용: 인증 토큰 파일 경로 (기본: secrets/token.json)"
    )
    storage_parser.add_argument(
        "--client-secret", default="secrets/client_secret.json",
        help="Google Drive 전용: 클라이언트 비밀 파일 경로"
    )
    storage_parser.add_argument(
        "--drive-folder", default="KRX_Auto_Crawling_Data",
        help="Google Drive 전용: 루트 폴더 이름 (기본: KRX_Auto_Crawling_Data)"
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers.required = True

    # ── today ────────────────────────────────────────────────────────────────
    p_today = subparsers.add_parser(
        "today",
        parents=[storage_parser],
        help="최근 N일 수집",
        description="오늘 기준으로 최근 N일간 투자경고종목 시세를 수집·갱신합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_today.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        metavar="YYYY-MM-DD",
        help="수집 마지막 날짜 (기본: 오늘)",
    )
    p_today.add_argument(
        "--days", type=int, default=1, metavar="N",
        help="수집할 최근 일수 (기본: 1)",
    )
    p_today.add_argument(
        "--include-active", action="store_true",
        help="해제일 없는 진행 중 종목도 포함",
    )
    p_today.set_defaults(func=cmd_today)

    # ── year ─────────────────────────────────────────────────────────────────
    p_year = subparsers.add_parser(
        "year",
        parents=[storage_parser],
        help="연도 범위 수집",
        description="연도 범위 전체 투자경고종목을 수집합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = p_year.add_mutually_exclusive_group()
    group.add_argument("--year", type=int, metavar="YYYY", help="특정 연도만 수집")
    group.add_argument("--start", type=int, default=2020, metavar="YYYY", help="시작 연도 (기본: 2020)")
    p_year.add_argument("--end", type=int, default=datetime.now().year, metavar="YYYY", help="종료 연도 (기본: 올해)")
    p_year.add_argument(
        "--include-active", action="store_true",
        help="해제일 없는 진행 중 종목도 포함",
    )
    p_year.set_defaults(func=cmd_year)

    # ── scheduler ─────────────────────────────────────────────────────────────
    p_sched = subparsers.add_parser(
        "scheduler",
        help="Windows 작업 스케줄러 관리",
        description="매일 15:50 자동 실행 스케줄을 관리합니다.",
    )
    p_sched.add_argument(
        "action",
        choices=["install", "uninstall", "status"],
        help="install: 등록 / uninstall: 해제 / status: 확인",
    )
    p_sched.set_defaults(func=cmd_scheduler)

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
