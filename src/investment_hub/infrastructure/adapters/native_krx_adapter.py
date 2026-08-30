"""pykrx 없이 KRX 정보데이터시스템(data.krx.co.kr)을 직접 스크래핑하는 공통 어댑터.

pykrx는 2026-04 KRX 접속 방식 변경(로그인 필수화) 이후 우리 프로젝트들이 쓰던 버전에서
전부 깨졌고(https://github.com/sharebook-kr/pykrx/issues/278, #286), 서드파티 라이브러리에
의존하는 방식 자체가 이 생태계의 다른 프로젝트들(weekly_gainers, ceiling-tracker,
new_stock_crawler)과도 어긋난다 - 그 프로젝트들은 전부 직접 로그인 세션을 얻어 KRX API를
호출하는 자체 어댑터를 쓴다. 이 파일은 ceiling-tracker의 KrxDirectStockInfoAdapter 로그인
흐름을 기반으로, PyKRXAdapter와 동일한 퍼블릭 인터페이스(get_ticker_list,
get_ticker_name_mapping, get_stock_ohlcv, get_trading_days, get_daily_market_ohlcv)를
제공해 기존 호출부를 그대로 유지한 채 드롭인 교체할 수 있게 만든 것이다.

Projects/handoff_guide.md §4(공통 유틸리티, 벤더링 방식) 참고 - 이 파일을 프로젝트별
infrastructure/adapters 계층에 그대로 복사해 쓴다.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)


class NativeKrxAdapter:
    """KRX 정보데이터시스템에 직접 로그인해 시세 데이터를 수집하는 어댑터.

    로컬 파일시스템(JSON) 캐싱을 PyKRXAdapter와 동일하게 유지한다.
    """

    BASE_URL = "https://data.krx.co.kr"
    _UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    def __init__(self, cache_dir: str = "cache/pykrx", mbr_id: Optional[str] = None, pw: Optional[str] = None):
        self.cache_dir = Path(cache_dir)
        self.ticker_cache_dir = self.cache_dir / "tickers"
        self.ohlcv_cache_dir = self.cache_dir / "ohlcv"
        self.mapping_cache_dir = self.cache_dir / "mappings"
        self.market_daily_cache_dir = self.cache_dir / "market_daily"
        for d in (self.ticker_cache_dir, self.ohlcv_cache_dir, self.mapping_cache_dir, self.market_daily_cache_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.mbr_id = mbr_id or os.getenv("KRX_USERNAME")
        self.pw = pw or os.getenv("KRX_PASSWORD")
        if not self.mbr_id or not self.pw:
            raise ValueError("KRX_USERNAME 또는 KRX_PASSWORD 환경변수가 설정되지 않았습니다.")

        self.session = requests.Session()
        self._logged_in = False
        self._last_request_at: float = 0.0

    # ------------------------------------------------------------------
    # 레이트리밋 - KRX가 "비정상 대량 조회"로 판단해 IP를 차단하는 걸 막기 위해
    # data.krx.co.kr로 나가는 모든 요청 사이에 최소 1초 간격을 강제한다. 실제로
    # 이 딜레이 없이 짧은 시간에 수십 건이 몰려 24시간 IP 차단을 맞은 적이 있다.
    # ------------------------------------------------------------------

    MIN_REQUEST_INTERVAL_SECONDS = 1.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.MIN_REQUEST_INTERVAL_SECONDS:
            time.sleep(self.MIN_REQUEST_INTERVAL_SECONDS - elapsed)
        self._last_request_at = time.monotonic()

    # ------------------------------------------------------------------
    # 로그인
    # ------------------------------------------------------------------

    def _ensure_login(self) -> None:
        if self._logged_in:
            return
        login_page = f"{self.BASE_URL}/contents/MDC/COMS/client/MDCCOMS001.cmd"
        login_jsp = f"{self.BASE_URL}/contents/MDC/COMS/client/view/login.jsp?site=mdc"
        login_url = f"{self.BASE_URL}/contents/MDC/COMS/client/MDCCOMS001D1.cmd"

        self._throttle()
        self.session.get(login_page, headers={"User-Agent": self._UA}, timeout=15)
        self._throttle()
        self.session.get(login_jsp, headers={"User-Agent": self._UA, "Referer": login_page}, timeout=15)

        payload = {"mbrNm": "", "telNo": "", "di": "", "certType": "", "mbrId": self.mbr_id, "pw": self.pw}
        headers = {"User-Agent": self._UA, "Referer": login_page}
        self._throttle()
        resp = self.session.post(login_url, data=payload, headers=headers, timeout=15)
        data = resp.json()
        if data.get("_error_code") == "CD011":
            payload["skipDup"] = "Y"
            self._throttle()
            resp = self.session.post(login_url, data=payload, headers=headers, timeout=15)
            data = resp.json()

        if data.get("_error_code") != "CD001":
            raise RuntimeError(f"[NativeKrxAdapter] KRX 로그인 실패: {data}")

        self.session.cookies.set("mdc.client_session", "true", domain="data.krx.co.kr")
        self.session.cookies.set("lang", "ko_KR", domain="data.krx.co.kr")
        self._logged_in = True

    # ------------------------------------------------------------------
    # 캐시 헬퍼 (PyKRXAdapter와 동일한 파일 레이아웃)
    # ------------------------------------------------------------------

    def _load_cache(self, path: Path, label: str):
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                logger.info(f"  [CACHED] {label} loaded from {path.name}")
                return json.load(f)
        except Exception:
            return None

    def _save_cache(self, path: Path, data, label: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"  [CACHED] {label} saved to {path.name}")
        except Exception as e:
            logger.warning(f"  [CACHE SAVE ERROR] {label}: {e}")

    # ------------------------------------------------------------------
    # 전종목 시세 스냅샷 (이름/코드/OHLCV 모두 이 한 호출에서 나옴)
    # ------------------------------------------------------------------

    def _fetch_all_markets(self, date_str: str) -> list[dict]:
        self._ensure_login()
        url = f"{self.BASE_URL}/comm/bldAttendant/getJsonData.cmd"
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": self.BASE_URL,
            "Referer": f"{self.BASE_URL}/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201",
            "User-Agent": self._UA,
            "X-Requested-With": "XMLHttpRequest",
        }
        payload = {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
            "locale": "ko_KR",
            "mktId": "ALL",
            "trdDd": date_str,
            "share": "1",
            "money": "1",
            "csvxls_isNo": "false",
        }
        self._throttle()
        resp = self.session.post(url, headers=headers, data=payload, timeout=30)
        if resp.status_code != 200:
            logger.error(f"  [MARKET SNAPSHOT ERROR] status={resp.status_code} date={date_str}")
            return []
        data = resp.json()
        return data.get("OutBlock_1") or data.get("output") or []

    @staticmethod
    def _num(val: str) -> float:
        try:
            return float(str(val).replace(",", ""))
        except (ValueError, TypeError):
            return 0.0

    # ------------------------------------------------------------------
    # PyKRXAdapter 드롭인 호환 퍼블릭 API
    # ------------------------------------------------------------------

    def get_ticker_list(self, date: datetime, market: str = "ALL", use_cache: bool = True) -> list[str]:
        """지정된 날짜에 상장된 종목들의 티커 목록을 가져옵니다."""
        d_str = date.strftime("%Y%m%d")
        cache_path = self.ticker_cache_dir / f"{d_str}_{market}.json"
        if use_cache:
            cache = self._load_cache(cache_path, f"TICKER for {d_str}")
            if cache is not None:
                return cache

        rows = self._fetch_all_markets(d_str)
        if market != "ALL":
            rows = [r for r in rows if r.get("MKT_NM", "").upper().startswith(market[:4])]
        tickers = [r.get("ISU_SRT_CD", "") for r in rows if r.get("ISU_SRT_CD")]
        if use_cache and tickers:
            self._save_cache(cache_path, tickers, "TICKER")
        return tickers

    def get_ticker_name_mapping(self, date: datetime, market: str = "ALL", use_cache: bool = True) -> dict[str, str]:
        """특정 영업일에 유효한 종목명 -> 종목코드 매핑을 가져옵니다."""
        d_str = date.strftime("%Y%m%d")
        cache_path = self.mapping_cache_dir / f"{d_str}_{market}.json"
        if use_cache:
            cache = self._load_cache(cache_path, f"MAPPING for {d_str}")
            if cache is not None:
                return cache

        rows = self._fetch_all_markets(d_str)
        mapping = {r.get("ISU_ABBRV", ""): r.get("ISU_SRT_CD", "") for r in rows if r.get("ISU_ABBRV") and r.get("ISU_SRT_CD")}
        if use_cache and mapping:
            self._save_cache(cache_path, mapping, "MAPPING")
        return mapping

    def get_stock_ohlcv(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
        use_cache: bool = True,
        ticker_name: Optional[str] = None,
    ) -> pd.DataFrame:
        """단일 종목의 특정 기간 OHLCV 데이터를 네이버 금융 차트 API로 조회합니다.

        KRX 로그인 세션이 필요 없는 공개 API라 별도 인증 없이 바로 호출한다
        (ceiling-tracker의 fetch_ohlcv_bulk와 동일한 방식).
        """
        s_str, e_str = start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")
        t_name = ticker_name or ticker
        cache_path = self.ohlcv_cache_dir / f"{t_name}_{s_str}_{e_str}.json"

        if use_cache:
            cache = self._load_cache(cache_path, f"OHLCV for {t_name}")
            if cache is not None:
                return self._to_ohlcv_df(cache)

        df = self._fetch_naver_ohlcv(ticker, start_date, end_date)
        if use_cache and not df.empty:
            self._save_cache(cache_path, self._from_ohlcv_df(df), f"OHLCV for {t_name}")
        return df

    def _fetch_naver_ohlcv(self, ticker: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
        import xml.etree.ElementTree as ET

        url = f"https://fchart.stock.naver.com/sise.nhn?symbol={ticker}&timeframe=day&count=3650&requestType=0"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            records = []
            for item in root.findall(".//item"):
                data = item.attrib.get("data")
                if not data:
                    continue
                parts = data.split("|")
                if len(parts) < 6:
                    continue
                dt = pd.to_datetime(parts[0], format="%Y%m%d")
                if not (start_date <= dt.to_pydatetime() <= end_date):
                    continue
                records.append(
                    {
                        "날짜": dt,
                        "시가": int(parts[1]),
                        "고가": int(parts[2]),
                        "저가": int(parts[3]),
                        "종가": int(parts[4]),
                        "거래량": int(parts[5]),
                    }
                )
            if not records:
                return pd.DataFrame()
            df = pd.DataFrame(records).set_index("날짜").sort_index()
            return df
        except Exception as e:
            logger.warning(f"  [OHLCV API ERROR] {ticker}: {e}")
            return pd.DataFrame()

    def _to_ohlcv_df(self, data: list) -> pd.DataFrame:
        df = pd.DataFrame(data)
        if "날짜" in df.columns:
            df["날짜"] = pd.to_datetime(df["날짜"])
            df.set_index("날짜", inplace=True)
        return df

    def _from_ohlcv_df(self, df: pd.DataFrame) -> list:
        df_save = df.reset_index()
        df_save.columns = ["날짜"] + list(df.columns[1:])
        df_save["날짜"] = df_save["날짜"].astype(str)
        return df_save.to_dict("records")

    def get_trading_days(self, start_date: datetime, end_date: datetime) -> list[datetime]:
        """해당 기간 내 실제 개장일 목록을 삼성전자(005930) 시세 이력으로 역산합니다."""
        self._ensure_login()
        url = f"{self.BASE_URL}/comm/bldAttendant/getJsonData.cmd"
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": self.BASE_URL,
            "Referer": f"{self.BASE_URL}/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201",
            "User-Agent": self._UA,
            "X-Requested-With": "XMLHttpRequest",
        }
        payload = {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT01701",
            "locale": "ko_KR",
            "isuCd": "KR7005930003",
            "isuSrtCd": "005930",
            "strtDd": start_date.strftime("%Y%m%d"),
            "endDd": end_date.strftime("%Y%m%d"),
            "adjStkPrc_isNo": "Y",
            "share": "1",
            "money": "1",
            "csvxls_isNo": "false",
        }
        try:
            self._throttle()
            resp = self.session.post(url, headers=headers, data=payload, timeout=30)
            rows = resp.json().get("output", [])
            days = []
            for row in rows:
                trd_str = row.get("TRD_DD", "").replace("/", "")
                if trd_str:
                    days.append(datetime.strptime(trd_str, "%Y%m%d"))
            return sorted(days)
        except Exception as e:
            logger.warning(f"  [TRADING DAYS ERROR] {e}")
            return []

    def get_daily_market_ohlcv(self, date: datetime, market: str = "ALL", use_cache: bool = True) -> pd.DataFrame:
        """지정된 날짜의 시장 내 모든 종목 OHLCV 데이터를 조회합니다 (티커 인덱스)."""
        d_str = date.strftime("%Y%m%d")
        cache_path = self.market_daily_cache_dir / f"{d_str}_{market}.json"
        if use_cache:
            cache = self._load_cache(cache_path, f"MARKET DAILY for {d_str}")
            if cache is not None:
                return self._to_market_df(cache)

        rows = self._fetch_all_markets(d_str)
        if market != "ALL":
            rows = [r for r in rows if r.get("MKT_NM", "").upper().startswith(market[:4])]

        records = []
        for r in rows:
            code = r.get("ISU_SRT_CD")
            if not code:
                continue
            records.append(
                {
                    "티커": code,
                    "종목명": r.get("ISU_ABBRV", ""),
                    "시가": self._num(r.get("TDD_OPNPRC", "0")),
                    "고가": self._num(r.get("TDD_HGPRC", "0")),
                    "저가": self._num(r.get("TDD_LWPRC", "0")),
                    "종가": self._num(r.get("TDD_CLSPRC", "0")),
                    "거래량": self._num(r.get("ACC_TRDVOL", "0")),
                }
            )
        df = pd.DataFrame(records)
        if not df.empty:
            df.set_index("티커", inplace=True)
        if use_cache and not df.empty:
            self._save_cache(cache_path, self._from_market_df(df), "MARKET DAILY")
        return df

    def _to_market_df(self, data: list) -> pd.DataFrame:
        df = pd.DataFrame(data)
        ticker_col = next((c for c in ["티커", "ticker"] if c in df.columns), None)
        if ticker_col:
            df.set_index(ticker_col, inplace=True)
        for col in ["시가", "고가", "저가", "종가", "거래량"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def _from_market_df(self, df: pd.DataFrame) -> list:
        df_save = df.reset_index()
        if not any(c in df_save.columns for c in ["티커", "ticker"]):
            df_save.rename(columns={df_save.columns[0]: "티커"}, inplace=True)
        return df_save.to_dict("records")
