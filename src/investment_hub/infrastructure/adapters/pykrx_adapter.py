"""PyKRX 어댑터 모듈: pykrx 라이브러리를 통해 한국거래소(KRX) 시장 데이터를 수집합니다.

로컬 파일시스템(JSON)을 활용한 하이브리드 캐싱 전략을 구현하여 API 호출비용을 낮춥니다:
1. 날짜별 종목 리스트 캐싱
2. 종목별 OHLCV(시가/고가/저가/종가/거래량) 데이터 캐싱
"""

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from pykrx import stock


class PyKRXAdapter:
    """pykrx 오픈소스 API를 래핑하여 주식 시장 데이터를 가져오는 외부 어댑터.

    내부적으로 캐시 디렉토리를 운용하여 동일한 일자/종목에 대한 반복적인 HTTP 호출을 방지합니다.
    """

    def __init__(self, cache_dir: str = "cache/pykrx"):
        self.cache_dir = Path(cache_dir)
        self.ticker_cache_dir = self.cache_dir / "tickers"
        self.ohlcv_cache_dir = self.cache_dir / "ohlcv"
        self.mapping_cache_dir = self.cache_dir / "mappings"
        self.market_daily_cache_dir = self.cache_dir / "market_daily"

        self.ticker_cache_dir.mkdir(parents=True, exist_ok=True)
        self.ohlcv_cache_dir.mkdir(parents=True, exist_ok=True)
        self.mapping_cache_dir.mkdir(parents=True, exist_ok=True)
        self.market_daily_cache_dir.mkdir(parents=True, exist_ok=True)

    def get_ticker_list(self, date: datetime, market: str = "ALL", use_cache: bool = True) -> list[str]:
        """지정된 날짜에 상장된 종목들의 티커(종목코드) 목록을 가져옵니다.

        Args:
            date (datetime): 상장 종목을 조회할 기준 날짜.
            market (str): 조회 대상 시장 구분. 기본값 "ALL". ("KOSPI", "KOSDAQ", "KONEX", "ALL" 중 택 1)
            use_cache (bool): 로컬 디스크 캐시 사용 여부.

        Returns:
            list[str]: 수집된 6자리 종목코드 문자열 원소들의 목록. 에러 발생 시 빈 리스트 반환.
        """
        date_str = date.strftime("%Y%m%d")
        cache_file = self.ticker_cache_dir / f"{date_str}_{market}.json"

        # 캐시 확인
        if use_cache and cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as f:
                    tickers = json.load(f)
                print(f"  [TICKER CACHE HIT] {len(tickers)} tickers for {date_str}")
                return tickers
            except Exception as e:
                print(f"  [TICKER CACHE ERROR] {e}")

        # API 호출
        print(f"  [TICKER API] Fetching ticker list for {date_str}...")

        try:
            if market == "ALL":
                tickers = []
                for mkt in ["KOSPI", "KOSDAQ", "KONEX"]:
                    try:
                        mkt_tickers = stock.get_market_ticker_list(date_str, market=mkt)
                        tickers.extend(mkt_tickers)
                        print(f"    - {mkt}: {len(mkt_tickers)} tickers")
                    except Exception as e:
                        print(f"    - {mkt}: Error - {e}")
            else:
                tickers = stock.get_market_ticker_list(date_str, market=market)

            print(f"  [TICKER SUCCESS] Total {len(tickers)} tickers")

            # 캐시 저장
            if use_cache and tickers:
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(tickers, f, ensure_ascii=False, indent=2)
                    print(f"  [TICKER CACHED] {len(tickers)} tickers saved")
                except Exception as e:
                    print(f"  [TICKER CACHE SAVE ERROR] {e}")

            return tickers

        except Exception as e:
            print(f"  [TICKER API ERROR] {e}")
            return []

    def get_ticker_name_mapping(self, date: datetime, market: str = "ALL", use_cache: bool = True) -> dict[str, str]:
        """특정 영업일에 유효한 종목명 → 종목코드(티커) 매핑 딕셔너리를 생성하거나 가져옵니다.

        Args:
            date (datetime): 조회 기준 날짜.
            market (str): 시장 구분. 기본값 "ALL". ("KOSPI", "KOSDAQ", "KONEX", "ALL")
            use_cache (bool): 맵핑 결과 캐시 사용 여부.

        Returns:
            dict[str, str]: "삼성전자" -> "005930" 형태의 데이터를 가지는 딕셔너리.
        """
        date_str = date.strftime("%Y%m%d")
        cache_file = self.mapping_cache_dir / f"{date_str}_{market}.json"

        # 캐시 확인
        if use_cache and cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as f:
                    mapping = json.load(f)
                print(f"  [MAPPING CACHE HIT] {len(mapping)} name-to-code mappings for {date_str}")
                return mapping
            except Exception as e:
                print(f"  [MAPPING CACHE ERROR] {e}")

        # 종목 리스트 가져오기
        print(f"  [MAPPING API] Building name-to-code mapping for {date_str}...")
        tickers = self.get_ticker_list(date, market, use_cache)

        if not tickers:
            print(f"  [MAPPING ERROR] No tickers found for {date_str}")
            return {}

        # 종목명→종목코드 매핑 생성
        mapping = {}
        for ticker in tickers:
            try:
                name = stock.get_market_ticker_name(ticker)
                if name:
                    mapping[name] = ticker
            except Exception as e:
                print(f"  [MAPPING ERROR] Failed to get name for {ticker}: {e}")

        print(f"  [MAPPING SUCCESS] Created {len(mapping)} name-to-code mappings")

        # 캐시 저장
        if use_cache and mapping:
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(mapping, f, ensure_ascii=False, indent=2)
                print(f"  [MAPPING CACHED] {len(mapping)} mappings saved")
            except Exception as e:
                print(f"  [MAPPING CACHE SAVE ERROR] {e}")

        return mapping

    def get_stock_ohlcv(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        use_cache: bool = True,
        ticker_name: str | None = None,
    ) -> pd.DataFrame:
        """단일 종목에 대한 특정 기간 동안의 OHLCV(시고저종, 거래량) 일별 데이터를 조회합니다.

        Args:
            ticker (str): 조회할 종목코드.
            start_date (datetime): 조회 시작일.
            end_date (datetime): 조회 종료일.
            use_cache (bool): 데이터 캐싱 기능 사용 여부.
            ticker_name (str | None): 캐시 파일명 작성을 위해 사용될 종목명. (생략 시 API로 조회됨)

        Returns:
            pd.DataFrame: 생성된 날짜가 인덱스에 포함된 pandas DataFrame.
                컬럼 구성: [시가, 고가, 저가, 종가, 거래량]
                조회 실패나 기간 내 데이터가 없는 경우 빈 DataFrame 반환.
        """
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")

        # 종목명이 제공되지 않으면 API로 조회
        if ticker_name is None:
            try:
                ticker_name = stock.get_market_ticker_name(ticker)
            except Exception as e:
                print(f"  [NAME LOOKUP ERROR] {ticker}: {e}")
                ticker_name = ticker  # 실패 시 종목코드 사용

        # 종목명 기반 캐시 파일
        name_cache_file = self.ohlcv_cache_dir / f"{ticker_name}_{start_str}_{end_str}.json"

        # 종목코드 기반 캐시 파일 (기존 호환성)
        code_cache_file = self.ohlcv_cache_dir / f"{ticker}_{start_str}_{end_str}.json"

        # 캐시 확인 (종목명 우선, 없으면 종목코드)
        if use_cache:
            # 1. 종목명 기반 캐시 확인
            if name_cache_file.exists():
                try:
                    with open(name_cache_file, encoding="utf-8") as f:
                        cached_data = json.load(f)

                    if cached_data:
                        df = pd.DataFrame(cached_data)
                        df["날짜"] = pd.to_datetime(df["날짜"])
                        df.set_index("날짜", inplace=True)
                        return df
                except Exception as e:
                    print(f"  [OHLCV CACHE ERROR] {ticker_name}: {e}")

            # 2. 종목코드 기반 캐시 확인 (기존 호환성)
            elif code_cache_file.exists():
                try:
                    with open(code_cache_file, encoding="utf-8") as f:
                        cached_data = json.load(f)

                    if cached_data:
                        df = pd.DataFrame(cached_data)
                        df["날짜"] = pd.to_datetime(df["날짜"])
                        df.set_index("날짜", inplace=True)
                        print(f"  [LEGACY CACHE HIT] Using code-based cache for {ticker}")
                        return df
                except Exception as e:
                    print(f"  [OHLCV CACHE ERROR] {ticker}: {e}")

        # API 호출
        try:
            df = stock.get_market_ohlcv(start_str, end_str, ticker)

            # 캐시 저장 (종목명 기반으로만 저장)
            if use_cache and not df.empty:
                try:
                    df_to_save = df.reset_index()
                    df_to_save.columns = ["날짜"] + list(df.columns)
                    df_to_save["날짜"] = df_to_save["날짜"].astype(str)
                    cache_data = df_to_save.to_dict("records")

                    with open(name_cache_file, "w", encoding="utf-8") as f:
                        json.dump(cache_data, f, ensure_ascii=False, indent=2)
                    print(f"  [OHLCV CACHED] {ticker_name} ({ticker})")
                except Exception as e:
                    print(f"  [OHLCV CACHE SAVE ERROR] {ticker_name}: {e}")

            return df

        except Exception as e:
            print(f"  [OHLCV API ERROR] {ticker}: {e}")
            return pd.DataFrame()

    def get_trading_days(self, start_date: datetime, end_date: datetime) -> list[datetime]:
        """해당 기간 내 증권시장이 실제로 열린 영업일(개장일) 목록을 가져옵니다.

        내부적으로 휴장이 드문 대표 종목(삼성전자)의 주가 기록이 위치한 일자 데이터를 역산하여 판단합니다.

        Args:
            start_date (datetime): 거래일 탐색 기간 시작일.
            end_date (datetime): 거래일 탐색 기간 종료일.

        Returns:
            list[datetime]: 시작일과 종료일 사이에 속하는 실제 영업일들의 리스트.
        """
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")

        try:
            # 삼성전자(005930)를 기준으로 거래일 목록 조회
            df = stock.get_market_ohlcv_by_date(start_str, end_str, "005930")
            return [d.to_pydatetime() for d in df.index]
        except Exception as e:
            print(f"  [TRADING DAYS ERROR] {e}")
            return []

    def get_daily_market_ohlcv(self, date: datetime, market: str = "ALL", use_cache: bool = True) -> pd.DataFrame:
        """지정된 단일 날짜에 대해 해당 시장 내 상장된 모든 종목의 OHLCV 단면 데이터를 조회합니다.

        Args:
            date (datetime): 시장 전체 주가를 조회할 영업일 시점.
            market (str): 조회할 시장 구분 (예: KOSPI, KOSDAQ).
            use_cache (bool): 디스크 데이터 캐시 사용 여부.

        Returns:
            pd.DataFrame:
                인덱스: 종목코드 (티커/ticker)
                컬럼: [시가, 고가, 저가, 종가, 거래량, 등락률 ...]
                만약 해당 일자가 휴장일이거나 데이터가 유효하지 않으면 빈 DataFrame이 반환됩니다.
        """
        date_str = date.strftime("%Y%m%d")
        cache_file = self.market_daily_cache_dir / f"{date_str}_{market}.json"

        # 캐시 확인
        if use_cache and cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as f:
                    cached_data = json.load(f)

                if cached_data:
                    # JSON -> DataFrame (orient='records'로 저장했을 경우 재구성 필요하지만,
                    # 여기서는 Ticker가 Index여야 하므로 orient='index'나 'split'이 적절할 수 있음.
                    # 하지만 편의상 reset_index() 후 records로 저장하고 다시 불러와서 set_index() 하는 방식을 사용)
                    df = pd.DataFrame(cached_data)
                    if "티커" in df.columns:
                        df.set_index("티커", inplace=True)
                    elif "ticker" in df.columns:
                        df.set_index("ticker", inplace=True)

                    # 숫자형 변환 (JSON 로드 시 문자열로 될 수 있음)
                    for col in ["시가", "고가", "저가", "종가", "거래량"]:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors="coerce")

                    return df
            except Exception as e:
                print(f"  [MARKET DAILY CACHE ERROR] {date_str}: {e}")

        # API 호출
        try:
            # get_market_ohlcv_by_ticker는 해당 일자의 전 종목 시세를 가져옴
            # market="ALL"일 경우 KOSPI, KOSDAQ, KONEX 반복 호출 필요할 수 있음 (pykrx 버전에 따라 다름)
            # 최신 pykrx는 market="ALL" 지원함.

            df = stock.get_market_ohlcv_by_ticker(date_str, market=market)

            # 유효성 검사: 데이터의 50% 이상이 종가 0이면 휴장일 또는 무효 데이터로 간주
            if not df.empty and "종가" in df.columns:
                zero_count = (df["종가"] == 0).sum()
                if zero_count > len(df) * 0.5:
                    # print(f"  [MARKET DAILY INVALID] {date_str}: Too many zero closes ({zero_count}/{len(df)})")
                    return pd.DataFrame()

            # 캐시 저장
            if use_cache and not df.empty:
                try:
                    df_to_save = df.reset_index()
                    # 인덱스 이름이 없을 수 있으므로 지정
                    if "티커" not in df_to_save.columns and "ticker" not in df_to_save.columns:
                        df_to_save.rename(columns={df_to_save.columns[0]: "티커"}, inplace=True)

                    cache_data = df_to_save.to_dict("records")

                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(cache_data, f, ensure_ascii=False, indent=2)
                    # print(f"  [MARKET DAILY CACHED] {date_str} ({len(df)} stocks)")
                except Exception as e:
                    print(f"  [MARKET DAILY CACHE SAVE ERROR] {date_str}: {e}")

            return df

        except Exception:
            # 휴장일이거나 데이터가 없는 경우 에러가 발생할 수 있음
            # print(f"  [MARKET DAILY API ERROR] {date_str}: {e}")
            return pd.DataFrame()


if __name__ == "__main__":
    # 테스트
    adapter = PyKRXAdapter()

    test_date = datetime(2026, 2, 12)
    print(f"Testing PyKRXAdapter for {test_date.strftime('%Y-%m-%d')}\n")

    # Test 1: 종목 리스트
    tickers = adapter.get_ticker_list(test_date, market="KOSDAQ", use_cache=True)
    print(f"KOSDAQ tickers: {len(tickers)}")

    # Test 2: 매일 전체 시세
    df = adapter.get_daily_market_ohlcv(test_date)
    print(f"Market OHLCV shape: {df.shape}")
