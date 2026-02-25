from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class InvestmentWarningStock:
    code: str
    name: str
    market: str
    designation_date: datetime
    release_date: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        """현재 투자경고 지정 상태(미해제)인지 여부"""
        return self.release_date is None

    @property
    def warning_days(self) -> Optional[int]:
        """지정일부터 해제일까지의 달력 일수. 아직 진행 중이면 None."""
        if self.is_active:
            return None
        return (self.release_date - self.designation_date).days

    def is_warning_duration_valid(self, max_days: int) -> bool:
        """경고 기간이 지정된 최대 일수 이내인지 확인 (주로 60일 데이터 필터링용)"""
        if self.is_active:
            return True
        days = self.warning_days
        return days is not None and days <= max_days

    def is_collectible_at(self, target_date: datetime, trading_days: list) -> bool:
        """
        특정 날짜가 수집/기입 유효 범위 내에 있는지 확인.
        원칙: designation_date <= target_date <= (release_date + 3 trading days)
        """
        # 자정 기준으로 비교
        t_date = target_date.date()
        d_date = self.designation_date.date()
        
        if t_date < d_date:
            return False
            
        if self.release_date is None:
            # 아직 해제되지 않은 경우 지정일 이후면 모두 수집 대상
            return True
            
        # 해제일 이후 3영업일까지만 수집
        rel_date = self.release_date.date()
        if t_date <= rel_date:
            return True
            
        # 해제일보다 늦은 영업일들 찾기
        post_release = sorted([d.date() for d in trading_days if d.date() > rel_date])
        if not post_release:
            return False
            
        # 최대 3번째 영업일까지만 허용
        limit_date = post_release[min(2, len(post_release)-1)]
        return t_date <= limit_date

    def to_dict(self):
        return {
            "code": self.code,
            "name": self.name,
            "market": self.market,
            "designation_date": self.designation_date,
            "release_date": self.release_date
        }


@dataclass
class DailyPriceData:
    """일별 가격 데이터 (종가 + 등락률)"""
    code: str
    name: str
    date: datetime
    close: float
    change_rate: float  # 등락률 (%)

    def to_dict(self):
        return {
            "code": self.code,
            "name": self.name,
            "date": self.date,
            "close": self.close,
            "change_rate": self.change_rate
        }
