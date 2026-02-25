import os
import pandas as pd
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

from investment_hub.domain.models import InvestmentWarningStock, DailyPriceData
from investment_hub.domain.services import calculate_returns
from investment_hub.core.ports.storage_port import StoragePort


def restore_stocks_and_prices_from_df(df: pd.DataFrame):
    """CSV DataFrame에서 종목 및 시세 도메인 모델 복원"""
    if df.empty:
        return [], {}

    stocks_dict = {}
    for _, row in df.drop_duplicates(subset=["code", "designation_date"]).iterrows():
        stocks_dict[row["code"]] = InvestmentWarningStock(
            code=str(row["code"]),
            name=str(row["name"]),
            market=str(row["market"]),
            designation_date=pd.to_datetime(row["designation_date"]),
            release_date=pd.to_datetime(row["release_date"]) if pd.notna(row["release_date"]) and row["release_date"] != "" else None
        )

    prices_dict = {}
    for code, group in df.groupby("code"):
        prices_dict[str(code)] = [
            DailyPriceData(
                code=str(code),
                name=str(row["name"]),
                date=pd.to_datetime(row["date"]),
                close=float(row["close"]),
                change_rate=float(row["change_rate"])
            ) for _, row in group.iterrows()
        ]
    return list(stocks_dict.values()), prices_dict


def save_investment_warning_excel(
    year: int,
    stocks: list,  # InvestmentWarningStock
    prices_by_code: dict,  # code -> list[DailyPriceData]
    storage: StoragePort,
    output_dir: str = "output"
):
    """투자경고 종목 분석 데이터를 엑셀로 저장 (Rich Format)"""
    wb = Workbook()
    ws = wb.active
    ws.title = f"{year}년 투자경고 분석"

    # [1] 데이터 전처리 (D+n 매핑 및 수익률 계산)
    rows_data = []
    max_days = 0
    
    for s in sorted(stocks, key=lambda x: x.designation_date or datetime.min):
        prices = sorted(prices_by_code.get(s.code, []), key=lambda x: x.date)
        if not prices:
            continue
            
        d0 = prices[0].date
        ptd = {}  # index -> price_info
        release_idx = None
        
        for i, p in enumerate(prices):
            idx = i  # 영업일 순서 (D+0, D+1, ...)
            ptd[idx] = {"close": p.close, "change_rate": p.change_rate}
            max_days = max(max_days, idx)
            # 해제일 인덱스 확인
            if s.release_date and p.date.date() == s.release_date.date():
                release_idx = idx
                
        # 수익률 계산 (도메인 서비스 활용)
        pre_return, post_return = calculate_returns(ptd, release_idx)
                    
        rows_data.append({
            "stock": s,
            "ptd": ptd,
            "release_idx": release_idx,
            "pre_return": pre_return,
            "post_return": post_return,
            "warning_days": (s.release_date - s.designation_date).days if s.release_date else None
        })

    # [2] 헤더 작성
    base_headers = ["종목명", "종목코드", "시장", "지정일", "해제일", "경고일수", "지정이후 수익률", "해제이후 수익률"]
    day_headers = [f"D+{d}" for d in range(max_days + 1)]
    ws.append(base_headers + day_headers)

    # 헤더 스타일
    header_fill = PatternFill(start_color="2F4F8F", end_color="2F4F8F", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    # [3] 데이터 행 작성
    red_fill = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")
    green_fill = PatternFill(start_color="CCFFCC", end_color="CCFFCC", fill_type="solid")
    base_col_count = len(base_headers)

    for data in rows_data:
        s = data["stock"]
        row = [
            s.name, s.code, s.market,
            s.designation_date.strftime("%Y-%m-%d") if s.designation_date else "",
            s.release_date.strftime("%Y-%m-%d") if s.release_date else "진행중",
            data["warning_days"],
            data["pre_return"],
            data["post_return"]
        ]
        
        ptd = data["ptd"]
        for d in range(max_days + 1):
            row.append(ptd[d]["close"] if d in ptd else None)
            
        ws.append(row)
        curr_row = ws.max_row
        
        # 상한가 및 해제일 강조
        for d in range(max_days + 1):
            if d not in ptd: continue
            col_idx = base_col_count + d + 1
            cell = ws.cell(row=curr_row, column=col_idx)
            
            if ptd[d]["change_rate"] >= 29.9:
                cell.fill = red_fill
            if d == data["release_idx"]:
                cell.fill = green_fill

    # [4] 서식 마무리
    # 열 너비 자동 조정
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except: pass
        ws.column_dimensions[column].width = min(max_length + 2, 30)

    # 틀 고정
    ws.freeze_panes = ws.cell(row=2, column=base_col_count + 1)

    # 저장
    xlsx_path = os.path.join(output_dir, f"투자경고종목분석({year}년).xlsx")
    storage.save_workbook(wb, xlsx_path)
