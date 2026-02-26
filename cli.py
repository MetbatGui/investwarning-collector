import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from investment_hub.core.ports.storage_port import StoragePort
from investment_hub.infrastructure.adapters.parquet_repository_adapter import ParquetRepositoryAdapter

# ─────────────────────────────────────────────────────────────────────────────
# 저장소 및 레포지토리 설정 헬퍼 (Composition Root)
# ─────────────────────────────────────────────────────────────────────────────

def _setup_di(args: argparse.Namespace) -> tuple[StoragePort, ParquetRepositoryAdapter]:
    """CLI 인자(args)에 따라 의존성을 가지는 저장소 어댑터와 레포지토리 객체를 생성 및 주입합니다.

    Args:
        args (argparse.Namespace): 사용자가 CLI를 통해 입력한 파라미터 묶음(Storage Type 등 포함).

    Returns:
        tuple[StoragePort, ParquetRepositoryAdapter]:
            - 선택된 스토리지 포트 구현체 (Local 혹은 Google Drive)
            - 데이터를 취급할 Parquet 레포지터리 어댑터
    """
    storage_type = getattr(args, "storage", "local")

    if storage_type == "drive":
        from investment_hub.infrastructure.adapters.google_drive_adapter import GoogleDriveAdapter
        token_file = getattr(args, "token_file", "secrets/token.json")
        client_secret = getattr(args, "client_secret", "secrets/client_secret.json")
        drive_folder = getattr(args, "drive_folder", "KRX_Auto_Crawling_Data")
        print(f"[설정] Google Drive 저장소 사용 (Token: {token_file})")
        storage: StoragePort = GoogleDriveAdapter(
            token_file=token_file,
            root_folder_name=drive_folder,
            client_secret_file=client_secret,
        )
    else:
        from investment_hub.infrastructure.adapters.local_storage_adapter import LocalStorageAdapter
        print("[설정] 로컬 저장소 사용")
        storage = LocalStorageAdapter()

    # Repository는 Parquet을 기본으로 사용
    repository = ParquetRepositoryAdapter(base_dir="output/parquet")
    return storage, repository


# ─────────────────────────────────────────────────────────────────────────────
# 서브커맨드 핸들러
# ─────────────────────────────────────────────────────────────────────────────


