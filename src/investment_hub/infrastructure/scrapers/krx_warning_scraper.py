from datetime import datetime, timedelta

import pandas as pd
import requests
from bs4 import BeautifulSoup

from investment_hub.domain.models import InvestmentWarningStock
from investment_hub.infrastructure.adapters.krx_calendar_service import KrxCalendarService
from investment_hub.infrastructure.adapters.pykrx_adapter import PyKRXAdapter


def fetch_investment_warning_stocks(start_date: str | None = None, end_date: str | None = None) -> list[InvestmentWarningStock]:
    """KRX KIND 기업공시 채널에서 특정 기간 동안의 '투자경고종목' 지정 내역을 크롤링합니다.

    지정일, 해제일, 종목명 등의 메타데이터를 수집하며, 종목 코드는 PyKRXAdapter의
    Ticker 매핑 캐시를 활용하여 연결합니다.

    Args:
        start_date (str | None): 검색 시작일 (YYYY-MM-DD 포맷). 기본값은 오늘로부터 1년 전.
        end_date (str | None): 검색 종료일 (YYYY-MM-DD 포맷). 기본값은 오늘 날짜.

    Returns:
        list[InvestmentWarningStock]: 수집 정보가 매핑된 도메인 객체 리스트. 수집 실패 시 빈 리스트 반환.
    """
    url = "https://kind.krx.co.kr/investwarn/investattentwarnrisky.do"
    start, end = _get_default_dates(start_date, end_date)
    mapping = _get_name_to_code_mapping(end)

    try:
        html = _fetch_html(url, _build_payload(start, end))
        if not html:
            return []

        rows = _parse_table(html)
        if not rows:
            return []

        return _process_rows(rows, mapping)
    except Exception as e:
        print(f"Exception during scraping: {e}")
        return []
def _get_default_dates(start_date: str | None, end_date: str | None) -> tuple[str, str]:
    end = end_date or datetime.now().strftime("%Y-%m-%d")
    start = start_date or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    return start, end

def _build_payload(start: str, end: str) -> dict:
    return {
        "method": "investattentwarnriskySub", "currentPageSize": "5000", "pageIndex": "1",
        "orderMode": "3", "orderStat": "D", "searchCodeType": "", "searchCorpName": "",
        "repIsuSrtCd": "", "menuIndex": "2", "forward": "invstwarnisu_sub",
        "searchFromDate": end, "marketType": "", "searchCorpNameTmp": "",
        "etsIsuSrtCd": "", "startDate": start, "endDate": end,
    }

def _get_name_to_code_mapping(end_date: str) -> dict:
    adapter = PyKRXAdapter()
    raw_date = min(datetime.strptime(end_date, "%Y-%m-%d"), datetime.now().replace(hour=0, minute=0, second=0, microsecond=0))
    # KRX는 주말/휴장일에는 티커 스냅샷을 주지 않아 mapping이 통째로 비게 된다
    # (해당 회차 전체 종목의 코드 매칭이 실패하는 사고로 이어짐) - 최근 영업일로 보정한다.
    trading_date = KrxCalendarService().get_last_trading_day(raw_date.date())
    mapping_date = datetime(trading_date.year, trading_date.month, trading_date.day)
    print(f"Building stock name-to-code mapping (기준일: {mapping_date.strftime('%Y-%m-%d')})...")
    mapping = adapter.get_ticker_name_mapping(mapping_date, market="ALL", use_cache=True)
    print(f"Mapping ready: {len(mapping)} stocks")
    return mapping

def _fetch_html(url: str, payload: dict) -> str | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/91.0.4472.124 Safari/537.36",
        "Referer": "https://kind.krx.co.kr/investwarn/investattentwarnrisky.do?method=investattentwarnriskyMain",
    }
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    resp = requests.post(url, data=payload, headers=headers, verify=False, timeout=30)  # nosec B501
    if resp.status_code != 200:
        print(f"Error: Status code {resp.status_code}")
        return None
    try:
        return resp.content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return resp.content.decode("euc-kr")
        except UnicodeDecodeError:
            try:
                return resp.content.decode("cp949")
            except UnicodeDecodeError:
                return resp.text

def _parse_table(html: str) -> list | None:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.select("table")
    if not tables:
        print("No tables found.")
        return None
    target = next((t for t in tables if "list" in (t.get("class") or []) and "type-00" in (t.get("class") or [])), tables[0])
    rows = target.select("tr")
    if len(rows) <= 1:
        print("No data rows found.")
        return None
    return rows

def _extract_market(name_cell) -> str:
    img = name_cell.find("img")
    if not img: return "Unknown"
    if img.has_attr("alt"): return str(img["alt"])
    if img.has_attr("title"): return str(img["title"])
    return "Unknown"

def _parse_date(date_str: str) -> datetime | None:
    if not date_str or date_str == "-":
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None

def _process_rows(rows: list, mapping: dict) -> list[InvestmentWarningStock]:
    """BS4로 파싱된 전체 행 집합을 순회하며 유효한 도메인 객체 리스트로 변환합니다.

    Args:
        rows (list): BeautifulSoup 행 목록.
        mapping (dict): 종목명-종합코드 매핑 맵.

    Returns:
        list[InvestmentWarningStock]: 생성 완료된 도메인 모델 콜렉션.
    """
    results = []
    for row in rows:
        stock = _parse_stock_from_row(row, mapping)
        if stock:
            results.append(stock)
    return results

def _parse_stock_from_row(row, mapping: dict) -> InvestmentWarningStock | None:
    """단일 HTML 행을 분석하여 지정일, 해제일 등의 속성을 추출하고 객체화합니다.

    Args:
        row: BeautifulSoup 단일 tr 요소.
        mapping (dict): 코드 조회용 사전.

    Returns:
        InvestmentWarningStock | None: 추출 성공 시 객체, 데이터 부족 시 None.
    """
    cols = row.select("td")
    if len(cols) < 5:
        return None

    name = cols[1].text.strip()
    desig_dt = _parse_date(cols[3].text.strip())
    if not desig_dt:
        return None

    code = mapping.get(name)
    if not code:
        print(f"  Warning: Could not find code for '{name}'")
        return None

    return InvestmentWarningStock(
        code=code, name=name, market=_extract_market(cols[1]),
        designation_date=desig_dt, release_date=_parse_date(cols[4].text.strip())
    )



if __name__ == "__main__":
    stocks = fetch_investment_warning_stocks()
    print(f"Fetched {len(stocks)} stocks.")
    if stocks:
        print(f"Sample: {stocks[0]}")
        # Convert to DF for display check
        df = pd.DataFrame([s.to_dict() for s in stocks])
        print(df.head())
