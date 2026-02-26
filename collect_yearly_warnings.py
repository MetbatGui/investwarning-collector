"""
투자경고종목 연도별 분리 수집 스크립트

2020~2026년(또는 지정 연도)의 투자경고종목을 연도별로 수집하여
output/ 디렉토리에 CSV 및 Excel 파일로 저장합니다.

사용법:
  uv run python collect_yearly_warnings.py           # 2020~2026 전체
  uv run python collect_yearly_warnings.py --year 2023         # 특정 연도만
  uv run python collect_yearly_warnings.py --start 2022 --end 2024  # 범위 지정
"""

import argparse
import os
from datetime import datetime

import pandas as pd

from investment_hub.core.ports.storage_port import StoragePort
from investment_hub.infrastructure.adapters.local_storage_adapter import LocalStorageAdapter
from investment_hub.infrastructure.adapters.parquet_repository_adapter import ParquetRepositoryAdapter
from investment_hub.infrastructure.collectors.daily_price_collector import collect_daily_prices_batch
from investment_hub.infrastructure.scrapers.krx_warning_scraper import fetch_investment_warning_stocks
from investment_hub.visualization.excel_exporter import WarningExcelExporter

# ─────────────────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────────────────
OUTPUT_DIR = "output"
PARQUET_DIR = "output/parquet"
MAX_WARNING_DAYS = 60  # 60일 이하 경고 종목만 수집
TRADING_DAYS_AFTER_RELEASE = 3  # 해제일 이후 추가 수집일

# 기본 저장소로 LocalStorageAdapter 사용
_storage: StoragePort = LocalStorageAdapter()

# 기본 레포지터리로 ParquetRepositoryAdapter 사용
_repository: ParquetRepositoryAdapter = ParquetRepositoryAdapter(PARQUET_DIR)


def set_storage(storage_adapter: StoragePort):
    global _storage
    _storage = storage_adapter


def get_storage() -> StoragePort:
    return _storage


def set_repository(repo: ParquetRepositoryAdapter) -> None:
    """전역 레포지터리 어댑터를 교체합니다 (테스트·CLI 용도)."""
    global _repository
    _repository = repo


def get_repository() -> ParquetRepositoryAdapter:
    """현재 사용 중인 레포지터리 어댑터를 반환합니다."""
    return _repository


# ─────────────────────────────────────────────────────────────────────────────
# 연도별 날짜 범위 (현재 연도는 오늘 날짜 자동 반영)
# ─────────────────────────────────────────────────────────────────────────────
def _build_year_ranges() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    current_year = datetime.now().year
    ranges = {}
    for y in range(2020, current_year):
        ranges[y] = (f"{y}-01-01", f"{y}-12-31")
    ranges[current_year] = (f"{current_year}-01-01", today)
    return ranges


YEAR_RANGES = _build_year_ranges()


# ─────────────────────────────────────────────────────────────────────────────
# 핵심 함수
# ─────────────────────────────────────────────────────────────────────────────


def collect_year(year: int, include_active: bool = False) -> bool:
    """
    특정 연도의 투자경고종목을 수집하고 CSV + Excel로 저장합니다.

    Args:
        year: 수집 연도
        include_active: True이면 아직 해제되지 않은 진행 중 종목도 포함

    Returns True if successful, False otherwise.
    """
    if year not in YEAR_RANGES:
        print(f"  [오류] {year}년은 지원하지 않는 연도입니다.")
        return False

    start_date, end_date = YEAR_RANGES[year]
    print(f"\n{'=' * 60}")
    print(f"  {year}년 투자경고종목 수집 시작")
    print(f"  기간: {start_date} ~ {end_date}")
    print(f"  진행 중 종목 포함: {'예' if include_active else '아니오'}")
    print(f"{'=' * 60}")

    # ── Step 1: 투자경고 지정 목록 수집 ──────────────────────────────────────
    print("\n[1/4] KRX KIND에서 투자경고 목록 수집 중...")
    stock_list = fetch_investment_warning_stocks(start_date, end_date)
    print(f"  → 총 {len(stock_list)}건 수집")

    if not stock_list:
        print("  데이터 없음. 건너뜀.")
        return False

    # ── Step 2: 필터링 ────────────────────────────────────────────────────────
    print(f"\n[2/4] 필터링 (코넥스 제외, {MAX_WARNING_DAYS}일 이하)...")
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)

    filtered = []
    for s in stock_list:
        if s.designation_date is None:
            continue
        if not (start_dt <= s.designation_date <= end_dt):
            continue
        if s.market == "코넥스":
            continue
        # 해제된 종목: 경고일 60일 이하만
        if s.release_date is not None:
            if (s.release_date - s.designation_date).days > MAX_WARNING_DAYS:
                continue
        else:
            # 진행 중 종목: include_active 플래그가 있어야 포함
            if not include_active:
                continue
        filtered.append(s)

    print(f"  → 필터 후 {len(filtered)}건")

    if not filtered:
        print("  필터 후 데이터 없음. 건너뜀.")
        return False

    # ── Step 3: 일별 가격 수집 ───────────────────────────────────────────────
    print("\n[3/4] 일별 가격 데이터 수집 (market_daily 캐시 활용)...")
    daily_prices_by_code = collect_daily_prices_batch(
        filtered,
        trading_days_after_release=TRADING_DAYS_AFTER_RELEASE,
    )

    # ── Step 4: 저장 ─────────────────────────────────────────────────────────
    print("\n[4/4] 결과 저장 (Parquet + Excel)...")
    storage = get_storage()
    storage.ensure_directory(OUTPUT_DIR)

    # 4-a. Parquet 저장 (원시 데이터 보존)
    repo = get_repository()
    repo.save_year(year, filtered, daily_prices_by_code)
    print(f"  [Parquet] {PARQUET_DIR}/{year}.parquet 저장 완료")

    # 4-b. CSV 저장 (하위 호환용)
    _save_csv(year, filtered, daily_prices_by_code, storage)

    # 4-c. Excel 출력 (Parquet에서 로드하여 재생성)
    reloaded_stocks, reloaded_prices = repo.load_year(year)

    exporter = WarningExcelExporter(trading_days_after_release=TRADING_DAYS_AFTER_RELEASE)
    wb = exporter.export(year, reloaded_stocks, reloaded_prices)

    xlsx_path = os.path.join(OUTPUT_DIR, f"투자경고종목분석({year}년).xlsx")
    storage.save_workbook(wb, xlsx_path)

    print(f"\n  [완료] {year}년 수집 완료")
    return True


