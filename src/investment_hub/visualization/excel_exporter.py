from datetime import datetime

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from investment_hub.domain.models import DailyPriceData, InvestmentWarningStock


class WarningExcelExporter:
    """도메인 모델 데이터를 바탕으로 스타일링이 반영된 엑셀 분석 시트를 생성합니다."""

    def __init__(self, trading_days_after_release: int = 3):
        """WarningExcelExporter 초기화.

        Args:
            trading_days_after_release (int): 해제일 이후 추가로 보여줄 영업일수. 기본값 3.
        """
        self.trading_days_after_release = trading_days_after_release
        # 컬러 테마 정의
        self.colors = {
            "header": PatternFill(start_color="EEEEEE", end_color="EEEEEE", fill_type="solid"),
            "release": PatternFill(start_color="CCEEFF", end_color="CCEEFF", fill_type="solid"),  # 연한 파랑
            "ceiling": PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid"),  # 연한 빨강
        }

    def export(self, year: int, stocks: list[InvestmentWarningStock], prices: dict[str, list[DailyPriceData]]) -> openpyxl.Workbook:
        """연도별 종목 및 시세 데이터를 스타일링이 적용된 엑셀 워크북으로 변환합니다.

        Args:
            year (int): 대상 연도.
            stocks (list[InvestmentWarningStock]): 투자경고 종목 리스트.
            prices (dict[str, list[DailyPriceData]]): 종목코드별 일별 시세 데이터 딕셔너리.

        Returns:
            openpyxl.Workbook: 생성된 엑셀 워크북 객체.
        """
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"{year}년 투자경고종목분석"

        rows_data, max_days = self._build_rows(stocks, prices)
        self._write_headers(ws, max_days)
        self._write_data(ws, rows_data, max_days)
        self._apply_styles(ws, max_days)

        return wb

    def _build_rows(self, stocks: list[InvestmentWarningStock], prices: dict[str, list[DailyPriceData]]) -> tuple[list[dict], int]:
        """엑셀 행으로 변환할 데이터를 구성하고 최대 영업일수(컬럼 수 파악용)를 계산합니다.

        Args:
            stocks (list[InvestmentWarningStock]): 투자경고 종목 리스트.
            prices (dict[str, list[DailyPriceData]]): 종목코드별 시세 데이터.

        Returns:
            tuple[list[dict], int]: (행 데이터 리스트, 최대 영업일수).
        """
        rows_data: list[dict] = []
        max_days = 0

        for s in stocks:
            p_list = prices.get(s.code, [])
            if not p_list:
                continue

            # 지정일 이후 데이터만 필터링
            post_des = [p for p in p_list if p.date.date() >= s.designation_date.date()]
            if not post_des:
                continue

            # 해제일 이후 N일까지만 포함 (해제일 미정인 경우 전체포함)
            rel_date = s.release_date
            valid = self._filter_valid_prices(post_des, rel_date)

            if valid:
                ptd = {i: p for i, p in enumerate(valid)}
                max_days = max(max_days, len(ptd) - 1)  # D+N 에서 N의 최대값

                # 해제일의 인덱스 찾기 (Raw: 표기용, Eff: 계산용)
                raw_idx = s.get_raw_release_index(valid)
                eff_idx = s.get_effective_release_index(valid)

                # 경고일수: 지정일부터 '실제 해제 전날'까지의 영업일수
                w_days = raw_idx if raw_idx is not None else len(ptd)

                rows_data.append({
                    "name": s.name, "code": s.code, "market": s.market,
                    "designation_date": s.designation_date.strftime("%Y-%m-%d") if s.designation_date else "",
                    "release_date": s.release_date.strftime("%Y-%m-%d") if s.release_date else "진행중",
                    "warning_days": w_days,
                    "prices_by_trading_day": ptd,
                    "raw_release_idx": raw_idx,
                    "effective_release_idx": eff_idx,
                })
        return rows_data, max_days

    def _filter_valid_prices(self, post_des: list[DailyPriceData], rel_date: datetime | None) -> list[DailyPriceData]:
        """시세 데이터에서 중복을 제거하고 해제일 기준 유효 수집 윈도우만큼 필터링합니다.

        Args:
            post_des (list[DailyPriceData]): 지정일 이후의 시세 리스트.
            rel_date (datetime | None): 해제일. 미정인 경우 전체 반환.

        Returns:
            list[DailyPriceData]: 필터링된 시세 리스트.
        """
        seen_dates = set()
        unique_list = []
        for p in post_des:
            dt = p.date.date()
            if dt not in seen_dates:
                seen_dates.add(dt)
                unique_list.append(p)

        if not rel_date:
            return unique_list

        valid = []
        count_after = 0
        for p in unique_list:
            valid.append(p)
            if p.date.date() > rel_date.date():
                count_after += 1
            if count_after >= self.trading_days_after_release:
                break
        return valid

    def _write_headers(self, ws: openpyxl.worksheet.worksheet.Worksheet, max_days: int):
        """엑셀 시트의 헤더 행을 작성합니다.

        Args:
            ws (Worksheet): 대상 워크시트.
            max_days (int): D+N 컬럼 생성을 위한 최대 시퀀스 번호.
        """
        # _write_data에서 헤더를 함께 처리하므로 빈 함수로 둡니다.
        pass

    def _write_data(self, ws: openpyxl.worksheet.worksheet.Worksheet, rows_data: list[dict], max_days: int):
        """행별 데이터를 시트에 기록하고 가격 셀에 대한 조건부 컬러링을 적용합니다.

        Args:
            ws (Worksheet): 대상 워크시트.
            rows_data (list[dict]): 기록할 데이터 리스트.
            max_days (int): 전체 행의 일관성을 맞추기 위한 헤더 최대 일수.
        """
        header = ["종목명", "종목코드", "시장", "지정일", "해제일", "경고일수", "해제전등락률(%)", "해제직후등락률(%)"]
        header += [f"D+{d}" for d in range(max_days + 1)]
        ws.append(header)

        for row in rows_data:
            ptd = row["prices_by_trading_day"]
            raw_idx = row["raw_release_idx"]
            eff_idx = row["effective_release_idx"]

            pre_change, post_change = "-", "-"
            if raw_idx is not None:
                # 해제 전 성능: 첫날부터 '실제 해제 전날'까지
                pre = self._calc_pct_change(ptd.get(0), ptd.get(raw_idx - 1))
                pre_change = str(pre) if pre is not None else "-"

            if raw_idx is not None and eff_idx is not None:
                # 해제 후 성능: '실제 해제 전날'부터 '유효 해제일'까지
                post = self._calc_pct_change(ptd.get(raw_idx - 1), ptd.get(eff_idx))
                post_change = str(post) if post is not None else "-"

            basic_vals = [
                row["name"], row["code"], row["market"],
                row["designation_date"], row["release_date"],
                row["warning_days"], pre_change, post_change
            ]

            # 행 추가
            current_row_idx = ws.max_row + 1
            for col_idx, val in enumerate(basic_vals, 1):
                cell = ws.cell(row=current_row_idx, column=col_idx, value=val)
                # 해제일 컬러링 (E열은 5번째)
                if col_idx == 5 and val != "진행중":
                    cell.fill = self.colors["release"]

            # 가격 데이터 추가 및 컬러링
            for i in range(max_days + 1):
                col_idx = 9 + i # I열부터 시작
                p = ptd.get(i)
                if p:
                    cell = ws.cell(row=current_row_idx, column=col_idx, value=int(p.close))
                    # 1. 상한가 컬러링 (29.5% 이상)
                    if p.change_rate >= 29.5:
                        cell.fill = self.colors["ceiling"]
                    # 2. 해제일 컬러링 (Raw 인덱스 기준 파란색 배경 복구)
                    if raw_idx == i and cell.fill.start_color.index == '00000000':
                        cell.fill = self.colors["release"]

    def _apply_styles(self, ws: openpyxl.worksheet.worksheet.Worksheet, max_days: int):
        """틀 고정, 헤더 스타일, 데이터 정렬, BestFit 너비 조정을 포함한 서식을 적용합니다.

        Args:
            ws (Worksheet): 대상 워크시트.
            max_days (int): 스타일을 적용할 전체 컬럼 범위를 잡기 위한 값.
        """
        # 1. 상단틀 고정
        ws.freeze_panes = "I2"

        # 2. 헤더 스타일
        for cell in ws[1]:
            cell.fill = self.colors["header"]
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")

        # 3. 기본 정렬 (메타 정보 영역)
        for row in ws.iter_rows(min_row=2):
            for cell in row[:8]:
                cell.alignment = Alignment(horizontal="center")

        # 4. BestFit (컬럼 너비 자동 조정)
        for col in ws.columns:
            max_length = 0
            column_letter = get_column_letter(col[0].column)
            for cell in col:
                try:
                    if cell.value:
                        length = len(str(cell.value))
                        # 한글 등 전각 문자 고려 (대략적인 보정)
                        for char in str(cell.value):
                            if ord(char) > 128:
                                length += 1
                        if length > max_length:
                            max_length = length
                except Exception:
                    pass
            adjusted_width = (max_length + 2)
            ws.column_dimensions[column_letter].width = min(adjusted_width, 50) # 너무 넓어지지 않게 상한선

    def _get_warning_performance(self, ptd: dict, rel_idx: int) -> tuple[float | None, float | None]:
        if rel_idx is None or rel_idx <= 0:
            return None, None

        prev_idx = rel_idx - 1
        pre = self._calc_pct_change(ptd.get(0), ptd.get(prev_idx))
        post = self._calc_pct_change(ptd.get(prev_idx), ptd.get(rel_idx))
        return pre, post

    def _calc_pct_change(self, start_p: DailyPriceData | None, end_p: DailyPriceData | None) -> float | None:
        """두 시점 사이의 종가 기준 수익률을 계산합니다.

        Args:
            start_p (DailyPriceData | None): 기준 시점 가격 데이터.
            end_p (DailyPriceData | None): 종료 시점 가격 데이터.

        Returns:
            float | None: 백분율 수익률 (%). 데이터 부족 시 None.
        """
        if start_p and end_p and start_p.close > 0:
            return round((end_p.close / start_p.close - 1) * 100, 2)
        return None
