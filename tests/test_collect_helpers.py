"""
Application Service 내 헬퍼 메서드 유닛 테스트
- _filter_stocks: KRX 목록 필터링 로직
- _make_row: CSV 행 딕셔너리 생성
"""

import pandas as pd
import pytest

from investment_hub.application.services import WarningCollectionService
from investment_hub.domain.models import InvestmentWarningStock
from investment_hub.infrastructure.adapters.local_storage_adapter import LocalStorageAdapter
from investment_hub.infrastructure.adapters.sqlite_repository_adapter import SqliteRepositoryAdapter

# ─────────────────────────────────────────────────────────────────────────────
# 픽스처
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def service():
    # 저장소는 메모리 수준이나 더미 경로 등 동작만 검증할 수 있는 객체 할당
    repo = SqliteRepositoryAdapter(base_dir="tests/dummy_sqlite")
    storage = LocalStorageAdapter()
    return WarningCollectionService(
        repository=repo,
        storage=storage,
        output_dir="tests/dummy_out",
        max_warning_days=60,
    )


def _make_stock(code, name="테스트", market="코스피", desig="2026-01-02", release=None):
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
    def test_basic_pass_through(self, service):
        stocks = [_make_stock("A", desig="2026-01-05", release="2026-01-10")]
        result = service._filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=False)
        assert len(result) == 1

    def test_excludes_konex(self, service):
        stocks = [_make_stock("A", market="코넥스", desig="2026-01-05", release="2026-01-10")]
        result = service._filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=False)
        assert len(result) == 0

    def test_excludes_outside_date_range(self, service):
        stocks = [_make_stock("A", desig="2026-02-01", release="2026-02-10")]
        result = service._filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=False)
        assert len(result) == 0

    def test_excludes_too_long_warning(self, service):
        stocks = [_make_stock("A", desig="2026-01-02", release="2026-04-01")]
        result = service._filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=False)
        assert len(result) == 0

    def test_excludes_active_when_not_include_active(self, service):
        stocks = [_make_stock("A", desig="2026-01-05", release=None)]
        result = service._filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=False)
        assert len(result) == 0

    def test_includes_active_when_include_active(self, service):
        stocks = [_make_stock("A", desig="2026-01-05", release=None)]
        result = service._filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=True)
        assert len(result) == 1

    def test_multiple_mixed(self, service):
        stocks = [
            _make_stock("A", market="코넥스", desig="2026-01-05", release="2026-01-10"),
            _make_stock("B", desig="2026-01-05", release="2026-01-10"),
            _make_stock("C", desig="2026-01-05", release=None),
        ]
        result = service._filter_stocks(stocks, "2026-01-01", "2026-01-31", include_active=False)
        assert len(result) == 1
        assert result[0].code == "B"


# ─────────────────────────────────────────────────────────────────────────────
# _make_row
# ─────────────────────────────────────────────────────────────────────────────


class TestMakeRow:
    def test_basic_row_structure(self, service):
        stock = _make_stock("000001", name="테스트A", desig="2026-01-02", release="2026-01-07")
        row = service._make_row(2026, stock, "2026-01-05", 10000, 2.5)
        assert row["year"] == 2026
        assert row["code"] == "000001"
        assert row["name"] == "테스트A"
        assert row["close"] == 10000
        assert row["change_rate"] == 2.5
        assert row["date"] == "2026-01-05"
        assert row["designation_date"] == "2026-01-02"
        assert row["release_date"] == "2026-01-07"

    def test_active_stock_empty_release_date(self, service):
        stock = _make_stock("000002", desig="2026-01-02", release=None)
        row = service._make_row(2026, stock, "2026-01-03", 5000, -1.0)
        assert row["release_date"] == ""

    def test_change_rate_rounded(self, service):
        stock = _make_stock("000003", desig="2026-01-02", release="2026-01-10")
        row = service._make_row(2026, stock, "2026-01-05", 10000, 2.123456)
        assert row["change_rate"] == 2.12
