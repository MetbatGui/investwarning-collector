from abc import ABC, abstractmethod
from datetime import datetime

from investment_hub.domain.models import InvestmentWarningStock


class MarketDataPort(ABC):
    """주식 시장 가격 및 영업일 데이터를 외부에서 수집/조회하기 위한 포트 인터페이스.

    실제 구현체는 pykrx 라이브러리 등이 될 수 있으며, 특정 날짜의 종가나 거래 정지 등을 판별하는 데 사용.
    """

    @abstractmethod
    def get_market_ohlcv_by_date(self, date: datetime) -> dict[str, dict[str, float]]:
        """특정 날짜의 시장 전체 종목 시세를 조히하여 종목코드별 데이터로 변환해 반환합니다.

        해당 날짜가 휴장일이거나 데이터가 아직 공개되지 않은 경우 빈 매핑을 반환할 수 있어야 합니다.

        Args:
            date (datetime): OHLCV 조회를 요청할 영업일.

        Returns:
            dict[str, dict[str, float]]:
                종목코드(code)를 키로 하고, 해당 종목의 `close`(종가) 및 `change_rate`(등락률)을
                가지는 내부 딕셔너리 구조의 매핑 정보를 반환합니다.
        """
        pass

    @abstractmethod
    def get_trading_days(self, start_date: datetime, end_date: datetime) -> list[datetime]:
        """시작일부터 종료일 사이의 실제 증시 개장일(영업일) 목록을 가져옵니다.

        투자경고 해제일 대비 '영업일수' 계산이나, 지정일 이후의 가격 순회 시 공휴일을 필터링하는 데 필수적입니다.

        Args:
            start_date (datetime): 조회 시작일.
            end_date (datetime): 조회 종료일.

        Returns:
            list[datetime]: 시작일 <= 거래일 <= 종료일인 영업일 원소들의 오름차순 리스트.
        """
        pass


class InvestmentWarningPort(ABC):
    """증권사 거래소 시스템(KRX KIND 등)에서 투자경고 지정 목록을 조회하기 위한 포트 인터페이스."""

    @abstractmethod
    def fetch_stocks(self, start_date: str, end_date: str) -> list[InvestmentWarningStock]:
        """시작일자와 종료일자 범위 내에 공시된 투자경고종목 지정 및 해제 목록을 가져옵니다.

        Args:
            start_date (str): 'YYYY-MM-DD' 형식의 조회 윈도우 시작일.
            end_date (str): 'YYYY-MM-DD' 형식의 조회 윈도우 마감일.

        Returns:
            list[InvestmentWarningStock]: 포싱이 완료된 도메인 모델(종목) 리스트.
        """
        pass
