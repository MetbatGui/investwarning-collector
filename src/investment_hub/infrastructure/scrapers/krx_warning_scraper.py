from datetime import datetime, timedelta

import pandas as pd
import requests
from bs4 import BeautifulSoup

from investment_hub.domain.models import InvestmentWarningStock
from investment_hub.infrastructure.adapters.pykrx_adapter import PyKRXAdapter


def fetch_investment_warning_stocks(start_date: str | None = None, end_date: str | None = None) -> list[InvestmentWarningStock]:
    """
    Fetches the list of 'Investment Warning' stocks from KRX KIND.

    Args:
        start_date (str): Start date for search (YYYY-MM-DD). Defaults to 1 year ago.
        end_date (str): End date for search (YYYY-MM-DD). Defaults to today.

    Returns:
        List[InvestmentWarningStock]: List of domain objects.
    """
    url = "https://kind.krx.co.kr/investwarn/investattentwarnrisky.do"

    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    # Payload based on user request and experimentation
    data = {
        "method": "investattentwarnriskySub",
        "currentPageSize": "5000",
        "pageIndex": "1",
        "orderMode": "3",
        "orderStat": "D",
        "searchCodeType": "",
        "searchCorpName": "",
        "repIsuSrtCd": "",
        "menuIndex": "2",
        "forward": "invstwarnisu_sub",
        "searchFromDate": end_date,
        "marketType": "",
        "searchCorpNameTmp": "",
        "etsIsuSrtCd": "",
        "startDate": start_date,
        "endDate": end_date,
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Referer": "https://kind.krx.co.kr/investwarn/investattentwarnrisky.do?method=investattentwarnriskyMain",
    }

    results = []

    # PyKRX 어댑터 초기화
    pykrx_adapter = PyKRXAdapter()

    # end_date 기준으로 종목명→코드 매핑 테이블 생성
    # (현재 날짜가 아닌 end_date 기준으로 조회해야 과거 사명의 종목도 찾을 수 있음)
    # 단, 미래 날짜는 오늘로 제한
    mapping_date = datetime.strptime(end_date, "%Y-%m-%d")
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if mapping_date > today:
        mapping_date = today

    print(f"Building stock name-to-code mapping (기준일: {mapping_date.strftime('%Y-%m-%d')})...")
    name_to_code_mapping = pykrx_adapter.get_ticker_name_mapping(mapping_date, market="ALL", use_cache=True)
    print(f"Mapping ready: {len(name_to_code_mapping)} stocks")

    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        response = requests.post(url, data=data, headers=headers, verify=False, timeout=30)  # nosec B501

        if response.status_code != 200:
            print(f"Error: Status code {response.status_code}")
            return []

        # Encoding handling
        content = response.content
        try:
            html = content.decode("euc-kr")
        except UnicodeDecodeError:
            try:
                html = content.decode("cp949")
            except UnicodeDecodeError:
                html = response.text

        soup = BeautifulSoup(html, "html.parser")
        tables = soup.select("table")

        if not tables:
            print("No tables found.")
            return []

        table = None
        for t in tables:
            if "list" in (t.get("class") or []) and "type-00" in (t.get("class") or []):
                table = t
                break

        if not table:
            table = tables[0]

        rows = table.select("tr")
        if len(rows) <= 1:
            print("No data rows found.")
            return []

        for row in rows:
            cols = row.select("td")
            if len(cols) < 5:
                continue

            # Cols: 0=No, 1=Name(MarketImg), 2=AnnouncementDate, 3=DesignationDate, 4=ReleaseDate

            name_cell = cols[1]
            company_name = name_cell.text.strip()

            # Extract Market from Image Alt
            # <img src="..." alt="코스닥" ...>
            market_img = name_cell.find("img")
            market = "Unknown"
            if market_img and market_img.has_attr("alt"):
                market = str(market_img["alt"])
            elif market_img and market_img.has_attr("title"):
                market = str(market_img["title"])  # Sometimes title is used?

            # 공시일은 cols[2] (미사용), 지정일은 cols[3], 해제일은 cols[4]
            designation_str = cols[3].text.strip()  # 지정일
            release_str = cols[4].text.strip()  # 해제일

            def parse_date(date_str):
                if not date_str or date_str == "-":
                    return None
                try:
                    return datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    return None

            designation_date = parse_date(designation_str)
            release_date = parse_date(release_str)

            # Must have designation date to be valid
            if designation_date:
                # 미리 생성한 매핑 테이블에서 종목코드 조회
                code = name_to_code_mapping.get(company_name)

                if code:
                    stock_obj = InvestmentWarningStock(
                        code=code,
                        name=company_name,
                        market=market,
                        designation_date=designation_date,
                        release_date=release_date,
                    )
                    results.append(stock_obj)
                else:
                    print(f"  Warning: Could not find code for '{company_name}'")

        return results

    except Exception as e:
        print(f"Exception during scraping: {e}")
        return []


if __name__ == "__main__":
    stocks = fetch_investment_warning_stocks()
    print(f"Fetched {len(stocks)} stocks.")
    if stocks:
        print(f"Sample: {stocks[0]}")
        # Convert to DF for display check
        df = pd.DataFrame([s.to_dict() for s in stocks])
        print(df.head())
