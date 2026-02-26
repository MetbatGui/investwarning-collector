"""
일별 가격 데이터 수집 모듈
PyKRXAdapter를 사용하여 효율적으로 데이터를 수집합니다.
"""

from datetime import datetime, timedelta
from typing import TypedDict

import pandas as pd
from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock
from investment_hub.infrastructure.adapters.pykrx_adapter import PyKRXAdapter


class _StockPeriod(TypedDict):
    start: datetime
    end: datetime
    min_designation: datetime
    info: InvestmentWarningStock


def _calc_period_for_stock(stock_info: InvestmentWarningStock, days_after: int) -> tuple[datetime, datetime]:
    start = stock_info.designation_date - timedelta(days=5)
    if stock_info.release_date is None:
        end = datetime.now()
    else:
        end = min(stock_info.release_date + timedelta(days=days_after * 2 + 5), datetime.now())
    return start, end

def _build_stock_periods(warning_stocks: list, days_after: int) -> tuple[dict, datetime, datetime]:
    periods: dict[str, _StockPeriod] = {}
    min_date, max_date = datetime.max, datetime.min
    for s in warning_stocks:
        start, end = _calc_period_for_stock(s, days_after)
        min_date, max_date = min(min_date, start), max(max_date, end)
        
        if s.code not in periods:
            periods[s.code] = {"start": start, "end": end, "min_designation": s.designation_date, "info": s}
        else:
            periods[s.code]["start"] = min(periods[s.code]["start"], start)
            periods[s.code]["end"] = max(periods[s.code]["end"], end)
            periods[s.code]["min_designation"] = min(periods[s.code]["min_designation"], s.designation_date)
    return periods, min_date, max_date

def _gen_business_dates(start_dt: datetime, end_dt: datetime) -> list[datetime]:
    dates = []
    curr = start_dt
    while curr <= end_dt:
        if curr.weekday() < 5: dates.append(curr)
        curr += timedelta(days=1)
    return dates

def _extract_from_market(df_market: pd.DataFrame, codes: list[str], dt: datetime, periods: dict) -> list[DailyPriceData]:
    found = []
    for code in codes:
        if code in df_market.index:
            try:
                row = df_market.loc[code]
                close_price = int(row["종가"])
                if close_price > 0:
                    info = periods[code]["info"]
                    found.append(DailyPriceData(code=code, name=info.name, date=dt, close=close_price, change_rate=round(float(row["등락률"]), 2)))
            except Exception: pass
    return found

def _fetch_prices(adapter: PyKRXAdapter, dates: list[datetime], periods: dict, use_cache: bool) -> dict:
    result = {code: [] for code in periods}
    for i, dt in enumerate(dates, 1):
        if i % 10 == 0: print(f"  날짜 처리 중 {i}/{len(dates)}: {dt.strftime('%Y-%m-%d')}...")
        target_codes = [c for c, p in periods.items() if p["start"] <= dt <= p["end"]]
        if not target_codes: continue
        
        df_market = adapter.get_daily_market_ohlcv(dt, market="ALL", use_cache=use_cache)
        if df_market.empty: continue
        for price_data in _extract_from_market(df_market, target_codes, dt, periods):
            result[price_data.code].append(price_data)
    return result

def _post_process(result: dict, periods: dict) -> dict:
    for code, prices in result.items():
        if prices:
            prices.sort(key=lambda x: x.date)
            min_desig = periods[code]["min_designation"]
            result[code] = [p for p in prices if p.date >= min_desig]
    return result

def collect_daily_prices_batch(warning_stocks: list[InvestmentWarningStock], trading_days_after_release: int = 3, use_cache: bool = True) -> dict[str, list[DailyPriceData]]:
    """여러 투자경고 종목의 일별 가격 데이터를 배치로 수집합니다."""
    if not warning_stocks: return {}
    print(f"  종목 수집 기간 계산 중 ({len(warning_stocks)}종목)...")
    
    periods, min_dt, max_dt = _build_stock_periods(warning_stocks, trading_days_after_release)
    print(f"  수집 기간: {min_dt.strftime('%Y-%m-%d')} ~ {max_dt.strftime('%Y-%m-%d')}")
    
    dates = _gen_business_dates(min_dt, max_dt)
    print(f"  목표 거래일: 약 {len(dates)}일")
    
    result = _fetch_prices(PyKRXAdapter(), dates, periods, use_cache)
    print("  후처리 중 (정렬 및 필터링)...")
    result = _post_process(result, periods)
    
    print(f"  완료: {len(result)}종목 처리됨")
    return result


def collect_daily_prices(
    warning_stock: InvestmentWarningStock, trading_days_after_release: int = 3, use_cache: bool = True
) -> list[DailyPriceData]:
    """
    단일 종목의 일별 가격 데이터를 수집합니다.
    내부적으로 배치 함수를 사용합니다.
    """
    result = collect_daily_prices_batch(
        [warning_stock], trading_days_after_release=trading_days_after_release, use_cache=use_cache
    )
    return result.get(warning_stock.code, [])


if __name__ == "__main__":
    # 테스트
    from datetime import datetime

    test_stock = InvestmentWarningStock(
        code="043590", name="웰킵스하이텍", market="코스닥", designation_date=datetime(2026, 2, 11), release_date=None
    )

    prices = collect_daily_prices(test_stock, trading_days_after_release=3)

    print(f"\nCollected {len(prices)} daily prices")
    for p in prices:
        print(f"{p.date.strftime('%Y-%m-%d')}: {p.close:,.0f}원 ({p.change_rate:+.2f}%)")