def _save_csv(year: int, filtered: list, daily_prices_by_code: dict, storage: StoragePort):
    """연도별 상세 CSV 저장 (long format)."""
    records = []
    for stock_info in filtered:
        code = stock_info.code
        prices = daily_prices_by_code.get(code, [])
        for dp in sorted(prices, key=lambda x: x.date):
            records.append(
                {
                    "year": year,
                    "code": code,
                    "name": stock_info.name,
                    "market": stock_info.market,
                    "designation_date": stock_info.designation_date.strftime("%Y-%m-%d")
                    if stock_info.designation_date
                    else "",
                    "release_date": stock_info.release_date.strftime("%Y-%m-%d") if stock_info.release_date else "",
                    "date": dp.date.strftime("%Y-%m-%d"),
                    "close": dp.close,
                    "change_rate": dp.change_rate,
                }
            )

    if not records:
        print("  CSV: 저장할 데이터 없음")
        return

    df = pd.DataFrame(records)
    csv_path = os.path.join(OUTPUT_DIR, f"투자경고종목분석({year}년).csv")
    storage.save_dataframe_csv(df, csv_path)


# ─────────────────────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(
        description="투자경고종목 연도별 수집",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  uv run python collect_yearly_warnings.py               # 2020~현재 전체
  uv run python collect_yearly_warnings.py --year 2025  # 2025년만
  uv run python collect_yearly_warnings.py --start 2023 --end 2025
  uv run python collect_yearly_warnings.py --include-active  # 진행 중 종목 포함
""",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--year", type=int, help="특정 연도만 수집 (예: 2023)")
    group.add_argument("--start", type=int, default=2020, help="시작 연도 (기본: 2020)")
    parser.add_argument("--end", type=int, default=datetime.now().year, help="종료 연도 (기본: 현재 연도)")
    parser.add_argument("--include-active", action="store_true", help="해제일 없는 진행 중 종목도 포함")
    return parser.parse_args()


def main():
    args = parse_args()
    include_active = args.include_active

    if args.year:
        years = [args.year]
    else:
        years = list(range(args.start, args.end + 1))

    # 지원하지 않는 연도 필터 (YEAR_RANGES에 없는 연도)
    valid_years = [y for y in years if y in YEAR_RANGES]
    skipped = set(years) - set(valid_years)
    if skipped:
        print(f"[경고] 수집 불가 연도 제외: {sorted(skipped)}")

    print(f"\n{'=' * 60}")
    print("  투자경고종목 연도별 수집")
    print(f"  대상 연도: {valid_years}")
    print(f"  진행 중 종목 포함: {'예' if include_active else '아니오'}")
    print(f"{'=' * 60}")

    results = {}
    for year in valid_years:
        ok = collect_year(year, include_active=include_active)
        results[year] = "[완료]" if ok else "[실패/데이터없음]"

    print(f"\n{'=' * 60}")
    print("  최종 결과 요약")
    print(f"{'=' * 60}")
    for year, status in results.items():
        print(f"  {year}년: {status}")
    print(f"\n  출력 디렉토리: {os.path.abspath(OUTPUT_DIR)}/")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
