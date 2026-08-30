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
from investment_hub.domain.models import CollectionResult, DailyPriceData, InvestmentWarningStock
from investment_hub.infrastructure.adapters.native_krx_adapter import NativeKrxAdapter as PyKRXAdapter
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
    """KRX 기업공시 시스템의 투자경고 종목 추출부터 내부 파켓 저장, 엑셀 생성을 주관하는 파이프라인.

    수집한 투자경고 데이터를 DB(파켓)에 저장하고, CSV 및 엑셀(openpyxl)
    리포트를 산출하는 End-to-End Orchestrator 역할을 수행합니다.
    """

    def __init__(
        self,
        repository: WarningStockRepository,
        storage: StoragePort,
        output_dir: str = "output",
        max_warning_days: int = 60,
        trading_days_after_release: int = 3,
    ):
        """WarningCollectionService 초기화 및 포트 주입.

        Args:
            repository (WarningStockRepository): 파켓 등의 연도별 저장소 어댑터 포트 개체.
            storage (StoragePort): 로컬/구글 드라이브 입출력을 위한 어댑터 포트 개체.
            output_dir (str): 파일이 저장될 최상위 디렉토리 명칭. 기본값 "output".
            max_warning_days (int): 최대 허용 경고일수(지정~해제). 해당 값을 넘길시 오류로 간주해 필터링. 기본 60.
            trading_days_after_release (int): 해제일 기준 이후 시세를 추적할(수집할) 추가 최대 영업일. 기본 3.
        """
        self.repository = repository
        self.storage = storage
        self.output_dir = output_dir
        self.max_warning_days = max_warning_days
        self.trading_days_after_release = trading_days_after_release
        self.excel_exporter = WarningExcelExporter(trading_days_after_release=trading_days_after_release)


    def collect_year(self, year: int, end_date: str | None = None, include_active: bool = True) -> bool:
        """지정된 연도의 투자경고종목 데이터를 전체 백필(Backfill) 방식으로 수집합니다.

        Args:
            year (int): 수집할 연도.
            end_date (str | None): 수집 종료일 (YYYY-MM-DD). None이면 연말 또는 오늘.
            include_active (bool): 현재 경고 유지 중인 종목 포함 여부.

        Returns:
            bool: 성공적으로 수집 및 저장이 완료되면 True.
        """
        print(f"\n[{year}년 전체 백필 시작]")

        filtered = self._step_y1_fetch_and_filter(year, end_date, include_active)
        if not filtered: return False

        prices_by_code = self._step_y2_collect_prices(filtered)
        if not prices_by_code: return False

        filtered = [s for s in filtered if prices_by_code.get(s.code)]
        return self._step_y3_save_and_export(year, filtered, prices_by_code)

    def _step_y1_fetch_and_filter(self, year: int, end_date_str: str | None, include_active: bool) -> list:
        """[Step] 지정된 연도의 종목 목록을 가져오고 정해진 규칙에 따라 필터링합니다.

        Args:
            year (int): 수집 대상 연도.
            end_date_str (str): 데이터 수집 대상 상한.
            include_active (bool): 해제일 미정 종목 포함 여부.

        Returns:
            list[InvestmentWarningStock]: 필터링된 종목 객체 리스트.
        """
        print(f"[1/4] '{year}' 연도 종목 스크래핑...")
        start_date = f"{year}-01-01"

        now = datetime.now()
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        max_today = now if now >= market_close else now - timedelta(days=1)

        end_date = end_date_str if end_date_str else (max_today.strftime("%Y-%m-%d") if year == now.year else f"{year}-12-31")
        try:
            stocks = fetch_investment_warning_stocks(start_date, end_date)
            filtered = self._filter_stocks(stocks, start_date, end_date, include_active)
            print(f"  → 전체 {len(stocks)}종목 중 정책 부합 {len(filtered)}종목")
            return filtered
        except Exception as e:
            print(f"스크래핑 중 오류: {e}"); return []

    def _step_y2_collect_prices(self, stocks: list) -> dict:
        """[Step] 필터링된 종목들에 대해 필요한 전체 기간 시세를 일괄 수집합니다.

        Args:
            stocks (list[InvestmentWarningStock]): 대상 종목 리스트.

        Returns:
            dict: 종목코드를 키로, DailyPriceData 리스트를 값으로 갖는 맵.
        """
        print("\n[3/4] 시세 데이터 수집...")
        try:
            return collect_daily_prices_batch(stocks, trading_days_after_release=self.trading_days_after_release)
        except Exception as e:
            print(f"시세 수집 중 오류: {e}"); return {}

    def _step_y3_save_and_export(self, year: int, stocks: list, prices: dict) -> bool:
        """[Step] 수집된 데이터를 영구 저장소에 기록하고 엑셀 리포트를 생성합니다.

        Args:
            year (int): 대상 연도.
            stocks (list[InvestmentWarningStock]): 최종 포함된 종목 리스트.
            prices (dict): 종목별 시세 데이터.

        Returns:
            bool: 저장 및 생성 성공 여부.
        """
        print("\n[4/4] 결과 저장 (Parquet + Excel)...")
        self.storage.ensure_directory(self.output_dir)
        self.repository.save_year(year, stocks, prices)

        re_stocks, re_prices = self.repository.load_year(year)
        wb = self.excel_exporter.export(year, re_stocks, re_prices)
        self.storage.save_workbook(wb, os.path.join(self.output_dir, f"투자경고종목분석({year}년).xlsx"))
        print(f"\n  [완료] {year}년 수집 백필 및 Excel 생성 완료")
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # 증분 수집 (collect_today) 헬퍼
    # ─────────────────────────────────────────────────────────────────────────

    def _filter_stocks(
        self,
        stock_list: list,
        query_start: str,
        end_date: str,
        include_active: bool,
    ) -> list:
        """스크랩한 원본 목록을 대상으로 사내 프로덕션 정책(코넥스 제한방어, 최대 일수, 잔존 여부)을 반영해 여과합니다.

        Args:
            stock_list (list[InvestmentWarningStock]): 스크래퍼로부터 넘어온 가공 전 도메인 콜렉션.
            query_start (str): 지정일 검사 하한 기준 문자열 ('YYYY-MM-DD').
            end_date (str): 지정일 검사 상한 기준 문자열 ('YYYY-MM-DD').
            include_active (bool): 해제일 미정인 현재 경고 종목 포함 여부 플래그.

        Returns:
            list[InvestmentWarningStock]: 유효 조건을 충족하는 원소들로만 구성된 정리 목록.
        """
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
        """도메인 데이터 속성과 시장 수치를 결합하여 단일 CSV/Parquet 롱폼 행을 딕셔너리로 구축합니다.

        Args:
            year (int): 처리 소속 연도.
            stock_info (InvestmentWarningStock): 대상 종목 메타 객체.
            date_str (str): 시세 체결 영업 일자.
            close (int): 해당일 종가.
            change_rate (float): 전일 대비 등락률 (퍼센트율).

        Returns:
            dict: 직렬화가 준비된 평면화 딕셔너리 구조체.
        """
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
        """이전 수집분 중 아직 '진행중'이었던 종목에 대해 KRX 최신 해제일(release_date)이 감지되면 원자적으로 동기화 갱신합니다.

        올해와 전년도 분의 Parquet 파일을 대상으로 확인 및 수정을 일괄 시도합니다.

        Args:
            year (int): 탐색 시점 기준 대상 연도.
            all_filtered (list[InvestmentWarningStock]): 현재 KRX에서 응답한 실시간 메타 배열.
            logger (logging.Logger): 변경 내역 트래킹에 사용될 로거.
        """
        if not all_filtered:
            return

        krx_release = {s.code: s.release_date for s in all_filtered}

        for target_year in (year - 1, year):
            if not self.repository.year_exists(target_year):
                continue

            stocks, prices = self.repository.load_year(target_year)
            if not stocks:
                continue

            changes = 0
            for stock in stocks:
                new_release_date = krx_release.get(stock.code)
                if stock.code in krx_release and stock.release_date != new_release_date:
                    old_val = stock.release_date.strftime("%Y-%m-%d") if stock.release_date else "진행중"
                    new_val = new_release_date.strftime("%Y-%m-%d") if new_release_date else "진행중"
                    label = "해제" if (not stock.release_date and new_release_date) else "갱신"
                    logger.info(
                        f"  [{target_year}년] {stock.code}({stock.name}) release_date {label}: "
                        f"'{old_val}' → '{new_val}'"
                    )
                    stock.release_date = new_release_date
                    changes += 1

            if changes > 0:
                self.repository.save_year(target_year, stocks, prices)
                logger.info(f"  [{target_year}년] release_date {changes}종목 동기화 완료")

    # ─────────────────────────────────────────────────────────────────────────
    # 증분 수집 메인 로직
    # ─────────────────────────────────────────────────────────────────────────
    def _step1_fetch_stocks(self, year: int, end_date_str: str | None, include_active: bool) -> tuple[list, list]:
        """[Step] 증분 수집을 위해 현재 및 이전 연도의 종목 목록을 KRX에서 가져와 필터링합니다.

        Args:
            year (int): 현재 처리 중인 연도.
            end_date_str (str): 조회 종료일 (옵셔널, 없으면 기본값 당해 연말 또는 오늘).
            include_active (bool): 해제일 미정 종목 포함 여부.

        Returns:
            tuple[list, list]: (전체 필터링 목록, 현재 연도 전용 필터링 목록)
        """
        year_start = f"{year}-01-01"
        now = datetime.now()
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        max_today = now if now >= market_close else now - timedelta(days=1)

        if end_date_str:
            end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
        else:
            end_dt = min(datetime(year, 12, 31), max_today)
        end_date = end_dt.strftime("%Y-%m-%d")

        if end_dt.year < year and not end_date_str:
            print("  [건너뜀] 대상 기간이 유효하지 않음 (미래 연도)")
            return [], []

        cross_year = self.repository.year_exists(year - 1)
        query_start = f"{year - 1}-01-01" if cross_year else year_start
        stock_list = fetch_investment_warning_stocks(query_start, end_date)
        all_filtered = self._filter_stocks(stock_list, query_start, end_date, include_active)
        filtered = [s for s in all_filtered if s.designation_date >= pd.to_datetime(year_start)]
        return all_filtered, filtered

    def _step2_sync_and_load(self, year: int, all_filtered: list, logger: logging.Logger) -> tuple[list, dict]:
        """[Step] 최신 해제일 정보를 동기화하고 해당 연도의 기존 데이터를 로드합니다.

        Args:
            year (int): 대상 연도.
            all_filtered (list): KRX에서 가져온 전체 종목 목록.
            logger (logging.Logger): 작업 로그를 기록할 로거.

        Returns:
            tuple[list, dict]: (기존 저장된 종목 리스트, 종목별 시세 맵)
        """
        logger.info("[2/6] release_date 동기화 (현재·이전 연도 Parquet)...")
        self._sync_release_dates(year, all_filtered, logger)
        return self.repository.load_year(year)

    def _step3_get_dates(self, end_dt: datetime, days: int, existing_prices: dict) -> tuple[list[str], list[str]]:
        """[Step] 수집이 필요한 타겟 영업일 목록과 누락된 날짜를 식별합니다.

        Args:
            end_dt (datetime): 기준 종료 일시.
            days (int): 조회할 영업일 수.
            existing_prices (dict): 기존에 이미 수집된 시세 데이터 맵.

        Returns:
            tuple[list[str], list[str]]: (전체 타겟 날짜 목록, 실제 수집이 필요한 누락 날짜 목록)
        """
        collected_dates = {dp.date.strftime("%Y-%m-%d") for plist in existing_prices.values() for dp in plist}
        trading_days = PyKRXAdapter().get_trading_days(end_dt - timedelta(days=days * 4 + 10), end_dt)
        target_dates = [d.strftime("%Y-%m-%d") for d in trading_days[-days:]]
        if not target_dates:
            target_dates = [(end_dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
        missing_dates = [d for d in target_dates if d not in collected_dates]
        return target_dates, missing_dates

    def _step45_collect_prices(self, year: int, filtered: list, existing_codes: set, missing_dates: list) -> list[dict]:
        """[Step] 기존 종목의 누락 가격과 신규 종목의 전체 가격을 일괄 수집합니다.

        Args:
            year (int): 기준 연도.
            filtered (list): 대상 종목 목록.
            existing_codes (set): 이미 저장소에 존재하는 종목 코드 집합.
            missing_dates (list): 수집이 필요한 누락 날짜 목록.

        Returns:
            list[dict]: 수집된 모든 신규 가격 데이터 행 리스트.
        """
        all_new_rows: list[dict] = []
        known_stocks = [s for s in filtered if s.code in existing_codes]
        all_new_rows.extend(self._collect_incremental_prices(year, missing_dates, known_stocks))

        new_stocks = [s for s in filtered if s.code not in existing_codes]
        all_new_rows.extend(self._collect_new_stock_prices(year, new_stocks))
        return all_new_rows

    def _collect_incremental_prices(self, year: int, missing_dates: list[str], known_stocks: list) -> list[dict]:
        """이미 알고 있는 종목들에 대해 부족한 날짜의 시세를 시장 전종목 조회를 통해 채웁니다.

        Args:
            year (int): 기준 연도.
            missing_dates (list[str]): 누락된 날짜 리스트.
            known_stocks (list): 이미 알고 있는 종목 목록.

        Returns:
            list[dict]: 추출된 가격 레코드 목록.
        """
        all_new_rows: list[dict] = []
        if not missing_dates or not known_stocks:
            return all_new_rows
        # 동일 종목 다중 지정 시 시세가 여러 번 수집되어 병합되는 현상 방지용 고유 필터링
        unique_stocks = list({s.code: s for s in known_stocks}.values())
        adapter = PyKRXAdapter()
        for date_str in missing_dates:
            date_dt = datetime.strptime(date_str, "%Y-%m-%d")
            market_df = adapter.get_daily_market_ohlcv(date_dt)
            if not market_df.empty:
                all_new_rows.extend(self._extract_prices_from_market(year, date_str, market_df, unique_stocks))
        return all_new_rows

    def _extract_prices_from_market(self, year: int, date_str: str, market_df: pd.DataFrame, known_stocks: list) -> list[dict]:
        """시장 전체 시세 데이터프레임에서 관심 종목들의 가격 정보만 추출합니다.

        Args:
            year (int): 기준 연도.
            date_str (str): 대상 날짜 문자열.
            market_df (pd.DataFrame): 시장 전체 시세 데이터.
            known_stocks (list): 추출 대상 종목 목록.

        Returns:
            list[dict]: 추출된 행 리스트.
        """
        rows = []
        for stock_info in known_stocks:
            code = stock_info.code
            if code in market_df.index:
                close = int(market_df.loc[code].get("종가", 0))
                if close > 0:
                    change_rate = float(market_df.loc[code].get("등락률", 0.0))
                    rows.append(self._make_row(year, stock_info, date_str, close, change_rate))
        return rows

    def _collect_new_stock_prices(self, year: int, new_stocks: list) -> list[dict]:
        """새로 발견된 종목들에 대해 개별 종목 시세 조회를 통해 전체 시세를 수집합니다.

        Args:
            year (int): 기준 연도.
            new_stocks (list): 신규 종목 목록.

        Returns:
            list[dict]: 수집된 가격 레코드 리스트.
        """
        all_new_rows: list[dict] = []
        if not new_stocks:
            return all_new_rows
        new_prices_map = collect_daily_prices_batch(new_stocks, trading_days_after_release=self.trading_days_after_release)
        for stock_info in new_stocks:
            for dp in new_prices_map.get(stock_info.code, []):
                all_new_rows.append(self._make_row(year, stock_info, dp.date.strftime("%Y-%m-%d"), int(dp.close), dp.change_rate))
        return all_new_rows

    def _step6_merge_save(self, year: int, filtered: list, all_new_rows: list[dict], existing_stocks: list, existing_prices: dict) -> bool:
        """[Step] 수집된 신규 데이터를 기존 데이터와 병합하고 파일로 저장하며 리포트를 갱신합니다.

        Args:
            year (int): 기준 연도.
            filtered (list): 실시간 필터링된 전체 종목 목록.
            all_new_rows (list): 이번에 새로 수집된 시세 레코드.
            existing_stocks (list): 기존 저장소에 있던 종목 리스트.
            existing_prices (dict): 기존 저장소에 있던 시세 맵.

        Returns:
            bool: 전체 과정 성공 여부.
        """
        if not all_new_rows:
            if not existing_stocks: return False
            final_stocks, final_prices = existing_stocks, existing_prices
        else:
            self._save_incremental_dfs(year, filtered, all_new_rows)
            final_stocks, final_prices = self.repository.load_year(year)

        wb = self.excel_exporter.export(year, final_stocks, final_prices)
        self.storage.save_workbook(wb, os.path.join(self.output_dir, f"투자경고종목분석({year}년).xlsx"))
        return True

    def _save_incremental_dfs(self, year: int, filtered: list, all_new_rows: list[dict]) -> None:
        """증분 결과물을 Parquet에 병합 저장합니다."""
        stk_map, prc_map = self._build_incremental_maps(filtered, all_new_rows)
        self.storage.ensure_directory(self.output_dir)
        self.repository.append_year(year, list(stk_map.values()), prc_map)

    def _build_incremental_maps(self, filtered, new_rows) -> tuple[dict, dict]:
        """증분 수집 행들을 기반으로 저장소 업데이트용 도메인 객체 맵을 구축합니다.

        Args:
            filtered (list): 실시간 종목 리스트.
            new_rows (list): 신규 수집된 시세 데이터 행.

        Returns:
            tuple[dict, dict]: (종합 종목 맵, 신규 시세 데이터 맵)
        """
        stk_map = {s.code: s for s in filtered}
        prc_map: dict[str, list[DailyPriceData]] = {}
        for r in new_rows:
            if r["code"] in stk_map:
                dp = DailyPriceData(code=r["code"], name=r["name"], date=datetime.strptime(r["date"], "%Y-%m-%d"), close=float(r["close"]), change_rate=float(r["change_rate"]))
                prc_map.setdefault(r["code"], []).append(dp)
        return stk_map, prc_map

    def collect_today(self, end_date: str, days: int = 1, include_active: bool = True) -> CollectionResult:
        """지정된 날짜 기준의 증분(Incremental) 시세를 수집하고 분석 리포트를 갱신합니다.

        일일 스케줄러에서 주로 호출되며, 해제일 동기화와 신규 종목 감지를 함께 수행합니다.

        Args:
            end_date (str): 수집 기준일 (YYYY-MM-DD).
            days (int): 오늘을 포함하여 소급할 영업일수. 기본값 1.
            include_active (bool): 현재 경고 중인 종목을 결과에 포함할지 여부.

        Returns:
            CollectionResult: 수집/업로드 결과와 변경 규모(discovered/new_stocks/new_price_rows)를
                담은 값 객체.
        """
        logger, year, end_dt = setup_logging(end_date), int(end_date[:4]), datetime.strptime(end_date, "%Y-%m-%d")

        # DB SSOT 세션 시작: 원격(Drive)에 더 최신 DB가 있으면 로컬 작업 사본으로 받아온다
        # (db_ssot_guide.md §6). 로컬 storage는 repository와 동일 파일을 가리키므로 no-op이다.
        self._sync_db_down(year, logger)
        self._sync_db_down(year - 1, logger)

        try:
            try:
                all_filtered, filtered = self._step1_fetch_stocks(year, end_date, include_active)
            except Exception as e:
                logger.error(f"KRX 조회 실패: {e}")
                return CollectionResult(success=False, reason=f"KRX 조회 실패: {e}")

            if not all_filtered:
                return CollectionResult(success=False, reason="대상 기간 내 투자경고종목이 없습니다.")

            existing_stocks, existing_prices = self._step2_sync_and_load(year, all_filtered, logger)
            existing_codes = {s.code for s in existing_stocks}
            new_stock_count = sum(1 for s in filtered if s.code not in existing_codes)

            target_dates, missing_dates = self._step3_get_dates(end_dt, days, existing_prices)

            has_new_data = bool(missing_dates) or new_stock_count > 0
            excel_path = os.path.join(self.output_dir, f"투자경고종목분석({year}년).xlsx")
            excel_exists = self.storage.path_exists(excel_path)

            if not has_new_data and excel_exists:
                logger.info(f"  [증분 건너뜀] 수집할 누락 시세가 없고, 엑셀 리포트가 이미 존재합니다. ({excel_path})")
                return CollectionResult(success=True, discovered=len(all_filtered), reason="변경 없음(증분 건너뜀)")
            elif not excel_exists:
                logger.info(f"  [파일 누락] 대상 엑셀 리포트 미존재: ({excel_path}). 리포트를 재생성합니다.")

            all_new_rows = self._step45_collect_prices(year, filtered, existing_codes, missing_dates)
            saved = self._step6_merge_save(year, filtered, all_new_rows, existing_stocks, existing_prices)
            return CollectionResult(
                success=saved,
                discovered=len(all_filtered),
                new_stocks=new_stock_count,
                new_price_rows=len(all_new_rows),
                reason="" if saved else "저장/업로드 실패",
            )
        finally:
            # 이번 실행에서 실제로 쓰기 작업을 받을 수 있는 저장 단위(올해·작년 DB 파일)를
            # 결과와 무관하게 항상 업로드한다 - release_date 동기화만 일어나고 조기
            # 반환되는 경로에서도 로컬 변경분이 원격에 반영되도록 한다(orchestration_guide.md §3).
            self._sync_db_up(year, logger)
            self._sync_db_up(year - 1, logger)

    def _sync_db_down(self, year: int, logger: logging.Logger) -> None:
        """DB SSOT(Drive)의 연도별 DB 파일을 로컬 작업 사본으로 받아온다.

        storage가 LocalStorageAdapter면 get_file()이 이미 repository와 같은 파일을
        가리키므로 로컬 파일에 그대로 다시 쓰는 건 자기 자신을 덮어쓰는 no-op이다.
        storage가 GoogleDriveAdapter일 때만 실질적인 원격->로컬 다운로드가 된다.
        """
        db_path = getattr(self.repository, "db_path", None)
        if db_path is None:
            return
        local_path = db_path(year)
        remote_path = str(local_path).replace("\\", "/")
        try:
            data = self.storage.get_file(remote_path)
            if data is not None:
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(data)
        except Exception as e:
            logger.warning(f"  [DB 동기화 건너뜀] {year}년 DB 다운로드 실패(로컬 사본 유지): {e}")

    def _sync_db_up(self, year: int, logger: logging.Logger) -> None:
        """로컬 작업 사본의 연도별 DB 파일을 DB SSOT(Drive)로 업로드한다."""
        db_path = getattr(self.repository, "db_path", None)
        if db_path is None:
            return
        local_path = db_path(year)
        if not local_path.exists():
            return
        remote_path = str(local_path).replace("\\", "/")
        try:
            self.storage.put_file(remote_path, local_path.read_bytes())
        except Exception as e:
            logger.warning(f"  [DB 동기화 실패] {year}년 DB 업로드 실패: {e}")


class ReportGenerationService:
    """백업된 Parquet 바이너리 블록 저장소 원본을 읽어 서식적 엑셀 문서를 합성하고 발행/배포하는 서비스."""

    def __init__(
        self,
        repository: WarningStockRepository,
        storage: StoragePort,
        output_dir: str = "output",
        trading_days_after_release: int = 3,
    ):
        """ReportGenerationService 초기화.

        Args:
            repository (WarningStockRepository): 데이터 원천이 담겨 있는 레파지토리 규격.
            storage (StoragePort): 산출물(리포트)를 기록 저장할 I/O 공간 어댑터.
            output_dir (str): 파일들의 종착지 폴더.
            trading_days_after_release (int): 워닝 일수 차트 생성용 시트 최대 영업 컬럼. (기본 3)
        """
        self.repository = repository
        self.storage = storage
        self.output_dir = output_dir
        self.excel_exporter = WarningExcelExporter(trading_days_after_release=trading_days_after_release)

    def generate_excel_report(self, year: int) -> bool:
        """지정한 연착 연도의 파켓 복합 뷰어를 호출하여 .xlsx 포맷 리포트를 강제 단독 재생산합니다.

        과거 연도 서식이나 누락 복구시 유용합니다.

        Args:
            year (int): 대상 파일 추출 연도(예시: 2025).

        Returns:
            bool: 성공적 파싱과 서식 기입, 저장이 성사 되었을 경우 True 반환.
        """
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
