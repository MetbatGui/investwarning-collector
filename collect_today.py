"""
일별 증분 수집 스크립트 (진짜 증분 + release_date 동기화)

기존 CSV에 오늘 신규 데이터만 append하며, KRX 최신 release_date를 항상 동기화합니다:
- 기존 종목: market_daily 1번 호출로 오늘 시세만 추가 (날짜당 API 1번)
- 신규 지정 종목: 전체 기간 수집 후 append
- 이미 수집된 날짜는 자동 skip
- release_date 변경 시 현재·이전 연도 CSV 원자적 갱신
- 연도 경계 종목(전년 지정 → 금년 해제) 자동 처리

직접 실행보다는 cli.py를 통해 실행하는 것을 우선합니다.
사용법:
  uv run python cli.py today            # 오늘 하루
  uv run python cli.py today --days 5   # 최근 5일 (누락 날짜 자동 처리)
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

# collect_yearly_warnings.py에서 공통 상수·저장 함수만 재사용
sys.path.insert(0, os.path.dirname(__file__))
from collect_yearly_warnings import (  # noqa: E402
    MAX_WARNING_DAYS,
    OUTPUT_DIR,
    PARQUET_DIR,
    TRADING_DAYS_AFTER_RELEASE,
    get_repository,
    get_storage,
)
from investment_hub.core.ports.storage_port import StoragePort  # noqa: E402
from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock  # noqa: E402
from investment_hub.infrastructure.adapters.pykrx_adapter import PyKRXAdapter  # noqa: E402
from investment_hub.infrastructure.collectors.daily_price_collector import collect_daily_prices_batch  # noqa: E402
from investment_hub.infrastructure.scrapers.krx_warning_scraper import fetch_investment_warning_stocks  # noqa: E402
from investment_hub.visualization.excel_exporter import WarningExcelExporter  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# 로깅 설정
# ─────────────────────────────────────────────────────────────────────────────
LOG_DIR = "logs"


def setup_logging(date_str: str) -> logging.Logger:
    # 로그 디렉토리는 항상 로컬에 생성
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, f"{date_str.replace('-', '')}.log")

    logger = logging.getLogger("collect_today")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼 함수
# ─────────────────────────────────────────────────────────────────────────────


def _csv_path(year: int) -> str:
    return os.path.join(OUTPUT_DIR, f"투자경고종목분석({year}년).csv")


def _load_existing_csv(csv_path: str, storage: StoragePort) -> pd.DataFrame:
    """기존 CSV 로드. 없으면 빈 DataFrame 반환."""
    if not storage.path_exists(csv_path):
        return pd.DataFrame()
    try:
        df = storage.load_dataframe(csv_path, dtype={"code": str})
        if not df.empty:
            df["date"] = df["date"].astype(str)
            df["designation_date"] = df["designation_date"].astype(str)
            df["release_date"] = df["release_date"].fillna("").astype(str)
        return df
    except Exception as e:
        print(f"[경고] 기존 CSV 로드 실패 ({csv_path}): {e}")
        return pd.DataFrame()


def _filter_stocks(
    stock_list: list,
    query_start: str,
    end_date: str,
    include_active: bool,
) -> list:
    """KRX 목록을 프로덕션 기준으로 필터링."""
    start_dt = pd.to_datetime(query_start)
    end_dt = pd.to_datetime(end_date)

    filtered = []
    for s in stock_list:
        if s.designation_date is None:
            continue
        if not (start_dt <= s.designation_date <= end_dt):
            continue
        if s.market == "코넥스":
            continue
        if s.release_date is not None:
            if (s.release_date - s.designation_date).days > MAX_WARNING_DAYS:
                continue
        else:
            if not include_active:
                continue
        filtered.append(s)
    return filtered


def _make_row(
    year: int,
    stock_info: InvestmentWarningStock,
    date_str: str,
    close: int,
    change_rate: float,
) -> dict:
    """CSV 행 딕셔너리 생성 헬퍼."""
    return {
        "year": year,
        "code": stock_info.code,
        "name": stock_info.name,
        "market": stock_info.market,
        "designation_date": stock_info.designation_date.strftime("%Y-%m-%d"),
        "release_date": stock_info.release_date.strftime("%Y-%m-%d") if stock_info.release_date else "",
        "date": date_str,
        "close": close,
        "change_rate": round(change_rate, 2),
    }


def _restore_from_df(df: pd.DataFrame) -> tuple:
    """
    CSV DataFrame → (filtered: list[InvestmentWarningStock], daily_prices: dict)

    Excel 재생성용. 동일 종목코드의 복수 지정 이력을 지원합니다.
    """
    filtered = []
    daily_prices: dict[str, list[DailyPriceData]] = {}

    # (code, designation_date) 쌍으로 InvestmentWarningStock 생성
    meta = (
        df[["code", "name", "market", "designation_date", "release_date"]]
        .drop_duplicates(subset=["code", "designation_date"])
        .reset_index(drop=True)
    )
    for _, row in meta.iterrows():
        release_str = row.get("release_date", "")
        release_dt = pd.to_datetime(release_str) if release_str and release_str not in ("", "nan") else None

        filtered.append(
            InvestmentWarningStock(
                code=str(row["code"]),
                name=row["name"],
                market=row["market"],
                designation_date=pd.to_datetime(row["designation_date"]),
                release_date=release_dt,
            )
        )

    # code별 전체 시세 (복수 지정 이력이 있어도 합산)
    for code, group in df.groupby("code"):
        prices = []
        for _, row in group.iterrows():
            prices.append(
                DailyPriceData(
                    code=str(code),
                    name=row["name"],
                    date=pd.to_datetime(row["date"]),
                    close=float(row["close"]),
                    change_rate=float(row["change_rate"]),
                )
            )
        daily_prices[str(code)] = prices

    return filtered, daily_prices


# ─────────────────────────────────────────────────────────────────────────────
# release_date 동기화
# ─────────────────────────────────────────────────────────────────────────────


def _sync_release_dates(
    year: int,
    all_filtered: list,
    storage: StoragePort,
    logger: logging.Logger,
) -> None:
    """
    KRX 최신 release_date를 현재·이전 연도 CSV에 원자적으로 동기화합니다.
    """
    if not all_filtered:
        return

    # KRX 최신 release_date 매핑 구성
    krx_release: dict[str, str] = {
        s.code: (s.release_date.strftime("%Y-%m-%d") if s.release_date else "") for s in all_filtered
    }

    for target_year in (year - 1, year):
        csv_path = _csv_path(target_year)
        df = _load_existing_csv(csv_path, storage)
        if df.empty:
            continue

        # 이 CSV 내 종목 중 release_date가 달라진 것만 추출
        changes: dict[str, tuple[str, str]] = {}  # code → (old, new)
        for code, new_val in krx_release.items():
            mask = df["code"] == code
            if not mask.any():
                continue
            old_val = df.loc[mask, "release_date"].iloc[0]
            if old_val != new_val:
                changes[code] = (old_val, new_val)

        if not changes:
            continue

        # 변경 적용 + 로그
        for code, (old_val, new_val) in changes.items():
            name = df.loc[df["code"] == code, "name"].iloc[0]
            label = "해제" if (not old_val and new_val) else "갱신"
            logger.info(
                f"  [{target_year}년] {code}({name}) release_date {label}: "
                f"'{old_val or '진행중'}' → '{new_val or '진행중'}'"
            )
            df.loc[df["code"] == code, "release_date"] = new_val

        if storage.save_dataframe_csv(df, csv_path):
            logger.info(f"  [{target_year}년] release_date {len(changes)}종목 동기화 완료")


# ─────────────────────────────────────────────────────────────────────────────
# 핵심 함수: 진짜 증분 수집
# ─────────────────────────────────────────────────────────────────────────────


def collect_today(end_date: str, days: int = 1, include_active: bool = False) -> bool:
    """
    진짜 증분 수집 + release_date 동기화.

    - release_date 변경 시 현재·이전 연도 CSV 즉시 반영
    - 연도 경계 종목(전년 지정, 금년 해제) 자동 처리
    - 기존 종목: 누락 날짜의 시세를 market_daily(날짜당 API 1번)로 추가
    - 신규 지정 종목: 전체 기간 수집 후 append
    - 이미 수집된 날짜는 skip

    Args:
        end_date: 수집 마지막 날짜 (YYYY-MM-DD)
        days: 처리할 최근 일수
        include_active: 진행 중 종목 포함 여부
    """
    logger = setup_logging(end_date)
    year = int(end_date[:4])
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    year_start = f"{year}-01-01"

    logger.info("=" * 60)
    logger.info(f"  투자경고종목 증분 수집: {end_date} (최근 {days}일)")
    logger.info(f"  진행 중 종목 포함: {'예' if include_active else '아니오'}")
    logger.info("=" * 60)

    # ── Step 1: KRX 목록 조회 (연도 경계 자동 감지) ─────────────────────────
    storage = get_storage()
    # 이전 연도 미해제 종목 감지 → 조회 시작일 확장
    prev_csv_path = _csv_path(year - 1)
    prev_df_prefetch = _load_existing_csv(prev_csv_path, storage)
    cross_year_unsettled = not prev_df_prefetch.empty and (prev_df_prefetch["release_date"] == "").any()

    query_start = f"{year - 1}-01-01" if cross_year_unsettled else year_start
    if cross_year_unsettled:
        logger.info(f"[1/6] KRX KIND 목록 조회 ({query_start} ~ {end_date}) ← {year - 1}년 미해제 종목 감지, 범위 확장")
    else:
        logger.info(f"[1/6] KRX KIND 목록 조회 ({query_start} ~ {end_date})...")

    try:
        stock_list = fetch_investment_warning_stocks(query_start, end_date)
    except Exception as e:
        logger.error(f"KRX 조회 실패: {e}")
        return False

    # 전체 필터링 (연도 무관 — release_date 동기화용)
    all_filtered = _filter_stocks(stock_list, query_start, end_date, include_active)
    # 현재 연도 종목만 (시세 수집용)
    filtered = [s for s in all_filtered if s.designation_date >= pd.to_datetime(year_start)]
    logger.info(f"  → 전체 {len(all_filtered)}종목 / {year}년 대상 {len(filtered)}종목")

    if not all_filtered:
        logger.warning("  조회 결과 없음. 종료.")
        return False

    # ── Step 2: release_date 동기화 (Parquet + CSV) ──────────────────────────
    logger.info("[2/6] release_date 동기화 (현재·이전 연도 Parquet)...")
    _sync_release_dates(year, all_filtered, storage, logger)

    # Parquet에서 현재 연도 기존 데이터 로드
    repo = get_repository()
    existing_stocks, existing_prices_by_code = repo.load_year(year)
    existing_codes = {s.code for s in existing_stocks}
    # 이미 수집된 날짜 추출 (전체 종목 기준 합집합)
    collected_dates: set[str] = set()
    for price_list in existing_prices_by_code.values():
        for dp in price_list:
            collected_dates.add(dp.date.strftime("%Y-%m-%d"))

    # ── Step 3: 처리 대상 날짜 계산 (실제 영업일 기준) ───────────────────────────
    adapter = PyKRXAdapter()
    # 넉넉하게 기간을 조회한 뒤 마지막 N 영업일을 추출 (추석/설날 대비)
    trading_days = adapter.get_trading_days(
        start_date=end_dt - timedelta(days=days * 4 + 10),
        end_date=end_dt,
    )
    target_dates = [d.strftime("%Y-%m-%d") for d in trading_days[-days:]]

    # 만약 거래일을 못 가져왔을 경우 대비 (기존 달력 기준 방식 fallback)
    if not target_dates:
        logger.warning("  영업일 목록 조회 실패. 달력 기준 날짜로 대체합니다.")
        target_dates = [(end_dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]

    missing_dates = [d for d in target_dates if d not in collected_dates]
    logger.info(f"[3/6] 대상 날짜(영업일): {target_dates}")
    logger.info(f"[3/6] 누락 날짜: {missing_dates} ({len(missing_dates)}/{len(target_dates)}일)")

    if not missing_dates and not any(s.code not in existing_codes for s in filtered):
        logger.info("  모든 데이터가 이미 최신 상태입니다. skip.")
        return True

    # ── Step 4: 기존 종목 → 누락 날짜의 시세 추가 (market_daily) ───────────
    all_new_rows: list[dict] = []
    known_stocks = [s for s in filtered if s.code in existing_codes]

    if missing_dates and known_stocks:
        logger.info(f"[4/6] 기존 {len(known_stocks)}종목 × {len(missing_dates)}일 시세 수집...")

        for date_str in missing_dates:
            date_dt = datetime.strptime(date_str, "%Y-%m-%d")
            market_df = adapter.get_daily_market_ohlcv(date_dt)
            if market_df.empty:
                logger.info(f"  {date_str}: 휴장일 또는 데이터 없음, skip")
                continue

            day_count = 0
            for stock_info in known_stocks:
                code = stock_info.code
                if code not in market_df.index:
                    continue
                row = market_df.loc[code]
                close = int(row.get("종가", 0))
                if close <= 0:
                    continue
                change_rate = float(row.get("등락률", 0.0))
                all_new_rows.append(_make_row(year, stock_info, date_str, close, change_rate))
                day_count += 1

            logger.info(f"  {date_str}: {day_count}종목 추가")
    else:
        logger.info("[4/6] 기존 종목 시세 추가 없음 (skip)")

    # ── Step 5: 신규 종목 → 전체 기간 수집 ──────────────────────────────────
    new_stocks = [s for s in filtered if s.code not in existing_codes]

    if new_stocks:
        logger.info(f"[5/6] 신규 {len(new_stocks)}종목 전체 기간 수집...")
        try:
            new_prices = collect_daily_prices_batch(
                new_stocks,
                trading_days_after_release=TRADING_DAYS_AFTER_RELEASE,
            )
        except Exception as e:
            logger.error(f"신규 종목 시세 수집 실패: {e}")
            new_prices = {}

        for stock_info in new_stocks:
            for dp in new_prices.get(stock_info.code, []):
                all_new_rows.append(
                    _make_row(
                        year,
                        stock_info,
                        dp.date.strftime("%Y-%m-%d"),
                        int(dp.close),
                        dp.change_rate,
                    )
                )
    else:
        logger.info("[5/6] 신규 종목 없음 (skip)")

    # ── Step 6: Parquet append → CSV append → Excel 재생성 ──────────────────
    logger.info("[6/6] 파일 저장 중...")

    if not all_new_rows:
        if not existing_stocks:
            logger.warning("추가할 데이터 없음.")
            return False
        # 신규 행 없어도 Excel은 재생성 (release_date 동기화 반영)
        logger.info("  신규 행 없음. Excel 재생성만 수행.")
        final_stocks, final_prices = existing_stocks, existing_prices_by_code
    else:
        # 신규 행을 DailyPriceData & InvestmentWarningStock으로 변환
        new_stocks_map: dict[str, InvestmentWarningStock] = {s.code: s for s in filtered}
        new_prices_map: dict[str, list[DailyPriceData]] = {}
        for row in all_new_rows:
            code = row["code"]
            stock = new_stocks_map.get(code)
            if stock is None:
                continue
            dp = DailyPriceData(
                code=code,
                name=row["name"],
                date=datetime.strptime(row["date"], "%Y-%m-%d"),
                close=float(row["close"]),
                change_rate=float(row["change_rate"]),
            )
            new_prices_map.setdefault(code, []).append(dp)

        storage.ensure_directory(OUTPUT_DIR)

        # Parquet 증분 저장
        repo.append_year(year, list(new_stocks_map.values()), new_prices_map)
        logger.info(f"  Parquet 증분 저장: {PARQUET_DIR}/{year}.parquet (+{len(all_new_rows)}행)")

        # CSV 하위 호환 저장 (기존 방식 유지)
        new_df = pd.DataFrame(all_new_rows)
        csv_path = _csv_path(year)
        prev_csv = _load_existing_csv(csv_path, storage)
        updated_df = (
            pd.concat([prev_csv, new_df], ignore_index=True)
            .drop_duplicates(subset=["code", "designation_date", "date"])
            .sort_values(["code", "designation_date", "date"])
            .reset_index(drop=True)
        )
        storage.save_dataframe_csv(updated_df, csv_path)
        logger.info(f"  CSV 저장: {os.path.basename(csv_path)} ({len(updated_df):,}행)")

        # Excel 재생성용 최신 Parquet 로드
        final_stocks, final_prices = repo.load_year(year)

    exporter = WarningExcelExporter(trading_days_after_release=TRADING_DAYS_AFTER_RELEASE)
    wb = exporter.export(year, final_stocks, final_prices)
    xlsx_path = os.path.join(OUTPUT_DIR, f"투자경고종목분석({year}년).xlsx")
    storage.save_workbook(wb, xlsx_path)

    logger.info(
        f"[완료] 신규 {len(new_stocks)}종목 전체 수집 / 기존 {len(known_stocks)}종목 × {len(missing_dates)}일 시세 추가"
    )
    logger.info("=" * 60)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 진입점 (cli.py 경유 권장, 직접 실행도 가능)
# ─────────────────────────────────────────────────────────────────────────────


def _parse_args():
    parser = argparse.ArgumentParser(
        description="투자경고종목 증분 수집",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  uv run python cli.py today             # 오늘 하루
  uv run python cli.py today --days 5    # 최근 5일
  uv run python cli.py today --date 2026-02-21  # 특정 날짜
""",
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        metavar="YYYY-MM-DD",
        help="수집 마지막 날짜 (기본: 오늘)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        metavar="N",
        help="수집할 최근 일수 (기본: 1)",
    )
    parser.add_argument(
        "--include-active",
        action="store_true",
        help="해제일 없는 진행 중 종목도 포함",
    )
    return parser.parse_args()


def main():
    args = _parse_args()

    try:
        end_date = datetime.strptime(args.date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        print(f"[오류] 날짜 형식 오류: {args.date}")
        sys.exit(1)

    if end_date > datetime.now().strftime("%Y-%m-%d"):
        print(f"[오류] 미래 날짜: {end_date}")
        sys.exit(1)

    if args.days < 1:
        print("[오류] --days는 1 이상이어야 합니다.")
        sys.exit(1)

    ok = collect_today(end_date, days=args.days, include_active=args.include_active)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
