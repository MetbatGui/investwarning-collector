"""
domain/services.py — calculate_returns 유닛 테스트
"""
import pytest
from investment_hub.domain.services import calculate_returns


def _ptd(*prices):
    """인덱스->종가 딕셔너리 생성 헬퍼 (close만 사용)"""
    return {i: {"close": p, "change_rate": 0.0} for i, p in enumerate(prices)}


class TestCalculateReturns:

    def test_both_none_when_no_release(self):
        ptd = _ptd(10000, 11000, 12000)
        pre, post = calculate_returns(ptd, release_idx=None)
        assert pre is None
        assert post is None

    def test_both_none_when_release_at_d0(self):
        # release_idx=0 이면 계산 안 함
        ptd = _ptd(10000, 11000)
        pre, post = calculate_returns(ptd, release_idx=0)
        assert pre is None
        assert post is None

    def test_pre_return_calculated(self):
        # D+0=10000, D+1(해제전날)=12000, D+2(해제일)=11000
        ptd = _ptd(10000, 12000, 11000)
        pre, post = calculate_returns(ptd, release_idx=2)
        # pre: (12000 / 10000 - 1) * 100 = 20.0%
        assert pre == pytest.approx(20.0)

    def test_post_return_calculated(self):
        ptd = _ptd(10000, 12000, 11000)
        pre, post = calculate_returns(ptd, release_idx=2)
        # post: (11000 / 12000 - 1) * 100 ≈ -8.33%
        assert post == pytest.approx(-8.33, abs=0.01)

    def test_post_return_zero_uses_next_day(self):
        # 해제일 변동이 0%인 경우 → D+3으로 계산
        # D+0=10000, D+1(pre)=11000, D+2(해제일)=11000 (0%), D+3=12000
        ptd = _ptd(10000, 11000, 11000, 12000)
        pre, post = calculate_returns(ptd, release_idx=2)
        # post_return: 해제일이 0% → 다음날(D+3=12000) 기준
        # (12000 / 11000 - 1) * 100 ≈ 9.09%
        assert post == pytest.approx(9.09, abs=0.01)

    def test_post_return_nonzero_does_not_use_next_day(self):
        # 해제일 변동이 0%가 아닌 경우 D+3 무시
        ptd = _ptd(10000, 11000, 13000, 99999)
        pre, post = calculate_returns(ptd, release_idx=2)
        # post: (13000 / 11000 - 1) * 100 ≈ 18.18%
        assert post == pytest.approx(18.18, abs=0.01)

    def test_missing_price_data_returns_none(self):
        # prev_idx(=1) 데이터가 없으면 None
        ptd = {0: {"close": 10000, "change_rate": 0}, 2: {"close": 9000, "change_rate": 0}}
        pre, post = calculate_returns(ptd, release_idx=2)
        assert pre is None
        assert post is None

    def test_zero_d0_price_returns_none_pre(self):
        # D+0 종가가 0이면 pre_return None
        ptd = {0: {"close": 0, "change_rate": 0}, 1: {"close": 5000, "change_rate": 0},
               2: {"close": 6000, "change_rate": 0}}
        pre, post = calculate_returns(ptd, release_idx=2)
        assert pre is None
