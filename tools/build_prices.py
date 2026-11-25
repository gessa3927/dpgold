"""한국금거래소 메인 시세 → 한국대표금거래소 시세(prices.json) 변환.

입력: 브라우저에서 추출한 JSON 파일 (ts, out{gold24,gold18,gold14,platinum,silver: {cells:[살때, 팔때]}})
사용: python3 tools/build_prices.py raw.json prices.json
"""
import json
import re
import sys

# 규칙: (살 때 조정액, 팔 때 조정액)  — 살 때 = 고객이 살 때(우리 판매), 팔 때 = 고객이 팔 때(우리 매입)
RULES = {
    "gold24":   (-3000, +3000),
    "gold18":   (None,  +1500),   # 살 때는 '제품시세적용'
    "gold14":   (None,  +1500),
    "platinum": (-900,  +800),
    "silver":   (-120,  +120),
}
LABELS = {
    "gold24": ("순금", "24K"),
    "gold18": ("18K", ""),
    "gold14": ("14K", ""),
    "platinum": ("백금", ""),
    "silver": ("은", ""),
}


def parse_cell(text):
    """'668,000 -3.29%down -22,000' → (668000, -22000). 숫자 없으면 (None, None, 원문)."""
    nums = re.findall(r"[+-]?\d[\d,]*(?:\.\d+)?", text or "")
    nums = [n for n in nums if "." not in n]  # 퍼센트(소수) 제외
    if not nums:
        return None, None, (text or "").strip()
    price = int(nums[0].replace(",", "").lstrip("+"))
    chg = None
    if len(nums) > 1:
        chg = abs(int(nums[-1].replace(",", "").lstrip("+-")))
        if "down" in text or "하락" in text:
            chg = -chg
        elif not ("up" in text or "상승" in text):
            chg = int(nums[-1].replace(",", ""))
    return price, chg, None


def check_cell(text):
    """카운트업 애니메이션 중간값 방지: 표시된 등락률과 (등락액/가격)이 일치해야 함."""
    m = re.match(r"\s*([\d,]+)\s*([+-]?[\d.]+)%\s*(up|down)?\s*([+-]?[\d,]+)", text or "")
    if not m:
        return  # '제품시세적용' 등 숫자 없는 칸
    price = int(m.group(1).replace(",", ""))
    pct = abs(float(m.group(2)))
    chg = abs(int(m.group(4).replace(",", "").lstrip("+-")))
    if price <= 0:
        raise ValueError(f"가격 0: {text}")
    calc = chg / price * 100
    if abs(calc - pct) > 0.06:
        raise ValueError(f"등락률 불일치(애니메이션 중간값 의심): {text} → 계산 {calc:.2f}% vs 표시 {pct}%")


def build(raw):
    for key in ["gold24", "gold18", "gold14", "platinum", "silver"]:
        for c in raw["out"][key]["cells"]:
            check_cell(c)
    g = raw["out"]["gold24"]["cells"]
    if parse_cell(g[0])[0] <= parse_cell(g[1])[0]:
        raise ValueError("순금 살 때 <= 팔 때: 비정상")
    items = []
    for key in ["gold24", "gold18", "gold14", "platinum", "silver"]:
        cells = raw["out"][key]["cells"]
        sell_adj, buy_adj = RULES[key]
        s_price, s_chg, s_text = parse_cell(cells[0])
        b_price, b_chg, b_text = parse_cell(cells[1])
        name, sub = LABELS[key]
        item = {"name": name, "sub": sub}
        if sell_adj is None or s_price is None:
            item.update(sell=None, sellText=s_text or "제품시세적용")
        else:
            item.update(sell=s_price + sell_adj, sellChg=s_chg)
        if buy_adj is None or b_price is None:
            item.update(buy=None, buyText=b_text or "-")
        else:
            item.update(buy=b_price + buy_adj, buyChg=b_chg)
        items.append(item)
    return {"updated": raw["ts"][:16], "items": items}


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as f:
        raw = json.load(f)
    data = build(raw)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps(data, ensure_ascii=False, indent=2))
