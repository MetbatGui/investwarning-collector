"""
일별 가격 데이터 수집 모듈
PyKRXAdapter를 사용하여 효율적으로 데이터를 수집합니다.
"""

from datetime import datetime, timedelta
from typing import TypedDict

from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock
from investment_hub.infrastructure.adapters.pykrx_adapter import PyKRXAdapter


class _StockPeriod(TypedDict):
    start: datetime
    end: datetime
    min_designation: datetime
    info: InvestmentWarningStock


def collect_daily_prices_batch(
    warning_stocks: list[InvestmentWarningStock],
    trading_days_after_release: int = 3,
    use_cache: bool = True,
) -> dict[str, list[DailyPriceData]]:
    """
    여러 투자경고 종목의 일별 가격 데이터를 배치로 수집합니다.

    Args:
        warning_stocks: 투자경고 종목 리스트
        trading_days_after_release: 해제일 이후 수집할 거래일 수
        use_cache: 캐시 사용 여부

    Returns:
        종목코드별 일별 가격 데이터 딕셔너리
    """
    adapter = PyKRXAdapter()
    result: dict[str, list[DailyPriceData]] = {stock.code: [] for stock in warning_stocks}

    # 1. 전체 수집 기간 계산
    min_date = datetime.max
    max_date = datetime.min

    # 각 종목별 수집 기간 정보 미리 계산
    stock_periods: dict[str, _StockPeriod] = {}

    print(f"  종목 수집 기간 계산 중 ({len(warning_stocks)}종목)...")

    for stock_info in warning_stocks:
        code = stock_info.code
        release_date = stock_info.release_date
        designation_date = stock_info.designation_date

        # 활성 경고인 경우 오늘까지
        is_active = release_date is None
        calc_release_date: datetime
        if is_active:
            calc_release_date = datetime.now()
        else:
            calc_release_date = release_date  # type: ignore[assignment]  # is_active=False 보장됨

        # 수집 기간: 지정일 - 5일 ~ (해제일 + 추가 수집일)
        start_date = designation_date - timedelta(days=5)

        # 추가 수집 기간: release_date + 약 N*2 달력일 (공휴일/주말 감안)
        # is_active면 오늘까지
        if is_active:
            end_date = datetime.now()
        else:
            end_date = calc_release_date + timedelta(days=trading_days_after_release * 2 + 5)

        # 미래의 날짜는 오늘로 제한
        if end_date > datetime.now():
            end_date = datetime.now()

        # 전체 기간 갱신 (global)
        if start_date < min_date:
            min_date = start_date
        if end_date > max_date:
            max_date = end_date

        # 종목별 기간 갱신 (한 종목이 여러 번 지정된 경우 합집합 기간 수집)
        if code not in stock_periods:
            stock_periods[code] = {
                "start": start_date,
                "end": end_date,
                "min_designation": designation_date,
                "info": stock_info,
            }
        else:
            if start_date < stock_periods[code]["start"]:
                stock_periods[code]["start"] = start_date
            if end_date > stock_periods[code]["end"]:
                stock_periods[code]["end"] = end_date
            if designation_date < stock_periods[code]["min_designation"]:
                stock_periods[code]["min_designation"] = designation_date

    print(f"  수집 기간: {min_date.strftime('%Y-%m-%d')} ~ {max_date.strftime('%Y-%m-%d')}")

    # 2. 영업일 기준 날짜 리스트 생성 (주말 제외, 공휴일은 API 호출 시 빈 데이터로 처리됨)
    target_dates = []
    curr_date = min_date
    while curr_date <= max_date:
        # 주말 제외 (0:월 ~ 6:일)
        if curr_date.weekday() < 5:
            target_dates.append(curr_date)
        curr_date += timedelta(days=1)

    print(f"  목표 거래일: 약 {len(target_dates)}일")

    # 3. 날짜별로 전체 종목 시세 조회 및 데이터 추출
    total_dates = len(target_dates)

    for i, date in enumerate(target_dates, 1):
        if i % 10 == 0:
            print(f"  날짜 처리 중 {i}/{total_dates}: {date.strftime('%Y-%m-%d')}...")

        # 해당 날짜에 데이터를 수집해야 하는 종목 필터링
        target_codes = [code for code, period in stock_periods.items() if period["start"] <= date <= period["end"]]

        if not target_codes:
            continue

        # 일자별 전체 시세 조회 (캐시 활용)
        df_market = adapter.get_daily_market_ohlcv(date, market="ALL", use_cache=use_cache)

        if df_market.empty:
            continue

        # 필요한 종목 데이터 추출 및 저장
        for code in target_codes:
            if code in df_market.index:
                row = df_market.loc[code]

                try:
                    close_price = int(row["종가"])

                    # 종가 0 이하 무시 (거래정지 등 제외, 휴장일 데이터 오입력 방지)
                    if close_price <= 0:
                        continue

                    change_rate = float(row["등락률"])

                    stock_info_obj: InvestmentWarningStock = stock_periods[code]["info"]
                    daily_price = DailyPriceData(
                        code=code,
                        name=stock_info_obj.name,
                        date=date,
                        close=close_price,
                        change_rate=round(change_rate, 2),
                    )
                    result[code].append(daily_price)
                except Exception:
                    # 데이터 형식이 맞지 않는 경우 스킵
                    pass

    # 4. 각 종목별 후처리 (정렬 및 지정일 필터링)
    print("  후처리 중 (정렬 및 필터링)...")
    for code, prices in result.items():
        if not prices:
            continue

        # 날짜순 정렬
        prices.sort(key=lambda x: x.date)

        # 지정일부터 필터링
        # 한 종목이 여러 번 지정된 경우, 가장 빠른 지정일 기준으로 필터링하여
        # 호출자가 필요한 모든 데이터를 확보할 수 있게 함
        period_info = stock_periods[code]
        min_designation = period_info["min_designation"]

        result[code] = [p for p in prices if p.date >= min_designation]

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
