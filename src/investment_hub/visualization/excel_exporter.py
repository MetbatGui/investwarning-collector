"""Excel 리포트 생성 전용 Exporter."""

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock


class WarningExcelExporter:
    """수집 완료된 투자경고종목 데이터와 일별 시세를 엑셀 워크북 형태로 변환하는 시각화 도구.

    저장소(Port/Adapter)에 의존하지 않고, 순수 데이터(Entity) 기반으로 openpyxl 구조체를 반환하는
    단일 책임 원칙을 준수합니다. 서식(색상, 정렬, 틀 고정) 렌더링을 내장하고 있습니다.
    """

    def __init__(self, trading_days_after_release: int = 3):
        self.trading_days_after_release = trading_days_after_release

    def export(
        self, year: int, stocks: list[InvestmentWarningStock], prices_by_code: dict[str, list[DailyPriceData]]
    ) -> openpyxl.Workbook:
        """연간 시세 데이터와 종목 목록을 결합하여 분석결과가 담긴 엑셀 Workbook 인스턴스를 생성합니다.

        Args:
            year (int): 리포트가 생성되는 기준 연도 (예: 2025). 시트명에 반영됩니다.
            stocks (list[InvestmentWarningStock]): 추출 대상이 되는 투자경고 종목들의 리스트.
            prices_by_code (dict[str, list[DailyPriceData]]): 종목코드 키와 일자별 가격 데이터 값의 매핑.

        Returns:
            openpyxl.Workbook: 스타일 서식(배경, 글꼴 등)과 데이터가 전부 기입된 엑셀 내부 객체.
        """
        rows_data, max_days = self._build_rows(stocks, prices_by_code)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"투자경고_{year}"

        if not rows_data:
            return wb

        # ── 헤더 ────────────────────────────────────────────────────────────
        base_headers = [
            "종목명",
            "종목코드",
            "시장",
            "지정일",
            "해제일",
            "경고일수",
            "해제전등락률(%)",
            "해제직후등락률(%)",
        ]
        day_headers = [f"D+{d}" for d in range(max_days + 1)]
        header = base_headers + day_headers
        ws.append(header)

        # 헤더 스타일
        header_fill = PatternFill(start_color="2F4F8F", end_color="2F4F8F", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        # ── 데이터 행 ────────────────────────────────────────────────────────
        green_fill = PatternFill(start_color="CCFFCC", end_color="CCFFCC", fill_type="solid")
        red_fill = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")
        base_col_count = len(base_headers)

        for row_data in rows_data:
            pre_return, post_return = self._calc_returns(row_data)

            row = [
                row_data["name"],
                row_data["code"],
                row_data["market"],
                row_data["designation_date"],
                row_data["release_date"],
                row_data["warning_days"],
                pre_return,
                post_return,
            ]

            ptd = row_data["prices_by_trading_day"]
            for d in range(max_days + 1):
                row.append(ptd[d]["close"] if d in ptd else None)

            ws.append(row)

            # 셀 배경 강조 (상한가 붉은색, 해제일 녹색)
            current_row = ws.max_row
            for d in range(max_days + 1):
                if d not in ptd:
                    continue
                col_idx = base_col_count + d + 1
                cell = ws.cell(row=current_row, column=col_idx)

                change_rate = ptd[d]["change_rate"]
                if change_rate >= 29.9:
                    cell.fill = red_fill
                elif d == row_data["release_trading_day"]:
                    cell.fill = green_fill

        # ── 열 너비 자동 조정 및 틀 고정 ────────────────────────────────────
        for col_idx, col_cells in enumerate(ws.columns, 1):
            max_len = max((len(str(c.value or "")) for c in col_cells), default=0)
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 20)

        ws.freeze_panes = ws.cell(row=2, column=base_col_count + 1)

        return wb

    def _build_rows(
        self, filtered: list[InvestmentWarningStock], daily_prices_by_code: dict[str, list[DailyPriceData]]
    ) -> tuple[list[dict], int]:
        """도메인 모델들로부터 엑셀 각 행(Row)에 주입될 딕셔너리 데이터와 최장 추적일수를 구성합니다.

        Args:
            filtered (list[InvestmentWarningStock]): 필터링된 투자경고 후보 종목 리스트.
            daily_prices_by_code (dict[str, list[DailyPriceData]]): 종목코드 매핑 주가 데이터.

        Returns:
            tuple[list[dict], int]:
                - list[dict]: 지정일자, 해제일자, 유효 일별 가격등이 가공된 행 데이터 목록.
                - int: 데이터 중 해제일 후 추적된 최장 D+n 영업일 숫자 (`max_days`).
        """
        max_days = 0
        rows_data = []

        for stock_info in filtered:
            code = stock_info.code
            daily_prices = daily_prices_by_code.get(code, [])
            if not daily_prices:
                continue

            daily_prices_sorted = sorted(daily_prices, key=lambda x: x.date)
            designation_date = stock_info.designation_date
            release_date = stock_info.release_date

            # 해제일 인덱스 탐색
            release_trading_day_orig = None
            for idx, dp in enumerate(daily_prices_sorted):
                if release_date and dp.date.date() == release_date.date():
                    release_trading_day_orig = idx
                    break
            if release_date and release_trading_day_orig is None:
                for idx, dp in enumerate(daily_prices_sorted):
                    if dp.date.date() >= release_date.date():
                        release_trading_day_orig = idx
                        break

            # valid_prices: 지정일부터 해제일 + n일 (n=trading_days_after_release)
            valid_prices = []

            # 1단계: 지정일 이후 데이터만 필터링
            post_designation_prices = []
            for dp in daily_prices_sorted:
                if designation_date and dp.date.date() < designation_date.date():
                    continue
                post_designation_prices.append(dp)

            # 2단계: 해제일 찾고 valid_prices 구성
            release_found = False
            days_after_release = 0

            for dp in post_designation_prices:
                if release_date and not release_found:
                    # 지정일과 같거나 큰 날짜를 찾고 있으므로
                    if dp.date.date() >= release_date.date():
                        release_found = True

                if release_found:
                    if days_after_release > self.trading_days_after_release:
                        break
                    days_after_release += 1

                valid_prices.append(dp)

            prices_by_trading_day = {}
            new_release_trading_day = None
            for i, dp in enumerate(valid_prices):
                prices_by_trading_day[i] = {
                    "date": dp.date,
                    "close": dp.close,
                    "change_rate": dp.change_rate,
                }
                if release_date:
                    if dp.date.date() == release_date.date() and dp.close > 0:
                        new_release_trading_day = i
                    elif new_release_trading_day is None and dp.date.date() > release_date.date() and dp.close > 0:
                        new_release_trading_day = i

            if prices_by_trading_day:
                max_days = max(max_days, len(prices_by_trading_day) - 1)

            rows_data.append(
                {
                    "name": stock_info.name,
                    "code": code,
                    "market": stock_info.market,
                    "designation_date": designation_date.strftime("%Y-%m-%d") if designation_date else "",
                    "release_date": release_date.strftime("%Y-%m-%d") if release_date else "진행중",
                    "warning_days": (release_date - designation_date).days if release_date else None,
                    "prices_by_trading_day": prices_by_trading_day,
                    "release_trading_day": new_release_trading_day,
                }
            )

        return rows_data, max_days

    def _calc_returns(self, row_data: dict) -> tuple[float | None, float | None]:
        """해당 종목의 해제 전일 및 해제 직후 구간 등락률을 수식 계산합니다.

        Args:
            row_data (dict): `_build_rows` 에서 반환된 단일 종목 행위 데이터 매핑.

        Returns:
            tuple[float | None, float | None]:
                - 해제 전 등락률 (%)
                - 해제 직후 등락률 (%), 데이터가 없거나 유효하지 않으면 None.
        """
        pre_return = None
        post_return = None
        release_day = row_data["release_trading_day"]
        ptd = row_data["prices_by_trading_day"]

        # 해제일이 지정일 이후여야 함 (최소 D+1)
        if release_day is not None and release_day > 0:
            prev_day = release_day - 1

            # 1. 해제전 등락률
            if 0 in ptd and prev_day in ptd:
                d0_price = ptd[0]["close"]
                prev_price = ptd[prev_day]["close"]
                if d0_price > 0:
                    pre_return = round((prev_price / d0_price - 1) * 100, 2)

            # 2. 해제직후 등락률
            if prev_day in ptd and release_day in ptd:
                prev_price = ptd[prev_day]["close"]
                rel_price = ptd[release_day]["close"]
                if prev_price > 0:
                    post_return = round((rel_price / prev_price - 1) * 100, 2)

        return pre_return, post_return
