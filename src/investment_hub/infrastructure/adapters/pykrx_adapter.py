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
        """지정된 날짜에 상장된 종목들의 티커 목록을 가져옵니다."""
        d_str = date.strftime("%Y%m%d")
        cache_path = self.ticker_cache_dir / f"{d_str}_{market}.json"
        
        if use_cache:
            cache = self._load_cache(cache_path, f"TICKER for {d_str}")
            if cache is not None: return cache

        tickers = self._fetch_tickers(d_str, market)
        if use_cache and tickers: self._save_cache(cache_path, tickers, "TICKER")
        return tickers

    def _fetch_tickers(self, date_str: str, market: str) -> list[str]:
        """지정된 날짜와 시장에 대해 KRX로부터 티커 목록을 실제 조회합니다.

        Args:
            date_str (str): 'YYYYMMDD' 형식의 날짜 문자열.
            market (str): 시장 구분 (KOSPI, KOSDAQ, KONEX, ALL).

        Returns:
            list[str]: 티커 목록 리스트.
        """
        print(f"  [TICKER API] Fetching for {date_str}...")
        try:
            if market != "ALL": return stock.get_market_ticker_list(date_str, market=market)
            all_tkrs = []
            for m in ["KOSPI", "KOSDAQ", "KONEX"]:
                try:
                    m_tkrs = stock.get_market_ticker_list(date_str, market=m)
                    all_tkrs.extend(m_tkrs)
                    print(f"    - {m}: {len(m_tkrs)} tickers")
                except Exception as e: print(f"    - {m}: Error - {e}")
            return all_tkrs
        except Exception as e: print(f"  [TICKER API ERROR] {e}"); return []

    def _load_cache(self, path: Path, label: str):
        """지정된 경로에서 JSON 캐시 파일을 읽어옵니다.

        Args:
            path (Path): 캐시 파일 경로.
            label (str): 로그 출력용 라벨.

        Returns:
            Any | None: 로드된 데이터 (list/dict), 파일이 없거나 오류 발생 시 None.
        """
        if not path.exists(): return None
        try:
            with open(path, encoding="utf-8") as f: data = json.load(f)
            print(f"  [CACHE HIT] {label}: {len(data)}")
            return data
        except Exception as e: print(f"  [CACHE ERROR] {label}: {e}"); return None

    def _save_cache(self, path: Path, data, label: str):
        """데이터를 JSON 형식으로 지정된 경로에 캐싱합니다.

        Args:
            path (Path): 캐시 파일 저장 경로.
            data (Any): 저장할 데이터.
            label (str): 로그 출력용 라벨.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"  [CACHED] {label} saved to {path.name}")
        except Exception as e: print(f"  [CACHE SAVE ERROR] {label}: {e}")

    def get_ticker_name_mapping(self, date: datetime, market: str = "ALL", use_cache: bool = True) -> dict[str, str]:
        """특정 영업일에 유효한 종목명 → 종목코드 매핑을 가져옵니다."""
        d_str = date.strftime("%Y%m%d")
        cache_path = self.mapping_cache_dir / f"{d_str}_{market}.json"
        
        if use_cache:
            cache = self._load_cache(cache_path, f"MAPPING for {d_str}")
            if cache is not None: return cache

        mapping = self._build_mapping(date, market, use_cache)
        if use_cache and mapping: self._save_cache(cache_path, mapping, "MAPPING")
        return mapping

    def _build_mapping(self, date: datetime, market: str, use_cache: bool) -> dict[str, str]:
        """티커 리스트를 순회하며 종목명 → 종목코드 매핑을 구축합니다.

        Args:
            date (datetime): 기준 일시.
            market (str): 시장 구분.
            use_cache (bool): 하위 티커 리스트 호출 시 캐시 사용 여부.

        Returns:
            dict[str, str]: 종목명을 키로, 코드를 값으로 갖는 사전형.
        """
        tickers = self.get_ticker_list(date, market, use_cache)
        if not tickers: return {}
        mapping = {}
        for tkr in tickers:
            try:
                name = stock.get_market_ticker_name(tkr)
                if name: mapping[name] = tkr
            except Exception as e: print(f"  [MAPPING ERROR] {tkr}: {e}")
        return mapping

    def get_stock_ohlcv(self, ticker: str, start_date: datetime, end_date: datetime, use_cache: bool = True, ticker_name: str | None = None) -> pd.DataFrame:
        """단일 종목의 특정 기간 OHLCV 데이터를 조회합니다."""
        s_str, e_str = start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")
        t_name = ticker_name or self._lookup_ticker_name(ticker)
        cache_path = self.ohlcv_cache_dir / f"{t_name}_{s_str}_{e_str}.json"
        
        if use_cache:
            cache = self._load_cache(cache_path, f"OHLCV for {t_name}")
            if cache is not None: return self._to_ohlcv_df(cache)

        df = self._fetch_stock_ohlcv(s_str, e_str, ticker)
        if use_cache and not df.empty:
            self._save_cache(cache_path, self._from_ohlcv_df(df), f"OHLCV for {t_name}")
        return df

    def _lookup_ticker_name(self, ticker: str) -> str:
        """티커 코드로부터 종목명을 조회합니다. 실패 시 티커를 그대로 반환합니다."""
        try: return stock.get_market_ticker_name(ticker) or ticker
        except: return ticker

    def _to_ohlcv_df(self, data: list) -> pd.DataFrame:
        """캐시(list) 데이터를 OHLCV 데이터프레임으로 복원하고 인덱스를 설정합니다."""
        df = pd.DataFrame(data)
        if "날짜" in df.columns:
            df["날짜"] = pd.to_datetime(df["날짜"])
            df.set_index("날짜", inplace=True)
        return df

    def _from_ohlcv_df(self, df: pd.DataFrame) -> list:
        """OHLCV 데이터프레임을 JSON 저장이 용이한 리스트 구조로 변환합니다."""
        df_save = df.reset_index()
        df_save.columns = ["날짜"] + list(df.columns)
        df_save["날짜"] = df_save["날짜"].astype(str)
        return df_save.to_dict("records")

    def _fetch_stock_ohlcv(self, start_str: str, end_str: str, ticker: str) -> pd.DataFrame:
        """KRX로부터 단일 종목의 OHLCV 데이터를 실제 조회합니다."""
        try: return stock.get_market_ohlcv(start_str, end_str, ticker)
        except Exception as e: print(f"  [OHLCV API ERROR] {ticker}: {e}"); return pd.DataFrame()

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
        """지정된 날짜의 시장 내 모든 종목 OHLCV 데이터를 조회합니다."""
        d_str = date.strftime("%Y%m%d")
        cache_path = self.market_daily_cache_dir / f"{d_str}_{market}.json"
        
        if use_cache:
            cache = self._load_cache(cache_path, f"MARKET DAILY for {d_str}")
            if cache is not None: return self._to_market_df(cache)

        df = self._fetch_market_ohlcv(d_str, market)
        if use_cache and not df.empty: self._save_cache(cache_path, self._from_market_df(df), f"MARKET DAILY")
        return df

    def _to_market_df(self, data: list) -> pd.DataFrame:
        """캐시된 시장 전체 시세 리스트를 데이터프레임으로 복원하고 타입을 정제합니다."""
        df = pd.DataFrame(data)
        ticker_col = next((c for c in ["티커", "ticker"] if c in df.columns), None)
        if ticker_col: df.set_index(ticker_col, inplace=True)
        for col in ["시가", "고가", "저가", "종가", "거래량"]:
            if col in df.columns: df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def _from_market_df(self, df: pd.DataFrame) -> list:
        """시장 전체 시세 데이터프레임을 JSON용 리스트로 변환합니다."""
        df_save = df.reset_index()
        if not any(c in df_save.columns for c in ["티커", "ticker"]):
            df_save.rename(columns={df_save.columns[0]: "티커"}, inplace=True)
        return df_save.to_dict("records")

    def _fetch_market_ohlcv(self, date_str: str, market: str) -> pd.DataFrame:
        """KRX로부터 전종목 OHLCV 데이터를 실제 조회합니다."""
        try:
            df = stock.get_market_ohlcv_by_ticker(date_str, market=market)
            return df if self._is_market_ohlcv_valid(df) else pd.DataFrame()
        except: return pd.DataFrame()

    def _is_market_ohlcv_valid(self, df: pd.DataFrame) -> bool:
        """조회된 전종목 시세 데이터의 유효성을 검증합니다 (0원 종가 과다 여부 확인)."""
        if df.empty or "종가" not in df.columns: return False
        return (df["종가"] == 0).sum() <= len(df) * 0.5


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
