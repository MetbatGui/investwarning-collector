from datetime import datetime

import pytest

from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock
from investment_hub.visualization.excel_exporter import WarningExcelExporter


@pytest.fixture
def exporter():
    return WarningExcelExporter(trading_days_after_release=3)


@pytest.fixture
def sample_stock():
    return InvestmentWarningStock(
        code="005930",
        name="삼성전자",
        market="코스피",
        designation_date=datetime(2025, 1, 2),
        release_date=datetime(2025, 1, 6),
    )


@pytest.fixture
def sample_prices():
    """지정일: 1/2, 해제일: 1/6인 경우의 가격 데이터 (휴일 무시, 매일 영업일 가정)"""
    return [
        DailyPriceData(code="005930", name="삼성전자", date=datetime(2025, 1, 2), close=70000, change_rate=0.5),  # D+0
        DailyPriceData(
            code="005930", name="삼성전자", date=datetime(2025, 1, 3), close=91000, change_rate=30.0
        ),  # D+1 (상한가)
        DailyPriceData(code="005930", name="삼성전자", date=datetime(2025, 1, 4), close=90000, change_rate=-1.1),  # D+2
        DailyPriceData(
            code="005930", name="삼성전자", date=datetime(2025, 1, 5), close=92000, change_rate=2.2
        ),  # D+3 (해제 전날)
        DailyPriceData(
            code="005930", name="삼성전자", date=datetime(2025, 1, 6), close=95000, change_rate=3.2
        ),  # D+4 (해제일)
        DailyPriceData(code="005930", name="삼성전자", date=datetime(2025, 1, 7), close=96000, change_rate=1.0),  # D+5
    ]


def test_build_rows_filtering(exporter, sample_stock, sample_prices):
    """지정일 이전 데이터 제외, 해제일+3영업일까지만 필터링되어야 한다."""
    # 노이즈 추가: 지정일 이전, 해제일+10영업일
    prices = (
        [DailyPriceData(code="005930", name="삼성전자", date=datetime(2025, 1, 1), close=69000, change_rate=0.0)]
        + sample_prices  # 1/2(D+0), 1/3, 1/4, 1/5, 1/6(해제일), 1/7(D+5)
        + [
            DailyPriceData(code="005930", name="삼성전자", date=datetime(2025, 1, 8), close=97000, change_rate=1.0),
            DailyPriceData(code="005930", name="삼성전자", date=datetime(2025, 1, 9), close=98000, change_rate=1.0),
            DailyPriceData(code="005930", name="삼성전자", date=datetime(2025, 1, 10), close=99000, change_rate=1.0),
            DailyPriceData(code="005930", name="삼성전자", date=datetime(2025, 1, 15), close=100000, change_rate=2.0),
        ]
    )

    rows_data, max_days = exporter._build_rows([sample_stock], {"005930": prices})

    assert len(rows_data) == 1
    row = rows_data[0]

    # 1/1과 1/10, 1/15는 제외됨
    # 포함: 1/2, 1/3, 1/4, 1/5, 1/6(해제일), 1/7, 1/8, 1/9 -> 총 8개
    assert len(row["prices_by_trading_day"]) == 8
    # raw_release_idx: 시세 목록 내에서 정확히 해제일(1/6)인 날의 인덱스
    # 1/2(0), 1/3(1), 1/4(2), 1/5(3), 1/6(4)
    assert row["raw_release_idx"] == 4
    # 해제일 당일 거래정지(등락률 0)가 아니므로 effective_release_idx도 동일
    assert row["effective_release_idx"] == 4


def test_calc_returns_valid_data(exporter, sample_stock, sample_prices):
    """해제전 등락률 = (1/5 종가 / 1/2 종가) - 1, 해제직후 등락률 = (1/6 종가 / 1/5 종가) - 1"""
    rows_data, _ = exporter._build_rows([sample_stock], {"005930": sample_prices})
    row = rows_data[0]
    pre_return, post_return = exporter._get_warning_performance(row["prices_by_trading_day"], row["raw_release_idx"])

    # 해제전: 92000 / 70000 - 1 = 31.43%
    assert pre_return == 31.43

    # 해제직후: 95000 / 92000 - 1 = 3.26%
    assert post_return == 3.26


def test_export_workbook_structure(exporter, sample_stock, sample_prices):
    """엑셀 시트 구조 검증"""
    wb = exporter.export(2025, [sample_stock], {"005930": sample_prices})
    ws = wb.active

    assert ws.title == "2025년 투자경고종목분석"

    # 헤더 검증
    headers = [cell.value for cell in ws[1]]
    assert headers[:8] == [
        "종목명",
        "종목코드",
        "시장",
        "지정일",
        "해제일",
        "경고일수",
        "해제전등락률(%)",
        "해제직후등락률(%)",
    ]
    assert "D+0" in headers

    # 데이터 행 검증 (등락률은 문자열로 기록됨 - _write_data가 str()로 변환)
    row2 = [cell.value for cell in ws[2]]
    assert row2[0] == "삼성전자"
    assert row2[1] == "005930"
    assert row2[5] == 4  # 경고일수 (1/6 - 1/2)
    assert row2[6] == "31.43"  # pre_return
    assert row2[7] == "3.26"  # post_return
    assert row2[8] == 70000  # D+0 종가
    assert row2[12] == 95000  # D+4 (해제일) 종가

    # 스타일/색상 검증
    # D+1: 30% 상승이므로 빨간색이어야 함
    d1_cell = ws.cell(row=2, column=8 + 1 + 1)  # base(8) + D+1(2) = 10
    assert d1_cell.fill.start_color.index in ("FFFFCCCC", "00FFCCCC")  # aRGB Format (openpyxl 내부 색상값 패턴)

    # D+4: 해제일이므로 release 색상(연한 파랑)이어야 함
    d4_cell = ws.cell(row=2, column=8 + 4 + 1)
    assert d4_cell.fill.start_color.index in ("FFCCEEFF", "00CCEEFF")


def test_export_empty_data(exporter):
    """빈 데이터 전달 시 기본 구조를 가진 Workbook 반환"""
    wb = exporter.export(2025, [], {})
    ws = wb.active
    assert ws.title == "2025년 투자경고종목분석"
    assert ws.max_row == 1  # 데이터가 없으면 헤더도 안 써짐
