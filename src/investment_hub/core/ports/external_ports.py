from abc import ABC, abstractmethod
from datetime import datetime

from investment_hub.domain.models import InvestmentWarningStock


class MarketDataPort(ABC):
    """시장 가격 데이터 조회를 위한 포트"""

    @abstractmethod
    def get_market_ohlcv_by_date(self, date: datetime) -> dict[str, dict[str, float]]:
        """특정 날짜의 전 종목 시세를 조회합니다. (code -> {close, change_rate})"""
        pass

    @abstractmethod
    def get_trading_days(self, start_date: datetime, end_date: datetime) -> list[datetime]:
        """지정된 기간 내의 실제 거래일 목록을 조회합니다."""
        pass


class InvestmentWarningPort(ABC):
    """외부 소스(KRX 등)로부터 투자경고 지정 정보를 조회하기 위한 포트"""

    @abstractmethod
    def fetch_stocks(self, start_date: str, end_date: str) -> list[InvestmentWarningStock]:
        """지정된 기간 내의 투자경고 지정 목록을 조회합니다."""
        pass
