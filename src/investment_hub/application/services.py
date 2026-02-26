"""
투자경고종목 수집 및 리포트 생성을 위한 Application Service 계층.

비즈니스 로직(Use Case)들을 담당하며, 도메인 모델과 Port(저장소, 수집기)를 조합하여 애플리케이션의 동작을 오케스트레이션합니다.
"""

import logging
import os
from datetime import datetime, timedelta

import pandas as pd
from investment_hub.core.ports.repository_port import WarningStockRepository

from investment_hub.core.ports.storage_port import StoragePort
from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock
from investment_hub.infrastructure.adapters.pykrx_adapter import PyKRXAdapter
from investment_hub.infrastructure.collectors.daily_price_collector import collect_daily_prices_batch
from investment_hub.infrastructure.scrapers.krx_warning_scraper import fetch_investment_warning_stocks
from investment_hub.visualization.excel_exporter import WarningExcelExporter

# ─────────────────────────────────────────────────────────────────────────────
# 로깅 설정
# ─────────────────────────────────────────────────────────────────────────────
LOG_DIR = "logs"


def setup_logging(date_str: str) -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, f"{date_str.replace('-', '')}.log")

    logger = logging.getLogger("WarningCollectionService")
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

class WarningCollectionService:
    """투자경고종목 수집을 오케스트레이션하는 애플리케이션 서비스"""

    def __init__(
        self,
        repository: WarningStockRepository,
        storage: StoragePort,
        output_dir: str = "output",
        max_warning_days: int = 60,
        trading_days_after_release: int = 3,
    ):
        self.repository = repository
        self.storage = storage
        self.output_dir = output_dir
        self.max_warning_days = max_warning_days
        self.trading_days_after_release = trading_days_after_release
        self.excel_exporter = WarningExcelExporter(
            trading_days_after_release=trading_days_after_release
        )

    def _csv_path(self, year: int) -> str:
        return os.path.join(self.output_dir, f"투자경고종목분석({year}년).csv")

    def _save_csv(self, year: int, filtered: list, daily_prices_by_code: dict):
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
        csv_path = self._csv_path(year)
        self.storage.save_dataframe_csv(df, csv_path)

    def collect_year(self, year: int, include_active: bool = False) -> bool:
        """단일 연도 투자경고종목 전체 백필 처리"""
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"

        if year == datetime.now().year:
            end_date = datetime.now().strftime("%Y-%m-%d")

        print(f"\n[1/4] {year}년 투자경고종목 백필...")
        try:
            stock_list = fetch_investment_warning_stocks(start_date, end_date)
        except Exception as e:
            print(f"KRX 조회 실패: {e}")
            return False

        # ── Step 2: 필터링 로직 (순수 파이썬)
        filtered = []
        for s in stock_list:
            if s.designation_date is None:
                continue
            if s.market == "코넥스":
                continue

            if s.release_date is not None:
                if (s.release_date - s.designation_date).days > self.max_warning_days:
                    continue
            else:
                if not include_active:
                    continue
            filtered.append(s)

        print(f"  → 전체 {len(stock_list)}종목 중, 조건 부합 {len(filtered)}종목 처리")
        if not filtered:
            return False

        # ── Step 3: 시세 조회
        print("\n[3/4] 시세 데이터 수집...")
        try:
            daily_prices_by_code = collect_daily_prices_batch(
                filtered,
                trading_days_after_release=self.trading_days_after_release,
            )
        except Exception as e:
            print(f"시세 수집 중 오류: {e}")
            return False

        # 빈 데이터 방지
        filtered = [s for s in filtered if daily_prices_by_code.get(s.code)]
        if not filtered:
            print("  수집된 유효 시세 데이터가 없습니다.")
            return False

        # ── Step 4: 저장
        print("\n[4/4] 결과 저장 (Parquet + Excel)...")
        self.storage.ensure_directory(self.output_dir)

        # 4-a. Parquet 저장
        self.repository.save_year(year, filtered, daily_prices_by_code)
        print(f"  [Parquet] {year}.parquet 백필/저장 완료")

        # 4-b. CSV 분리 저장
        self._save_csv(year, filtered, daily_prices_by_code)

        # 4-c. Excel 재생성
        reloaded_stocks, reloaded_prices = self.repository.load_year(year)
        wb = self.excel_exporter.export(year, reloaded_stocks, reloaded_prices)
        xlsx_path = os.path.join(self.output_dir, f"투자경고종목분석({year}년).xlsx")
        self.storage.save_workbook(wb, xlsx_path)

        print(f"\n  [완료] {year}년 수집 백필 및 Excel 생성 완료")
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # 증분 수집 (collect_today) 헬퍼
    # ─────────────────────────────────────────────────────────────────────────
    def _load_existing_csv(self, csv_path: str) -> pd.DataFrame:
        """기존 CSV 로드. 없으면 빈 DataFrame 반환."""
        if not self.storage.path_exists(csv_path):
            return pd.DataFrame()
        try:
            df = self.storage.load_dataframe(csv_path, dtype={"code": str})
            if not df.empty:
                df["date"] = df["date"].astype(str)
                df["designation_date"] = df["designation_date"].astype(str)
                df["release_date"] = df["release_date"].fillna("").astype(str)
            return df
        except Exception as e:
            print(f"[경고] 기존 CSV 로드 실패 ({csv_path}): {e}")
            return pd.DataFrame()

    def _filter_stocks(
        self,
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
                if (s.release_date - s.designation_date).days > self.max_warning_days:
                    continue
            else:
                if not include_active:
                    continue
            filtered.append(s)
        return filtered

    def _make_row(
        self,
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

    def _sync_release_dates(
        self,
        year: int,
        all_filtered: list,
        logger: logging.Logger,
    ) -> None:
        """KRX 최신 release_date를 현재·이전 연도 CSV에 원자적으로 동기화합니다."""
        if not all_filtered:
            return

        krx_release: dict[str, str] = {
            s.code: (s.release_date.strftime("%Y-%m-%d") if s.release_date else "") for s in all_filtered
        }

        for target_year in (year - 1, year):
            csv_path = self._csv_path(target_year)
            df = self._load_existing_csv(csv_path)
            if df.empty:
                continue

            changes: dict[str, tuple[str, str]] = {}
            for code, new_val in krx_release.items():
                mask = df["code"] == code
                if not mask.any():
                    continue
                old_val = df.loc[mask, "release_date"].iloc[0]
                if old_val != new_val:
                    changes[code] = (old_val, new_val)

            if not changes:
                continue

            for code, (old_val, new_val) in changes.items():
                name = df.loc[df["code"] == code, "name"].iloc[0]
                label = "해제" if (not old_val and new_val) else "갱신"
                logger.info(
                    f"  [{target_year}년] {code}({name}) release_date {label}: "
                    f"'{old_val or '진행중'}' → '{new_val or '진행중'}'"
                )
                df.loc[df["code"] == code, "release_date"] = new_val

            if self.storage.save_dataframe_csv(df, csv_path):
                logger.info(f"  [{target_year}년] release_date {len(changes)}종목 동기화 완료")

    # ─────────────────────────────────────────────────────────────────────────
    # 증분 수집 메인 로직
    # ─────────────────────────────────────────────────────────────────────────
    def collect_today(self, end_date: str, days: int = 1, include_active: bool = False) -> bool:
        """
        진짜 증분 수집 + release_date 동기화.
        - 기존 종목 시세 추가, 신규 종목 전체 수집
        - Parquet/CSV 백업 후 Excel 재생성 과정 파이프라인
        """
        logger = setup_logging(end_date)
        year = int(end_date[:4])
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        year_start = f"{year}-01-01"

        logger.info("=" * 60)
        logger.info(f"  투자경고종목 증분 수집: {end_date} (최근 {days}일)")
        logger.info(f"  진행 중 종목 포함: {'예' if include_active else '아니오'}")
        logger.info("=" * 60)

        # ── Step 1: KRX 목록 조회
        prev_csv_path = self._csv_path(year - 1)
        prev_df_prefetch = self._load_existing_csv(prev_csv_path)
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

        all_filtered = self._filter_stocks(stock_list, query_start, end_date, include_active)
        filtered = [s for s in all_filtered if s.designation_date >= pd.to_datetime(year_start)]
        logger.info(f"  → 전체 {len(all_filtered)}종목 / {year}년 대상 {len(filtered)}종목")

        if not all_filtered:
            logger.warning("  조회 결과 없음. 종료.")
            return False

        # ── Step 2: release_date 동기화
        logger.info("[2/6] release_date 동기화 (현재·이전 연도 Parquet)...")
        self._sync_release_dates(year, all_filtered, logger)

        existing_stocks, existing_prices_by_code = self.repository.load_year(year)
        existing_codes = {s.code for s in existing_stocks}
        collected_dates: set[str] = set()
        for price_list in existing_prices_by_code.values():
            for dp in price_list:
                collected_dates.add(dp.date.strftime("%Y-%m-%d"))

        # ── Step 3: 처리 대상 날짜 계산
        adapter = PyKRXAdapter()
        trading_days = adapter.get_trading_days(
            start_date=end_dt - timedelta(days=days * 4 + 10),
            end_date=end_dt,
        )
        target_dates = [d.strftime("%Y-%m-%d") for d in trading_days[-days:]]

        if not target_dates:
            logger.warning("  영업일 목록 조회 실패. 달력 기준 날짜로 대체합니다.")
            target_dates = [(end_dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]

        missing_dates = [d for d in target_dates if d not in collected_dates]
        logger.info(f"[3/6] 대상 날짜(영업일): {target_dates}")
        logger.info(f"[3/6] 누락 날짜: {missing_dates} ({len(missing_dates)}/{len(target_dates)}일)")

        if not missing_dates and not any(s.code not in existing_codes for s in filtered):
            logger.info("  모든 데이터가 이미 최신 상태입니다. skip.")
            return True

        # ── Step 4: 기존 종목 증분 추가
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
                    all_new_rows.append(self._make_row(year, stock_info, date_str, close, change_rate))
                    day_count += 1
                logger.info(f"  {date_str}: {day_count}종목 추가")
        else:
            logger.info("[4/6] 기존 종목 시세 추가 없음 (skip)")

        # ── Step 5: 신규 종목 전체 수집
        new_stocks = [s for s in filtered if s.code not in existing_codes]
        if new_stocks:
            logger.info(f"[5/6] 신규 {len(new_stocks)}종목 전체 기간 수집...")
            try:
                new_prices = collect_daily_prices_batch(
                    new_stocks,
                    trading_days_after_release=self.trading_days_after_release,
                )
            except Exception as e:
                logger.error(f"신규 종목 시세 수집 실패: {e}")
                new_prices = {}

            for stock_info in new_stocks:
                for dp in new_prices.get(stock_info.code, []):
                    all_new_rows.append(
                        self._make_row(
                            year,
                            stock_info,
                            dp.date.strftime("%Y-%m-%d"),
                            int(dp.close),
                            dp.change_rate,
                        )
                    )
        else:
            logger.info("[5/6] 신규 종목 없음 (skip)")

        # ── Step 6: 병합 및 저장
        logger.info("[6/6] 파일 저장 중...")
        if not all_new_rows:
            if not existing_stocks:
                logger.warning("추가할 데이터 없음.")
                return False
            logger.info("  신규 행 없음. Excel 재생성만 수행.")
            final_stocks, final_prices = existing_stocks, existing_prices_by_code
        else:
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

            self.storage.ensure_directory(self.output_dir)

            self.repository.append_year(year, list(new_stocks_map.values()), new_prices_map)
            logger.info(f"  Parquet 증분 저장완료 (+{len(all_new_rows)}행)")

            new_df = pd.DataFrame(all_new_rows)
            csv_path = self._csv_path(year)
            prev_csv = self._load_existing_csv(csv_path)
            updated_df = (
                pd.concat([prev_csv, new_df], ignore_index=True)
                .drop_duplicates(subset=["code", "designation_date", "date"])
                .sort_values(["code", "designation_date", "date"])
                .reset_index(drop=True)
            )
            self.storage.save_dataframe_csv(updated_df, csv_path)
            logger.info(f"  CSV 저장: {os.path.basename(csv_path)} ({len(updated_df):,}행)")

            final_stocks, final_prices = self.repository.load_year(year)

        wb = self.excel_exporter.export(year, final_stocks, final_prices)
        xlsx_path = os.path.join(self.output_dir, f"투자경고종목분석({year}년).xlsx")
        self.storage.save_workbook(wb, xlsx_path)

        logger.info(
            f"[완료] 신규 {len(new_stocks)}종목 전체 수집 / 기존 {len(known_stocks)}종목 × {len(missing_dates)}일 시세 추가"
        )
        logger.info("=" * 60)
        return True


class ReportGenerationService:
    """Parquet 저장소 데이터를 읽어 Excel 리포트로 변환·저장하는 서비스"""

    def __init__(
        self,
        repository: WarningStockRepository,
        storage: StoragePort,
        output_dir: str = "output",
        trading_days_after_release: int = 3,
    ):
        self.repository = repository
        self.storage = storage
        self.output_dir = output_dir
        self.excel_exporter = WarningExcelExporter(
            trading_days_after_release=trading_days_after_release
        )

    def generate_excel_report(self, year: int) -> bool:
        """지정 연도의 저장된 Parquet 데이터를 기반으로 엑셀 파일 재생성"""
        print(f"\n[Export Excel] {year}년 데이터 엑셀 재생성 시작...")

        try:
            stocks, prices = self.repository.load_year(year)
            if not stocks:
                print(f"  [경고] {year}년 Parquet 데이터가 비어있거나 존재하지 않습니다.")
                return False

            wb = self.excel_exporter.export(year, stocks, prices)
            self.storage.ensure_directory(self.output_dir)
            xlsx_path = os.path.join(self.output_dir, f"투자경고종목분석({year}년).xlsx")

            self.storage.save_workbook(wb, xlsx_path)
            print(f"  [완료] {xlsx_path} 재생성 완료")
            return True
        except Exception as e:
            print(f"  [오류] 엑셀 재생성 중 오류 발생: {e}")
            return False
