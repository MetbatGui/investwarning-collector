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

    def export(self, year: int, stocks: list[InvestmentWarningStock], prices_by_code: dict[str, list[DailyPriceData]]) -> openpyxl.Workbook:
        """분석 결과가 담긴 엑셀 Workbook 인스턴스를 생성합니다."""
        rows_data, max_days = self._build_rows(stocks, prices_by_code)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"투자경고_{year}"
        if not rows_data: return wb

        header = ["종목명", "종목코드", "시장", "지정일", "해제일", "경고일수", "해제전등락률(%)", "해제직후등락률(%)"]
        header += [f"D+{d}" for d in range(max_days + 1)]
        ws.append(header)
        
        self._apply_header_style(ws)
        self._write_data_rows(ws, rows_data, max_days, 8)
        self._finalize_sheet_format(ws, 8)
        return wb

    def _apply_header_style(self, ws):
        """헤더 행에 배경색, 글꼴, 정렬 등 스타일을 적용합니다.

        Args:
            ws (openpyxl.worksheet.worksheet.Worksheet): 대상 워크시트.
        """
        fill, font = PatternFill("solid", "2F4F8F"), Font("FFFFFF", bold=True)
        for cell in ws[1]:
            cell.fill, cell.font, cell.alignment = fill, font, Alignment("center")

    def _write_data_rows(self, ws, rows_data, max_days, base_cnt):
        """데이터 행을 기록하고 조건부 서식(상한가 등)을 적용합니다.

        Args:
            ws (openpyxl.worksheet.worksheet.Worksheet): 대상 워크시트.
            rows_data (list[dict]): 기록할 종목 데이터 행 리스트.
            max_days (int): 전체 데이터 중 최대 경고 유지 일수 (컬럼 확장용).
            base_cnt (int): 시세 데이터가 시작되는 기준 컬럼 인덱스.
        """
        g_fill, r_fill = PatternFill("solid", "CCFFCC"), PatternFill("solid", "FFCCCC")
        for rd in rows_data:
            pre, post = self._calc_returns(rd)
            row = [rd["name"], rd["code"], rd["market"], rd["designation_date"], rd["release_date"], rd["warning_days"], pre, post]
            ptd = rd["prices_by_trading_day"]
            row.extend(ptd[d]["close"] if d in ptd else None for d in range(max_days + 1))
            ws.append(row)
            
            c_row = ws.max_row
            for d in range(max_days + 1):
                if d in ptd:
                    cell = ws.cell(c_row, base_cnt + d + 1)
                    if ptd[d]["change_rate"] >= 29.9: cell.fill = r_fill
                    elif d == rd["release_trading_day"]: cell.fill = g_fill

    def _finalize_sheet_format(self, ws, base_cnt):
        """시트의 전체적인 서식(컬럼 너비, 틀 고정)을 최종 적용합니다."""
        for col in ws.columns:
            self._adjust_column_width(ws, col)
        ws.freeze_panes = ws.cell(2, base_cnt + 1)

    def _adjust_column_width(self, ws, col):
        l = max((len(str(c.value or "")) for c in col), default=0)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(l + 2, 20)

    def _build_rows(self, filtered: list, daily_prices_by_code: dict) -> tuple[list[dict], int]:
        """엑셀 행 구성을 위해 종목과 시세 데이터를 가공하고 일자별 매핑을 구축합니다.

        Args:
            filtered (list[InvestmentWarningStock]): 필터링된 종목 목록.
            daily_prices_by_code (dict): 종목별 시세 데이터.

        Returns:
            tuple[list[dict], int]: (가공된 행 데이터 리스트, 최대 경고 유지 일수)
        """
        max_days, rows_data = 0, []
        for s in filtered:
            prices = daily_prices_by_code.get(s.code, [])
            if not prices: continue
            valid = self._filter_valid_prices(sorted(prices, key=lambda x: x.date), s.designation_date, s.release_date)
            ptd, rel_idx = self._map_trading_days(valid, s.release_date)
            if ptd:
                max_days = max(max_days, len(ptd) - 1)
                rows_data.append({
                    "name": s.name, "code": s.code, "market": s.market,
                    "designation_date": s.designation_date.strftime("%Y-%m-%d") if s.designation_date else "",
                    "release_date": s.release_date.strftime("%Y-%m-%d") if s.release_date else "진행중",
                    "warning_days": (s.release_date - s.designation_date).days if s.release_date else None,
                    "prices_by_trading_day": ptd, "release_trading_day": rel_idx,
                })
        return rows_data, max_days

    def _filter_valid_prices(self, sorted_prices, des_date, rel_date):
        """지정일 이후부터 해제일+N영업일까지의 유효 시세 데이터만 필터링합니다."""
        post_des = [p for p in sorted_prices if not des_date or p.date.date() >= des_date.date()]
        valid, rel_found, after_cnt = [], False, 0
        for p in post_des:
            if rel_date and not rel_found and p.date.date() >= rel_date.date(): rel_found = True
            if rel_found:
                if after_cnt > self.trading_days_after_release: break
                after_cnt += 1
            valid.append(p)
        return valid

    def _map_trading_days(self, valid_prices, rel_date):
        """유효 시세를 상대적 영업일(D+n) 인덱스로 매핑합니다."""
        ptd, rel_idx = {}, None
        for i, p in enumerate(valid_prices):
            ptd[i] = {"date": p.date, "close": p.close, "change_rate": p.change_rate}
            if rel_date and p.close > 0:
                if p.date.date() == rel_date.date(): rel_idx = i
                elif rel_idx is None and p.date.date() > rel_date.date(): rel_idx = i
        return ptd, rel_idx

    def _calc_returns(self, row_data: dict) -> tuple[float | None, float | None]:
        """해당 종목의 해제 전일 및 해제 직후 구간 등락률을 수식 계산합니다."""
        rel_idx, ptd = row_data["release_trading_day"], row_data["prices_by_trading_day"]
        if rel_idx is None or rel_idx <= 0: return None, None

        prev_idx = rel_idx - 1
        pre = self._calc_pct_change(ptd.get(0), ptd.get(prev_idx))
        post = self._calc_pct_change(ptd.get(prev_idx), ptd.get(rel_idx))
        return pre, post

    def _calc_pct_change(self, start_p, end_p) -> float | None:
        if start_p and end_p and start_p["close"] > 0:
            return round((end_p["close"] / start_p["close"] - 1) * 100, 2)
        return None
