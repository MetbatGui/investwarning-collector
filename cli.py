import argparse
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from investment_hub.core.ports.storage_port import StoragePort
from investment_hub.infrastructure.adapters.sqlite_repository_adapter import SqliteRepositoryAdapter

# ─────────────────────────────────────────────────────────────────────────────
# 저장소 및 레포지토리 설정 헬퍼 (Composition Root)
# ─────────────────────────────────────────────────────────────────────────────


def _setup_di(args: argparse.Namespace) -> tuple[StoragePort, SqliteRepositoryAdapter]:
    """CLI 인자(args)에 따라 의존성을 가지는 저장소 어댑터와 레포지토리 객체를 생성 및 주입합니다.

    Args:
        args (argparse.Namespace): 사용자가 CLI를 통해 입력한 파라미터 묶음(Storage Type 등 포함).

    Returns:
        tuple[StoragePort, SqliteRepositoryAdapter]:
            - 선택된 스토리지 포트 구현체 (Local 혹은 Google Drive)
            - 데이터를 취급할 SQLite 레포지터리 어댑터 (SSOT, db_ssot_guide.md)
    """
    storage_type = getattr(args, "storage", "local")

    if storage_type == "drive":
        from investment_hub.infrastructure.adapters.google_drive_adapter import GoogleDriveAdapter

        token_file = getattr(args, "token_file", "secrets/token.json")
        client_secret = getattr(args, "client_secret", "secrets/client_secret.json")
        drive_folder = getattr(args, "drive_folder", "KRX_Auto_Crawling_Data")
        drive_folder_id = getattr(args, "drive_folder_id", None)
        logger.info(f"[설정] Google Drive 저장소 사용 (Token: {token_file})")
        storage: StoragePort = GoogleDriveAdapter(
            token_file=token_file,
            root_folder_name=drive_folder,
            root_folder_id=drive_folder_id,
            client_secret_file=client_secret,
        )
    else:
        from investment_hub.infrastructure.adapters.local_storage_adapter import LocalStorageAdapter

        logger.info("[설정] 로컬 저장소 사용")
        storage = LocalStorageAdapter()

    # Repository는 SQLite를 SSOT로 사용 (db_ssot_guide.md)
    repository = SqliteRepositoryAdapter(base_dir="output/db")
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
    # output_dir이 None이면 storage 타입에 따라 기본값 설정
    output_dir = args.output_dir if args.output_dir is not None else ("" if args.storage == "drive" else "output")

    target_date = args.date
    try:
        target_date = datetime.strptime(target_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        logger.error(f"[오류] 날짜 형식이 올바르지 않습니다: {target_date} (YYYY-MM-DD)")
        return 1

    if target_date > datetime.now().strftime("%Y-%m-%d"):
        logger.error(f"[오류] 미래 날짜는 수집할 수 없습니다: {target_date}")
        return 1

    from investment_hub.application.services import WarningCollectionService

    service = WarningCollectionService(repository=repository, storage=storage, output_dir=output_dir)
    result = service.collect_today(
        end_date=target_date,
        days=args.days,
        include_active=args.include_active,
    )
    logger.info(
        f"[결과] success={result.success} discovered={result.discovered} "
        f"new_stocks={result.new_stocks} new_price_rows={result.new_price_rows}"
        + (f" reason={result.reason}" if result.reason else "")
    )
    return 0 if result.success else 1


def _get_target_years(args: argparse.Namespace) -> list[int]:
    YEAR_RANGES = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
    years = [args.year] if args.year else list(range(args.start, args.end + 1))
    valid = [y for y in years if y in YEAR_RANGES]
    skipped = set(years) - set(valid)
    if skipped: logger.warning(f"[경고] 수집 불가 연도 제외: {sorted(skipped)}")
    return valid

def cmd_year(args: argparse.Namespace) -> int:
    """연도 범위 전체 데이터를 백필 수집합니다."""
    storage, repository = _setup_di(args)
    valid_years = _get_target_years(args)
    if not valid_years: return 1

    from investment_hub.application.services import WarningCollectionService
    output_dir = args.output_dir if args.output_dir is not None else ("" if args.storage == "drive" else "output")
    service = WarningCollectionService(repository=repository, storage=storage, output_dir=output_dir)

    logger.info(f"연도별 수집: {valid_years}")
    results = {
        y: service.collect_year(y, end_date=args.end_date, include_active=args.include_active)
        for y in valid_years
    }

    logger.info("최종 결과 요약")
    for y, ok in results.items(): logger.info(f"  {y}년: {'[완료]' if ok else '[실패]'}")

    # 하나라도 실패하면 exit code로 알려야 한다 - 예전엔 결과와 무관하게 항상 0을
    # 반환해 크론/스크립트가 실패를 감지할 수 없었다(docker_guide.md §10).
    return 0 if all(results.values()) else 1


def cmd_export_excel(args: argparse.Namespace) -> int:
    """Parquet 파일 데이터를 기반으로 Excel 뷰어 리포트를 단독 재생성합니다.

    Args:
        args (argparse.Namespace): 엑셀을 재작성할 `--year` 타겟 인자.

    Returns:
        int: 리포트 재생성 성공시 0, 에러시 1.
    """
    storage, repository = _setup_di(args)

    from investment_hub.application.services import ReportGenerationService

    output_dir = args.output_dir if args.output_dir is not None else ("" if args.storage == "drive" else "output")
    service = ReportGenerationService(repository=repository, storage=storage, output_dir=output_dir)
    year = args.year
    ok = service.generate_excel_report(year=year)
    return 0 if ok else 1


def _run_ps_script(script_path: Path, args: list[str]) -> int:
    res = subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script_path)] + args, capture_output=False)
    return res.returncode

