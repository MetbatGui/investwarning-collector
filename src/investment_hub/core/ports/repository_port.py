"""투자경고종목 레포지터리 포트 (인터페이스) 정의."""

from abc import ABC, abstractmethod

from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock


class WarningStockRepository(ABC):
    """투자경고종목 데이터의 저장·조회를 담당하는 레포지터리 포트.

    구현체는 Parquet, SQLite 등 다양한 백엔드를 사용할 수 있습니다.
    연간 단위로 데이터를 관리하며, 각 연도별 파티션에
    ``InvestmentWarningStock`` (종목 메타) + ``DailyPriceData`` (일별 시세)를 함께 보관합니다.

    Note:
        모든 메서드는 동기(blocking) 방식으로 동작합니다.
    """

    @abstractmethod
    def save_year(
        self,
        year: int,
        stocks: list[InvestmentWarningStock],
        prices: dict[str, list[DailyPriceData]],
    ) -> None:
        """특정 연도의 종목·시세 데이터를 전량 저장합니다 (덮어쓰기).

        기존 파티션이 존재하면 완전히 대체합니다. 원자적(atomic) 저장을 보장해야 합니다.

        Args:
            year: 저장 대상 연도 (예: 2025).
            stocks: 해당 연도의 투자경고 종목 목록.
            prices: 종목코드 → 일별 시세 목록 매핑.

        Raises:
            IOError: 저장 디렉토리 접근 실패 또는 디스크 공간 부족 시.
        """

    @abstractmethod
    def load_year(
        self,
        year: int,
    ) -> tuple[list[InvestmentWarningStock], dict[str, list[DailyPriceData]]]:
        """특정 연도의 종목·시세 데이터를 로드합니다.

        Args:
            year: 조회할 연도.

        Returns:
            (stocks, prices) 튜플.
            - stocks: 해당 연도의 투자경고 종목 목록 (지정일 오름차순 정렬).
            - prices: 종목코드 → 일별 시세 목록 매핑 (날짜 오름차순 정렬).
            파티션이 없으면 ([], {}) 반환.
        """

    @abstractmethod
    def append_year(
        self,
        year: int,
        stocks: list[InvestmentWarningStock],
        prices: dict[str, list[DailyPriceData]],
    ) -> None:
        """기존 파티션에 새 데이터를 증분 병합합니다.

        ``(code, designation_date, date)`` 복합 키 기준으로 중복을 제거하며,
        동일 키가 있을 경우 새 데이터를 우선합니다.
        파티션이 없으면 ``save_year()`` 와 동일하게 동작합니다.

        Args:
            year: 대상 연도.
            stocks: 추가할 종목 목록.
            prices: 추가할 종목코드 → 일별 시세 목록 매핑.

        Raises:
            IOError: 저장 실패 시.
        """

    @abstractmethod
    def year_exists(self, year: int) -> bool:
        """해당 연도의 파티션이 존재하는지 확인합니다.

        Args:
            year: 확인할 연도.

        Returns:
            파티션 파일이 존재하면 True.
        """

    @abstractmethod
    def list_available_years(self) -> list[int]:
        """저장된 파티션 연도 목록을 오름차순으로 반환합니다.

        Returns:
            사용 가능한 연도 리스트 (예: [2020, 2021, 2022]).
        """
