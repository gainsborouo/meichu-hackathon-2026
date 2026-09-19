from app.services.reward_rules import extract_rules


def test_extracts_domestic_and_overseas_rates_separately() -> None:
    rules = extract_rules(
        "國內一般消費享 1% LINE POINTS 回饋無上限；"
        "海外實體/線上消費享 2.8% LINE POINTS 回饋無上限。"
    )
    by_scope = {r["scope"]: r for r in rules}
    assert by_scope["domestic"]["rate"] == 0.01
    assert round(by_scope["overseas"]["rate"], 3) == 0.028
    assert by_scope["domestic"]["unlimited"] is True


def test_extracts_cap_registration_and_ranges() -> None:
    [rule] = extract_rules("完成登錄享最高 5% 回饋（加碼 2.2%），每戶每月回饋上限 450 點")
    assert rule["rate"] == 0.022 and rule["rate_max"] == 0.05
    assert rule["cap_amount"] == 450 and rule["cap_unit"] == "點"
    assert rule["requires_registration"] is True


def test_discount_notation_becomes_a_rate() -> None:
    [rule] = extract_rules("館內指定專櫃刷卡購物享9折至95折優惠")
    assert rule["rate"] == 0.05 and rule["rate_max"] == 0.1  # 95折=5%, 9折=10%


def test_unquantifiable_reward_yields_no_rule() -> None:
    assert extract_rules("享館內每日最高2至4小時免費停車優惠") == []
    assert extract_rules(None) == []


def test_min_spend_threshold_is_recorded() -> None:
    [rule] = extract_rules("當月累積消費達指定門檻（滿 NT$100）享 1% 點數回饋")
    assert rule["min_spend"] == 100
