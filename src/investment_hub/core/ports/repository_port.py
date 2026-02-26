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
        """특정 연도의 종목 및 시세 데이터를 전량 덮어쓰기 방식으로 저장합니다.

        기존 파티션이 존재하면 완전히 대체하며, 원자적(atomic) 저장을 보장해야 합니다.

        Args:
            year (int): 저장 대상 연도 (예: 2025).
            stocks (list[InvestmentWarningStock]): 해당 연도의 투자경고 종목 목록.
            prices (dict[str, list[DailyPriceData]]): 종목코드를 키로, 일별 시세 목록을 값으로 갖는 매핑.

        Raises:
            IOError: 저장 디렉토리 접근 실패 또는 디스크 공간 부족 등 I/O 문제 발생 시.
        """

    @abstractmethod
    def load_year(
        self,
        year: int,
    ) -> tuple[list[InvestmentWarningStock], dict[str, list[DailyPriceData]]]:
        """특정 연도의 저장된 파티션에서 종목 및 시세 데이터를 로드합니다.

        Args:
            year (int): 조회할 대상 연도.

        Returns:
            tuple[list[InvestmentWarningStock], dict[str, list[DailyPriceData]]]:
                (stocks, prices) 형태의 튜플 반환.
                stocks: 해당 연도의 투자경고 종목 목록 (지정일 오름차순 정렬).
                prices: 종목코드를 키로 하는 일별 시세 목록 매핑 (날짜 오름차순 정렬).
                만약 해당 연도의 파티션이 없으면 ([], {}) 쌍을 반환합니다.
        """

    @abstractmethod
    def append_year(
        self,
        year: int,
        stocks: list[InvestmentWarningStock],
        prices: dict[str, list[DailyPriceData]],
    ) -> None:
        """기존 파티션에 새로운 데이터를 증분(Append) 병합합니다.

        `(code, designation_date, date)` 복합 키 기준으로 중복을 제거하며,
        동일 키가 있을 경우 전달받은 새 데이터를 덮어써서 우선 반영합니다.
        만약 해당 연도 파티션 파일이 없으면 `save_year()`와 동일하게 1회성 전체 저장을 수행합니다.

        Args:
            year (int): 대상 저장 연도.
            stocks (list[InvestmentWarningStock]): 추가/갱신할 종목 목록.
            prices (dict[str, list[DailyPriceData]]): 추가/갱신할 일별 시세 매핑.

        Raises:
            IOError: 병합 과정에서의 저장 실패 또는 무결성 보장 실패 시 예외 발생.
        """

    @abstractmethod
    def year_exists(self, year: int) -> bool:
        """해당 연도의 물리적 파티션 데이터가 시스템에 존재하는지 확인합니다.

        Args:
            year (int): 존재 여부를 확인할 타켓 연도.

        Returns:
            bool: 파티션 파일/디렉토리가 유효하게 존재하면 True, 없으면 False.
        """

    @abstractmethod
    def list_available_years(self) -> list[int]:
        """저장소에 보관된 사용 가능한 전체 연도 목록을 반환합니다.

        Returns:
            list[int]: 수집/저장이 완료되어 읽기 가능한 연도 정수형 리스트 (오름차순).
        """
