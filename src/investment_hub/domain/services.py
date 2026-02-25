def calculate_returns(prices_by_idx: dict, release_idx: int | None) -> tuple[float | None, float | None]:
    """
    투자경고 지정/해제 전후의 수익률을 계산합니다.
    - pre_return: 지정일(D+0) 대비 해제 전날 종가 기준
    - post_return: 해제 전날 대비 해제일 종가 기준
    - [예외]: 해제일 등락률이 0%인 경우 그 다음 영업일(release_idx + 1)을 기준으로 계산함
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
