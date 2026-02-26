from dataclasses import dataclass
from datetime import datetime


@dataclass
class InvestmentWarningStock:
    """KRX에서 지정한 투자경고종목 도메인 모델.

    투자경고종목으로 지정된 개별 주식의 상태(시장, 지정일, 해제일 등) 정보를 캡슐화합니다.

    Attributes:
        code (str): 종목코드 (6자리)
        name (str): 종목명
        market (str): 소속 시장 (예: '코스피', '코스닥')
        designation_date (datetime): 투자경고 지정일
        release_date (datetime | None): 투자경고 해제일. 아직 해제되지 않은 경우 None.
    """
    code: str
    name: str
    market: str
    designation_date: datetime
    release_date: datetime | None = None

    @property
    def is_active(self) -> bool:
        """현재 투자경고 지정 상태가 유지중인지 여부를 반환합니다.

        Returns:
            bool: 해제일이 없으면 True, 있으면 False.
        """
        return self.release_date is None

    @property
    def warning_days(self) -> int | None:
        """투자경고종목 지정일부터 해제일까지 소요된 달력 일수를 계산합니다.

        Returns:
            int | None: 지정일부터 해제일까지의 경과 일수.
                아직 진행 중(해제되지 않음)이면 None을 반환합니다.
        """
        if self.is_active or self.release_date is None:
            return None
        return (self.release_date - self.designation_date).days

    def is_warning_duration_valid(self, max_days: int) -> bool:
        """경고 유지 기간이 지정된 최대 일수 이내인지 확인합니다.

        데이터 수집 시 비정상적으로 길게 경고 종목으로 남아있는 케이스를 필터링하기 위해 사용됩니다.

        Args:
            max_days (int): 허용되는 최대 경고일수.

        Returns:
            bool: 현재 진행 중이거나 지정된 일수 이하로 해제된 경우 True, 그 외에는 False.
        """
        if self.is_active:
            return True
        days = self.warning_days
        return days is not None and days <= max_days

    def is_collectible_at(self, target_date: datetime, trading_days: list) -> bool:
        """특정 날짜가 당해 종목의 유효 수집 기간(이벤트 윈도우) 내에 있는지 평가합니다.

        원칙적으로 `지정일 <= 판별일 <= 해제일로부터 3영업일 차` 범위에 속할 때 수집 대상으로 판정합니다.

        Args:
            target_date (datetime): 수집 여부를 판단할 대상 날짜.
            trading_days (list): 전체 휴장일이 제외된 영업일 목록.

        Returns:
            bool: 유효한 수집 기간에 포함되면 True, 그렇지 않으면 False.
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
        limit_date = post_release[min(2, len(post_release) - 1)]
        return t_date <= limit_date

    def to_dict(self) -> dict:
        """도메인 모델의 데이터를 딕셔너리 형태로 변환합니다.

        Returns:
            dict: 모델의 속성값들을 담고 있는 딕셔너리 객체.
        """
        return {
            "code": self.code,
            "name": self.name,
            "market": self.market,
            "designation_date": self.designation_date,
            "release_date": self.release_date,
        }


@dataclass
class DailyPriceData:
    """일별 가격 및 등락 정보를 담는 도메인 모델.

    단일 영업일에 대한 개별 주식의 종가 및 등락률 정보를 캡슐화합니다.

    Attributes:
        code (str): 종목코드
        name (str): 종목명
        date (datetime): 거래 일자
        close (float): 해당 일의 종가
        change_rate (float): 전일 대비 등락률 (%)
    """
    code: str
    name: str
    date: datetime
    close: float
    change_rate: float

    def to_dict(self) -> dict:
        """가격 모델의 데이터를 딕셔너리 형태로 변환합니다.

        Returns:
            dict: 객체의 가격 및 변동성 속성값을 포함하는 딕셔너리.
        """
        return {
            "code": self.code,
            "name": self.name,
            "date": self.date,
            "close": self.close,
            "change_rate": self.change_rate,
        }