def _show_scheduler_status(task_name: str) -> int:
    cmd = f"$t = Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue; " \
          f"if ($t) {{ Write-Host '[등록됨]' $t.TaskName $t.State }} " \
          f"else {{ Write-Host '[미등록] {task_name} 스케줄이 없습니다.' }}"
    return subprocess.run(["powershell", "-Command", cmd], capture_output=False).returncode

def cmd_scheduler(args: argparse.Namespace) -> int:
    """Windows 작업 스케줄러 관리 핸들러입니다."""
    task_name, ps1 = "InvestWarningCollector", Path(__file__).parent / "setup_scheduler.ps1"
    if not ps1.exists():
        logger.error(f"[오류] setup_scheduler.ps1 미존재: {ps1}")
        return 1

    if args.action == "install":
        logger.info(f"[스케줄러] '{task_name}' 등록 중...")
        return _run_ps_script(ps1, [])
    if args.action == "uninstall":
        logger.info(f"[스케줄러] '{task_name}' 등록 해제 중...")
        return _run_ps_script(ps1, ["-Uninstall"])
    if args.action == "status":
        return _show_scheduler_status(task_name)
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# CLI 파서 정의
# ─────────────────────────────────────────────────────────────────────────────


def _build_storage_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--storage", choices=["local", "drive"], default="local", help="저장 방식 선택")
    p.add_argument("--token-file", dest="token_file", default="secrets/token.json", help="Google Drive 토큰 경로")
    p.add_argument("--client-secret", dest="client_secret", default="secrets/client_secret.json", help="OAuth 비밀파일 경로")
    p.add_argument("--drive-folder", dest="drive_folder", default="KRX_Auto_Crawling_Data", help="Drive 루트 폴더명")
    p.add_argument("--drive-folder-id", dest="drive_folder_id", help="Drive 루트 폴더 ID (환경 변수보다 우선)")
    p.add_argument("--output-dir", dest="output_dir", help="출력 디렉토리 경로 (드라이브 사용 시 기본값은 루트)")
    return p

def _add_today_cmd(subparsers, common):
    p = subparsers.add_parser("today", parents=[common], help="최근 N일 수집", description="오늘 기준 최근 N일 수집")
    p.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="마지막 날짜 (YYYY-MM-DD)")
    p.add_argument("--days", type=int, default=1, help="수집 일수")
    p.add_argument("--exclude-active", dest="include_active", action="store_false", help="진행중 제외")
    p.set_defaults(include_active=True)
    p.set_defaults(func=cmd_today)

def _add_year_cmd(subparsers, common):
    p = subparsers.add_parser("year", parents=[common], help="연도 범위 수집", description="연도별 백필 수집")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--year", type=int, help="특정 연도")
    g.add_argument("--start", type=int, default=2020, help="시작 연도")
    p.add_argument("--end", type=int, default=datetime.now().year, help="종료 연도")
    p.add_argument("--end-date", dest="end_date", help="데이터 수집 종료 상한일 (YYYY-MM-DD)")
    p.add_argument("--exclude-active", dest="include_active", action="store_false", help="진행중 제외")
    p.set_defaults(include_active=True)
    p.set_defaults(func=cmd_year)

def _add_export_cmd(subparsers, common):
    p = subparsers.add_parser("export-excel", parents=[common], help="Excel 리포트 재생성")
    p.add_argument("--year", type=int, required=True, help="타겟 연도")
    p.set_defaults(func=cmd_export_excel)

def _add_scheduler_cmd(subparsers):
    p = subparsers.add_parser("scheduler", help="Windows 작업 스케줄러 관리")
    p.add_argument("action", choices=["install", "uninstall", "status"], help="install/uninstall/status")
    p.set_defaults(func=cmd_scheduler)

def build_parser() -> argparse.ArgumentParser:
    """CLI 구조 파서를 조립하고 반환합니다."""
    parser = argparse.ArgumentParser(prog="cli", description="투자경고종목 수집기")
    common = _build_storage_parser()
    subparsers = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    _add_today_cmd(subparsers, common)
    _add_year_cmd(subparsers, common)
    _add_export_cmd(subparsers, common)
    _add_scheduler_cmd(subparsers)
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
