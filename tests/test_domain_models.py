"""
InvestmentWarningStock, DailyPriceData 도메인 모델 유닛 테스트
"""

from datetime import datetime

import pytest

from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock

# ─────────────────────────────────────────────────────────────────────────────
# 픽스처
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def active_stock():
    """해제일 없는 진행 중 종목"""
    return InvestmentWarningStock(
        code="000001",
        name="테스트종목A",
        market="코스피",
        designation_date=datetime(2026, 1, 2),
        release_date=None,
    )


@pytest.fixture
def released_stock():
    """해제된 종목 (지정 5일 후 해제)"""
    return InvestmentWarningStock(
        code="000002",
        name="테스트종목B",
        market="코스닥",
        designation_date=datetime(2026, 1, 2),
        release_date=datetime(2026, 1, 7),  # 5일 후 해제
    )


# ─────────────────────────────────────────────────────────────────────────────
# is_active
# ─────────────────────────────────────────────────────────────────────────────


class TestIsActive:
    def test_active_when_no_release_date(self, active_stock):
        assert active_stock.is_active is True

    def test_not_active_when_released(self, released_stock):
        assert released_stock.is_active is False


# ─────────────────────────────────────────────────────────────────────────────
# warning_days
# ─────────────────────────────────────────────────────────────────────────────


class TestWarningDays:
    def test_none_when_active(self, active_stock):
        assert active_stock.warning_days is None

    def test_correct_days_when_released(self, released_stock):
        # 2026-01-02 ~ 2026-01-07 = 5일
        assert released_stock.warning_days == 5


# ─────────────────────────────────────────────────────────────────────────────
# is_warning_duration_valid
# ─────────────────────────────────────────────────────────────────────────────


class TestIsWarningDurationValid:
    def test_active_always_valid(self, active_stock):
        assert active_stock.is_warning_duration_valid(60) is True

    def test_within_max_days(self, released_stock):
        # warning_days=5, max=60 → valid
        assert released_stock.is_warning_duration_valid(60) is True

    def test_exceeds_max_days(self):
        stock = InvestmentWarningStock(
            code="X",
            name="장기경고",
            market="코스피",
            designation_date=datetime(2026, 1, 1),
            release_date=datetime(2026, 4, 1),  # 90일
        )
        assert stock.is_warning_duration_valid(60) is False

    def test_exact_max_days(self):
        stock = InvestmentWarningStock(
            code="Y",
            name="경계",
            market="코스피",
            designation_date=datetime(2026, 1, 1),
            release_date=datetime(2026, 3, 2),  # 60일
        )
        assert stock.is_warning_duration_valid(60) is True


# ─────────────────────────────────────────────────────────────────────────────
# is_collectible_at
# ─────────────────────────────────────────────────────────────────────────────


class TestIsCollectibleAt:
    """해제일 + 3 영업일 원칙 검증"""

    def _make_trading_days(self, *dates):
        """datetime 리스트 생성 헬퍼"""
        return [datetime(*d) for d in dates]

    def test_before_designation_date_not_collectible(self, released_stock):
        target = datetime(2026, 1, 1)  # 지정일 하루 전
        trading_days = self._make_trading_days((2026, 1, 8), (2026, 1, 9), (2026, 1, 12))
        assert released_stock.is_collectible_at(target, trading_days) is False

    def test_on_designation_date_collectible(self, released_stock):
        target = datetime(2026, 1, 2)  # 정확히 지정일
        trading_days = self._make_trading_days((2026, 1, 8), (2026, 1, 9), (2026, 1, 12))
        assert released_stock.is_collectible_at(target, trading_days) is True

    def test_on_release_date_collectible(self, released_stock):
        target = datetime(2026, 1, 7)  # 해제일 당일
        trading_days = self._make_trading_days((2026, 1, 8), (2026, 1, 9), (2026, 1, 12))
        assert released_stock.is_collectible_at(target, trading_days) is True

    def test_within_3_trading_days_after_release(self, released_stock):
        # 해제일 = 2026-01-07(수), 이후 영업일: 1/8(목), 1/9(금), 1/12(월)
        trading_days = self._make_trading_days((2026, 1, 8), (2026, 1, 9), (2026, 1, 12))
        assert released_stock.is_collectible_at(datetime(2026, 1, 8), trading_days) is True
        assert released_stock.is_collectible_at(datetime(2026, 1, 12), trading_days) is True  # 3번째 영업일

    def test_beyond_3_trading_days_after_release(self, released_stock):
        # 해제일 이후 4번째 영업일은 수집 불가
        trading_days = self._make_trading_days((2026, 1, 8), (2026, 1, 9), (2026, 1, 12), (2026, 1, 13))
        assert released_stock.is_collectible_at(datetime(2026, 1, 13), trading_days) is False

    def test_active_stock_always_collectible_after_designation(self, active_stock):
        target = datetime(2026, 6, 1)  # 먼 미래도 OK
        assert active_stock.is_collectible_at(target, []) is True

    def test_active_stock_not_collectible_before_designation(self, active_stock):
        target = datetime(2026, 1, 1)  # 지정일 전날
        assert active_stock.is_collectible_at(target, []) is False

    def test_no_post_release_trading_days(self, released_stock):
        # 해제일 이후 영업일 정보가 없으면 해제일 초과 날짜는 수집 불가
        trading_days = []
        assert released_stock.is_collectible_at(datetime(2026, 1, 8), trading_days) is False


# ─────────────────────────────────────────────────────────────────────────────
# to_dict
# ─────────────────────────────────────────────────────────────────────────────


class TestToDict:
    def test_active_stock_to_dict(self, active_stock):
        d = active_stock.to_dict()
        assert d["code"] == "000001"
        assert d["name"] == "테스트종목A"
        assert d["release_date"] is None

    def test_released_stock_to_dict(self, released_stock):
        d = released_stock.to_dict()
        assert d["release_date"] == datetime(2026, 1, 7)


# ─────────────────────────────────────────────────────────────────────────────
# DailyPriceData
# ─────────────────────────────────────────────────────────────────────────────


class TestDailyPriceData:
    def test_to_dict(self):
        dp = DailyPriceData(
            code="000001",
            name="테스트",
            date=datetime(2026, 1, 5),
            close=10000.0,
            change_rate=2.5,
        )
        d = dp.to_dict()
        assert d["code"] == "000001"
        assert d["close"] == 10000.0
        assert d["change_rate"] == 2.5
