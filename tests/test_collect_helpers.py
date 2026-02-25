"""
collect_today.py 헬퍼 함수 유닛 테스트
- _filter_stocks: KRX 목록 필터링 로직
- _make_row: CSV 행 딕셔너리 생성
- _restore_from_df: DataFrame → (stocks, prices) 복원
"""
import pytest
import pandas as pd
from unittest.mock import patch
from datetime import datetime

# collect_today.py는 최상위 루트에 있고 sys.path 조작을 하므로
# 함수를 직접 import하여 테스트
import sys
import os

# collect_yearly_warnings 의존성 모킹 (실제 파일 I/O 방지)
from unittest.mock import MagicMock
sys.modules.setdefault("collect_yearly_warnings", MagicMock(
    OUTPUT_DIR="output",
    MAX_WARNING_DAYS=60,
    TRADING_DAYS_AFTER_RELEASE=3,
    _save_excel=MagicMock(),
    get_storage=MagicMock(),
))

from collect_today import _filter_stocks, _make_row, _restore_from_df
from investment_hub.domain.models import InvestmentWarningStock, DailyPriceData


# ─────────────────────────────────────────────────────────────────────────────
# 픽스처
# ─────────────────────────────────────────────────────────────────────────────

def _make_stock(code, name="테스트", market="코스피",
               desig="2026-01-02", release=None):
    return InvestmentWarningStock(
        code=code,
        name=name,
        market=market,
        designation_date=pd.to_datetime(desig),
        release_date=pd.to_datetime(release) if release else None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# _filter_stocks
# ─────────────────────────────────────────────────────────────────────────────

class TestFilterStocks:
    def test_basic_pass_through(self):
        stocks = [_make_stock("A", desig="2026-01-05", release="2026-01-10")]
        result = _filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=False)
        assert len(result) == 1

    def test_excludes_konex(self):
        stocks = [_make_stock("A", market="코넥스", desig="2026-01-05", release="2026-01-10")]
        result = _filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=False)
        assert len(result) == 0

    def test_excludes_outside_date_range(self):
        # 지정일이 end_date 이후
        stocks = [_make_stock("A", desig="2026-02-01", release="2026-02-10")]
        result = _filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=False)
        assert len(result) == 0

    def test_excludes_too_long_warning(self):
        # warning_days > MAX_WARNING_DAYS(60)
        stocks = [_make_stock("A", desig="2026-01-02", release="2026-04-01")]
        result = _filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=False)
        assert len(result) == 0

    def test_excludes_active_when_not_include_active(self):
        stocks = [_make_stock("A", desig="2026-01-05", release=None)]
        result = _filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=False)
        assert len(result) == 0

    def test_includes_active_when_include_active(self):
        stocks = [_make_stock("A", desig="2026-01-05", release=None)]
        result = _filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=True)
        assert len(result) == 1

    def test_multiple_mixed(self):
        stocks = [
            _make_stock("A", market="코넥스", desig="2026-01-05", release="2026-01-10"),  # 코넥스 제외
            _make_stock("B", desig="2026-01-05", release="2026-01-10"),                   # 포함
            _make_stock("C", desig="2026-01-05", release=None),                           # active → 제외
        ]
        result = _filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=False)
        assert len(result) == 1
        assert result[0].code == "B"


# ─────────────────────────────────────────────────────────────────────────────
# _make_row
# ─────────────────────────────────────────────────────────────────────────────

class TestMakeRow:
    def test_basic_row_structure(self):
        stock = _make_stock("000001", name="테스트A", desig="2026-01-02", release="2026-01-07")
        row = _make_row(2026, stock, "2026-01-05", 10000, 2.5)
        assert row["year"] == 2026
        assert row["code"] == "000001"
        assert row["name"] == "테스트A"
        assert row["close"] == 10000
        assert row["change_rate"] == 2.5
        assert row["date"] == "2026-01-05"
        assert row["designation_date"] == "2026-01-02"
        assert row["release_date"] == "2026-01-07"

    def test_active_stock_empty_release_date(self):
        stock = _make_stock("000002", desig="2026-01-02", release=None)
        row = _make_row(2026, stock, "2026-01-03", 5000, -1.0)
        assert row["release_date"] == ""

    def test_change_rate_rounded(self):
        stock = _make_stock("000003", desig="2026-01-02", release="2026-01-10")
        row = _make_row(2026, stock, "2026-01-05", 10000, 2.123456)
        assert row["change_rate"] == 2.12


# ─────────────────────────────────────────────────────────────────────────────
# _restore_from_df
# ─────────────────────────────────────────────────────────────────────────────

class TestRestoreFromDf:
    def _make_df(self):
        return pd.DataFrame([
            {
                "code": "000001", "name": "종목A", "market": "코스피",
                "designation_date": "2026-01-02", "release_date": "2026-01-07",
                "date": "2026-01-03", "close": 10000.0, "change_rate": 1.5,
            },
            {
                "code": "000001", "name": "종목A", "market": "코스피",
                "designation_date": "2026-01-02", "release_date": "2026-01-07",
                "date": "2026-01-06", "close": 11000.0, "change_rate": -2.0,
            },
            {
                "code": "000002", "name": "종목B", "market": "코스닥",
                "designation_date": "2026-01-05", "release_date": "",
                "date": "2026-01-06", "close": 5000.0, "change_rate": 0.5,
            },
        ])

    def test_restores_stocks(self):
        stocks, _ = _restore_from_df(self._make_df())
        codes = {s.code for s in stocks}
        assert codes == {"000001", "000002"}

    def test_released_stock_has_release_date(self):
        stocks, _ = _restore_from_df(self._make_df())
        s = next(s for s in stocks if s.code == "000001")
        assert s.release_date is not None

    def test_active_stock_has_no_release_date(self):
        stocks, _ = _restore_from_df(self._make_df())
        s = next(s for s in stocks if s.code == "000002")
        assert s.release_date is None

    def test_restores_prices(self):
        _, prices = _restore_from_df(self._make_df())
        assert "000001" in prices
        assert len(prices["000001"]) == 2  # 두 날짜
        closes = sorted([p.close for p in prices["000001"]])
        assert closes == [10000.0, 11000.0]

    def test_no_duplicate_stocks(self):
        """동일 종목 두 날짜 데이터가 있어도 종목은 1개만 복원"""
        stocks, _ = _restore_from_df(self._make_df())
        codes = [s.code for s in stocks]
        assert codes.count("000001") == 1
