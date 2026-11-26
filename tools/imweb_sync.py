"""아임웹 순금 제품 판매가를 prices.json(우리 시세표)에 맞춰 일괄 수정.

판매가 = 순금 '살 때'(VAT 포함, prices.json의 순금 sell) x 돈수 + 제품별 공임, ROUND_TO 원 단위 반올림

사용 (GitHub Actions에서 실행):
  python3 tools/imweb_sync.py auth <인가코드>     # 최초 1회: 토큰 발급 + 연동완료 처리
  python3 tools/imweb_sync.py sync [--dry-run]    # 가격 일괄 수정
  python3 tools/imweb_sync.py list                # 상품 번호/이름/현재가 목록 출력

필요한 환경변수 (GitHub Secrets):
  IMWEB_CLIENT_ID, IMWEB_CLIENT_SECRET, TOKEN_KEY(토큰 파일 암호화용 임의 문구)
  IMWEB_REDIRECT_URI (워크플로에 고정값으로 지정)

리프레시 토큰은 매번 새로 바뀌므로(유효기간 90일) .imweb/token.enc 에 암호화해 저장하고 커밋한다.
액세스 토큰 유효시간은 2시간이라 매 실행마다 리프레시 토큰으로 새로 받는다.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://openapi.imweb.me"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(ROOT, ".imweb", "token.enc")
PRODUCTS_FILE = os.path.join(ROOT, "products.json")
PRICES_FILE = os.path.join(ROOT, "prices.json")
ROUND_TO = 100


def env(name):
    v = os.environ.get(name, "").strip()
    if not v:
        sys.exit(f"환경변수 {name} 이(가) 없습니다. GitHub Secrets를 확인하세요.")
    return v


# ---------- 토큰 저장 (openssl AES-256 암호화) ----------
def save_refresh_token(token):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-a", "-out", TOKEN_FILE, "-pass", "env:TOKEN_KEY"],
        input=token.encode(), check=True,
    )


def load_refresh_token():
    if not os.path.exists(TOKEN_FILE):
        sys.exit("저장된 토큰이 없습니다. 먼저 'auth' 워크플로를 실행하세요.")
    out = subprocess.run(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-a", "-in", TOKEN_FILE, "-pass", "env:TOKEN_KEY"],
        capture_output=True, check=True,
    )
    return out.stdout.decode().strip()


# ---------- HTTP ----------
def http(method, path, token=None, form=None, body=None, retries=3):
    url = API + path
    headers = {"Accept": "application/json"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            if e.code == 429 and attempt < retries - 1:
                time.sleep(2 + attempt * 2)
                continue
            raise SystemExit(f"API 오류 {e.code} {method} {path}: {text[:500]}")
    raise SystemExit("API 재시도 실패")


def get_tokens(form):
    res = http("POST", "/oauth2/token", form=form)
    data = res.get("data") or {}
    if not data.get("accessToken") or not data.get("refreshToken"):
        raise SystemExit(f"토큰 응답 이상: {json.dumps(res, ensure_ascii=False)[:300]}")
    return data["accessToken"], data["refreshToken"]


def access_token():
    """리프레시 토큰으로 새 토큰을 받고, 바뀐 리프레시 토큰을 즉시 저장."""
    at, rt = get_tokens({
        "grantType": "refresh_token",
        "clientId": env("IMWEB_CLIENT_ID"),
        "clientSecret": env("IMWEB_CLIENT_SECRET"),
        "refreshToken": load_refresh_token(),
    })
    save_refresh_token(rt)
    return at


# ---------- 명령 ----------
def cmd_auth(code):
    at, rt = get_tokens({
        "grantType": "authorization_code",
        "clientId": env("IMWEB_CLIENT_ID"),
        "clientSecret": env("IMWEB_CLIENT_SECRET"),
        "redirectUri": env("IMWEB_REDIRECT_URI"),
        "code": code.strip(),
    })
    save_refresh_token(rt)
    print("토큰 발급 성공, 저장 완료")
    info = http("GET", "/site-info", token=at)
    print("사이트 정보:", json.dumps(info.get("data"), ensure_ascii=False)[:800])
    try:
        res = http("PATCH", "/site-info/integration-complete", token=at, body={})
        print("연동완료 처리:", json.dumps(res, ensure_ascii=False)[:300])
    except SystemExit as e:
        print("연동완료 처리 실패(테스트 연동이면 무시 가능):", e)


def unit_code(at):
    info = (http("GET", "/site-info", token=at).get("data") or {})
    for key in ("unitList", "units", "unitCodes"):
        lst = info.get(key)
        if lst:
            first = lst[0]
            return first.get("unitCode") if isinstance(first, dict) else first
    if info.get("unitCode"):
        return info["unitCode"]
    raise SystemExit(f"unitCode를 찾지 못했습니다: {json.dumps(info, ensure_ascii=False)[:500]}")


def cmd_list():
    at = access_token()
    uc = unit_code(at)
    page = 1
    while True:
        res = http("GET", f"/products?unitCode={uc}&page={page}&limit=100", token=at)
        data = res.get("data") or {}
        items = data.get("list") or data.get("items") or []
        for p in items:
            print(p.get("prodNo"), "|", p.get("name"), "|", p.get("price"), "| VAT포함:", p.get("isPriceTaxIncluded"))
        if len(items) < 100:
            break
        page += 1


def gold_sell_price():
    with open(PRICES_FILE, encoding="utf-8") as f:
        prices = json.load(f)
    for it in prices["items"]:
        if it["name"] == "순금" and it.get("sell"):
            return int(it["sell"]), prices.get("updated")
    raise SystemExit("prices.json에서 순금 살 때 가격을 찾지 못했습니다.")


def target_price(gold, item):
    raw = gold * float(item["don"]) + int(item["labor"])
    return int(round(raw / ROUND_TO) * ROUND_TO)


def cmd_sync(dry_run):
    gold, updated = gold_sell_price()
    with open(PRODUCTS_FILE, encoding="utf-8") as f:
        products = [p for p in json.load(f)["products"] if p.get("enabled", True)]
    print(f"기준 순금 살 때(VAT포함): {gold:,}원 ({updated}), 대상 {len(products)}개")
    if not products:
        return
    at = access_token()
    uc = unit_code(at)
    changed = 0
    for item in products:
        new = target_price(gold, item)
        cur = (http("GET", f"/products/{item['prodNo']}?unitCode={uc}", token=at).get("data") or {}).get("price")
        tag = "변경없음" if cur == new else ("미리보기" if dry_run else "수정")
        print(f"[{tag}] {item['prodNo']} {item.get('name','')}: {cur} → {new:,} (돈수 {item['don']}, 공임 {int(item['labor']):,})")
        if cur == new or dry_run:
            continue
        http("PATCH", f"/products/{item['prodNo']}/price", token=at, body={
            "unitCode": uc,
            "price": new,
            "isPriceNone": "N",
            "isPriceTaxIncluded": "Y",
        })
        changed += 1
        time.sleep(0.6)
    print(f"완료: {changed}개 수정")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "auth":
        cmd_auth(sys.argv[2])
    elif cmd == "list":
        cmd_list()
    elif cmd == "sync":
        cmd_sync("--dry-run" in sys.argv)
    else:
        sys.exit(__doc__)
