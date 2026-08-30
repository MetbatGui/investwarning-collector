"""KRX 거래소 휴장일 판정 및 영업일 계산 공통 유틸리티.

weekly_gainers/src/application/services/calendar_service.py에서 추출한 정규 구현.
KRX 공시 채널(open.krx.co.kr)의 OPN99000001.jspx API로 연도별 휴장일을 조회해
캐시하고, 이를 기준으로 특정 날짜가 휴장일인지, 최근/다음 영업일이 언제인지 계산한다.

Projects/handoff_guide.md의 "공통 유틸리티" 절 참고 - 이 파일을 프로젝트별로
그대로 복사해 각자의 infrastructure 계층에 배치한다(모노레포/공유 패키지가
아니라 벤더링 방식). 프로젝트마다 도메인 포트(ABC) 상속 여부가 다를 수 있으므로
이 파일 자체는 순수 클래스로 두고, 필요한 프로젝트에서 어댑터로 감싸 쓴다.
"""

import logging
import time
from datetime import date, timedelta
from typing import Optional, Set, Tuple

import requests

logger = logging.getLogger(__name__)


class KrxCalendarService:
    """KRX 거래소 휴장일을 고려하여 수집 대상 기간을 계산하는 서비스."""

    def __init__(self):
        self._holidays_cache: dict[str, Set[date]] = {}

    def _fetch_krx_holidays(self, year: str) -> Set[date]:
        """KRX OPN99000001.jspx API를 호출하여 휴장일 집합을 반환합니다."""
        if year in self._holidays_cache:
            return self._holidays_cache[year]

        url_otp = "https://open.krx.co.kr/contents/COM/GenerateOTP.jspx"
        url_data = "https://open.krx.co.kr/contents/OPN/99/OPN99000001.jspx"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "Referer": "https://open.krx.co.kr/contents/MKD/01/0110/01100305/MKD01100305.jsp",
        }

        try:
            # 1. OTP 발급
            params = {
                "bld": "MKD/01/0110/01100305/mkd01100305_01",
                "name": "form",
                "_": int(time.time() * 1000),
            }
            resp_otp = requests.get(url_otp, params=params, headers=headers, timeout=10)
            if resp_otp.status_code != 200:
                return set()
            otp = resp_otp.text.strip()

            # 2. 데이터 조회
            payload = {
                "search_bas_yy": year,
                "gridTp": "KRX",
                "pagePath": "/contents/MKD/01/0110/01100305/MKD01100305.jsp",
                "code": otp,
            }
            resp_data = requests.post(url_data, data=payload, headers=headers, timeout=10)
            if resp_data.status_code != 200:
                return set()

            holidays_set = set()
            result = resp_data.json()
            rows = result.get("block1", [])
            for row in rows:
                date_str = row.get("calnd_dd")
                if date_str:
                    y, m, d = map(int, date_str.split("-"))
                    holidays_set.add(date(y, m, d))

            self._holidays_cache[year] = holidays_set
            return holidays_set
        except Exception as e:
            logger.warning(f"[KrxCalendarService] {year}년 KRX 휴장일 조회 중 예외 발생: {e}")
            return set()

    def is_holiday(self, target_date: date) -> bool:
        """주말이거나 KRX 고시 휴장일이면 True 반환."""
        if target_date.weekday() >= 5:
            return True
        # 근로자의 날(5월 1일)은 항상 주식 시장 휴장이므로 법적 보정
        if target_date.month == 5 and target_date.day == 1:
            return True
        year_str = str(target_date.year)
        holidays_set = self._fetch_krx_holidays(year_str)
        return target_date in holidays_set

    def get_week_dates(self, year: int, week: int) -> Tuple[date, date]:
        """특정 ISO 주차의 시작일(월)과 종료일(금)을 반환합니다."""
        first_day = date(year, 1, 4)
        first_monday = first_day - timedelta(days=first_day.weekday())
        target_monday = first_monday + timedelta(weeks=week - 1)
        target_friday = target_monday + timedelta(days=4)
        return target_monday, target_friday

    def get_last_trading_day(self, target_date: date) -> date:
        """주어진 날짜 이전(당일 포함)의 가장 최근 영업일을 반환합니다."""
        curr = target_date
        while True:
            if self.is_holiday(curr):
                curr -= timedelta(days=1)
                continue
            return curr

    def get_first_trading_day(self, target_date: date) -> date:
        """주어진 날짜 이후(당일 포함)의 가장 첫 영업일을 반환합니다."""
        curr = target_date
        while True:
            if self.is_holiday(curr):
                curr += timedelta(days=1)
                continue
            return curr

    def get_trading_range_in_period(self, start_date: date, end_date: date) -> Tuple[Optional[date], Optional[date]]:
        """지정된 임의 기간 내에서 휴장일을 제외한 첫 거래일과 마지막 거래일을 반환합니다."""
        trading_days = []
        curr = start_date
        while curr <= end_date:
            if not self.is_holiday(curr):
                trading_days.append(curr)
            curr += timedelta(days=1)

        if not trading_days:
            return None, None

        return trading_days[0], trading_days[-1]
