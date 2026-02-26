def calculate_returns(prices_by_idx: dict, release_idx: int | None) -> tuple[float | None, float | None]:
    """투자경고 종목의 지정/해제 전후 수익률을 계산하는 도메인 서비스.

    제공된 거래일별 가격 인덱스 딕셔너리를 사용하여 지정일(D+0) 대비 해제 전날 종가 기준(pre_return)과
    해제 전날 대비 해제일 종가 기준(post_return) 각각의 수익률을 도출합니다.

    Args:
        prices_by_idx (dict): 지정일을 0으로 매핑한 거래일 인덱스를 키로 하고 가격 정보를 값으로 갖는 딕셔너리.
        release_idx (int | None): 투자경고 해제일의 거래일 인덱스. 아직 해제되지 않은 경우 None.

    Returns:
        tuple[float | None, float | None]:
            (pre_return, post_return) 형태의 튜플.
            pre_return: 지정일 대비 해제 전날까지의 수익률 (%).
            post_return: 해제 전날 대비 해제일까지의 수익률 (%). (해제일 변동이 0%면 다음 영업일 기준 연장 계산)
            결과를 계산할 수 없는 경우 각각 None을 반환.
    """
    pre_return = None
    post_return = None

    if release_idx is not None and release_idx > 0:
        prev_idx = release_idx - 1

        # 1. 해제 전 수익률 (지정일 -> 해제 전날)
        if 0 in prices_by_idx and prev_idx in prices_by_idx:
            d0_p = prices_by_idx[0]["close"]
            prev_p = prices_by_idx[prev_idx]["close"]
            if d0_p > 0:
                pre_return = round((prev_p / d0_p - 1) * 100, 2)

        # 2. 해제 후 수익률 (해제 전날 -> 해제일)
        if prev_idx in prices_by_idx and release_idx in prices_by_idx:
            prev_p = prices_by_idx[prev_idx]["close"]
            rel_p = prices_by_idx[release_idx]["close"]
            if prev_p > 0:
                post_return = round((rel_p / prev_p - 1) * 100, 2)

                # [예외 케이스] 해제일 변동이 0%인 경우 다음 영업일까지 확장
                if post_return == 0 and (release_idx + 1) in prices_by_idx:
                    next_p = prices_by_idx[release_idx + 1]["close"]
                    post_return = round((next_p / prev_p - 1) * 100, 2)

    return pre_return, post_return