def cmd_today(args: argparse.Namespace) -> int:
    """오늘(또는 지정 날짜) 기준으로 가장 최근 영업일의 데이터를 증분 수집합니다.

    Args:
        args (argparse.Namespace): `--date`, `--days`, `--include-active` 등의 인자.

    Returns:
        int: 정상 처리 시 0 (성공), 오류 및 비정상 종료 시 1.
    """
    storage, repository = _setup_di(args)

    target_date = args.date
    try:
        target_date = datetime.strptime(target_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        print(f"[오류] 날짜 형식이 올바르지 않습니다: {target_date} (YYYY-MM-DD)")
        return 1

    if target_date > datetime.now().strftime("%Y-%m-%d"):
        print(f"[오류] 미래 날짜는 수집할 수 없습니다: {target_date}")
        return 1

    from investment_hub.application.services import WarningCollectionService

    service = WarningCollectionService(repository=repository, storage=storage)
    ok = service.collect_today(
        end_date=target_date,
        days=args.days,
        include_active=args.include_active,
    )
    return 0 if ok else 1


def cmd_year(args: argparse.Namespace) -> int:
    """단일 연도 혹여 연속된 연도 범위 전체의 데이터를 한 번에(백필) 수집합니다.

    Args:
        args (argparse.Namespace): `--year` 단일 연도 혹은 `--start`, `--end` 범위 지정 인자.

    Returns:
        int: 최소 하나 이상 수집 대상이 올바를 때 0을 리턴. 인자 에러 시 1 반환.
    """
    storage, repository = _setup_di(args)

    # 임시 하드코딩
    YEAR_RANGES = [2020, 2021, 2022, 2023, 2024, 2025, 2026]

    if args.year:
        years = [args.year]
    else:
        years = list(range(args.start, args.end + 1))

    valid_years = [y for y in years if y in YEAR_RANGES]
    skipped = set(years) - set(valid_years)
    if skipped:
        print(f"[경고] 수집 불가 연도 제외: {sorted(skipped)}")
    if not valid_years:
        print("[오류] 수집할 연도가 없습니다.")
        return 1

    print(f"\n{'=' * 60}")
    print(f"  연도별 데이터 수집: {valid_years}")
    print(f"{'=' * 60}")

    from investment_hub.application.services import WarningCollectionService

    service = WarningCollectionService(repository=repository, storage=storage)
    results = {}
    for year in valid_years:
        ok = service.collect_year(year, include_active=args.include_active)
        results[year] = "[완료]" if ok else "[실패/데이터없음]"

    print(f"\n{'=' * 60}")
    print("  최종 결과 요약")
    print(f"{'=' * 60}")
    for year, status in results.items():
        print(f"  {year}년: {status}")

    return 0


def cmd_export_excel(args: argparse.Namespace) -> int:
    """Parquet 파일 데이터를 기반으로 Excel 뷰어 리포트를 단독 재생성합니다.

    Args:
        args (argparse.Namespace): 엑셀을 재작성할 `--year` 타겟 인자.

    Returns:
        int: 리포트 재생성 성공시 0, 에러시 1.
    """
    storage, repository = _setup_di(args)

    from investment_hub.application.services import ReportGenerationService

    service = ReportGenerationService(repository=repository, storage=storage)
    year = args.year
    ok = service.generate_excel_report(year=year)
    return 0 if ok else 1


def cmd_scheduler(args: argparse.Namespace) -> int:
    """Windows 작업 스케줄러를 등록하거나 제거/상태확인을 보조합니다.

    Args:
        args (argparse.Namespace): `install`, `uninstall`, `status` 여부를 담은 action 인자.

    Returns:
        int: powershell 스크립트의 서브프로세스 종료 반환코드.
    """
    task_name = "InvestWarningCollector"
    ps1 = Path(__file__).parent / "setup_scheduler.ps1"

    if not ps1.exists():
        print(f"[오류] setup_scheduler.ps1 파일을 찾을 수 없습니다: {ps1}")
        return 1

    action = args.action

    if action == "install":
        print(f"[스케줄러] '{task_name}' 등록 중 (매일 15:50)...")
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
            [
                "powershell",
                "-Command",
                f"$t = Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue; "
                f"if ($t) {{ Write-Host '[등록됨]' $t.TaskName $t.State }} "
                f"else {{ Write-Host '[미등록] {task_name} 스케줄이 없습니다.' }}",
            ],
            capture_output=False,
        )
        return result.returncode

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# CLI 파서 정의
# ─────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """애플리케이션 전반의 CLI 인수(Argument) 구조 파서를 조립하고 반환합니다.

    Returns:
        argparse.ArgumentParser: 명령어 라우팅용 서브명령어가 정의된 파서 인스턴스.
    """
    parser = argparse.ArgumentParser(
        prog="cli",
        description="투자경고종목 수집기",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
커맨드:
  today      오늘(또는 지정 날짜) 시세를 증분 수집합니다.
  year       연도 범위 전체를 수집합니다.
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
        "--storage",
        choices=["local", "drive"],
        default="local",
        help="저장 방식 선택 (local: 로컬 파일, drive: 구글 드라이브)",
    )
    storage_parser.add_argument(
        "--token-file", dest="token_file", default="secrets/token.json", help="Google Drive 전용: 인증 토큰 파일 경로"
    )
    storage_parser.add_argument(
        "--client-secret",
        dest="client_secret",
        default="secrets/client_secret.json",
        help="Google Drive 전용: 클라이언트 비밀 파일 경로",
    )
    storage_parser.add_argument(
        "--drive-folder",
        dest="drive_folder",
        default="KRX_Auto_Crawling_Data",
        help="Google Drive 전용: 루트 폴더 이름",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers.required = True

    # ── today ─────────────────────────────────────────────────────────────────
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
        "--days",
        type=int,
        default=1,
        metavar="N",
        help="수집할 최근 일수 (기본: 1)",
    )
    p_today.add_argument(
        "--include-active",
        dest="include_active",
        action="store_true",
        help="해제일 없는 진행 중 종목도 포함",
    )
    p_today.set_defaults(func=cmd_today)

    # ── year ──────────────────────────────────────────────────────────────────
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
        "--include-active",
        dest="include_active",
        action="store_true",
        help="해제일 없는 진행 중 종목도 포함",
    )
    p_year.set_defaults(func=cmd_year)

    # ── export-excel ──────────────────────────────────────────────────────────
    p_export = subparsers.add_parser(
        "export-excel",
        parents=[storage_parser],
        help="Excel 리포트 재생성",
        description="Parquet 데이터에서 Excel 리포트를 다시 생성합니다.",
    )
    p_export.add_argument("--year", type=int, required=True, help="재생성할 연도")
    p_export.set_defaults(func=cmd_export_excel)

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
    """스크립트 호출 시 메인 진입지점으로, 명령어를 파싱하고 적절한 커맨드 핸들러로 위임합니다."""
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
