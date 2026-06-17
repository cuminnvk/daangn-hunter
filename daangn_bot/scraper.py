#!/usr/bin/env python3
"""Module quét daangn.com bằng Playwright.

- scrape_keyword(): tìm theo từ khóa (điện thoại...).
- scrape_free(): lấy đồ MIỄN PHÍ (나눔, giá 0원) và lọc đồ điện tử.
- analyze_condition(): phân tích tình trạng máy từ mô tả tiếng Hàn.
- detect_negotiable(): có thương lượng giá được không.
"""
from __future__ import annotations

import re
from urllib.parse import quote

SEARCH_PAGE = "https://www.daangn.com/kr/buy-sell/s/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
ITEM_ID_RE = re.compile(r"-([0-9a-z]{8,})/?$")

# ---------------------------------------------------------------------------
# Từ khóa nhận diện ĐỒ ĐIỆN TỬ (để lọc trong danh sách đồ miễn phí)
# ---------------------------------------------------------------------------
ELECTRONICS_WORDS = [
    "디지털", "전자", "아이폰", "갤럭시", "갤럭", "아이패드", "패드", "탭", "태블릿",
    "노트북", "맥북", "컴퓨터", "피씨", "데스크탑", "본체", "모니터", "키보드", "마우스",
    "충전", "케이블", "이어폰", "에어팟", "버즈", "헤드폰", "헤드셋", "스피커", "사운드바",
    "공유기", "라우터", "모뎀", "tv", "티비", "텔레비전", "닌텐도", "스위치", "플스",
    "플레이스테이션", "엑박", "xbox", "콘솔", "게임기", "카메라", "캠코더", "웹캠",
    "usb", "hdmi", "외장하드", "ssd", "hdd", "하드", "메모리", "램", "그래픽", "그래픽카드",
    "메인보드", "파워서플", "프린터", "스캐너", "셋톱", "블루투스", "워치", "스마트워치",
    "갤럭시워치", "애플워치", "드론", "무선", "프로젝터", "빔프로젝터", "안마", "면도기",
    "드라이기", "고데기", "선풍기", "청소기", "로봇청소기", "밥솥", "전기밥솥", "전자레인지",
    "에어프라", "냉장고", "세탁기", "건조기", "에어컨", "제습기", "가습기", "정수기",
    "공기청정기", "전기포트", "커피머신", "믹서", "토스터", "인덕션", "히터", "전기장판",
    "보조배터리", "충전기", "젠더", "리시버", "앰프", "턴테이블", "마이크", "조명", "led",
    "키즈탭", "갤럭시탭", "버즈", "갤탭", "노트10", "노트20", "s펜", "맥미니", "아이맥",
]

# ---------------------------------------------------------------------------
# PHỤ KIỆN điện thoại (KHÔNG phải máy) — loại bỏ khi săn điện thoại.
# Chỉ kiểm tra trong TIÊU ĐỀ để tránh loại nhầm tin "tặng kèm sạc".
# ---------------------------------------------------------------------------
ACCESSORY_WORDS = [
    "케이스", "커버", "범퍼", "그립톡", "스트랩", "파우치", "젤리", "젤리케이스",
    "보호필름", "강화유리", "액정필름", "유리필름", "필름", "보호",
    "거치대", "홀더", "스탠드", "마운트", "젠더", "어댑터", "아답터",
    "유심", "심카드", "메모리카드", "sd카드",
    "보조배터리", "배터리팩", "데코", "스티커", "악세사리", "악세서리",
    "부품용", "부속품", "공박스", "박스만", "이어팁", "정품박스",
]
# Từ chỉ ĐÚNG là điện thoại (máy thật).
PHONE_WORDS = [
    "아이폰", "iphone", "갤럭시", "galaxy", "스마트폰", "휴대폰", "핸드폰",
    "갤s", "갤노트", "갤z", "노트", "플립", "폴드", "아이폰se", "se2", "se3",
    "픽셀", "pixel", "샤오미", "홍미", "공기계", "자급제", "갤럭",
]

# Nhóm từ khóa cho tin KHÔNG phải điện thoại (quần áo, giày, túi...).
NON_PHONE_WORDS = [
    "의류", "옷", "티셔츠", "반팔", "긴팔", "맨투맨", "후드", "니트", "가디건",
    "자켓", "재킷", "코트", "패딩", "원피스", "치마", "스커트", "바지", "청바지",
    "트레이닝", "잠옷", "신발", "운동화", "구두", "슬리퍼", "가방", "백팩",
    "지갑", "모자", "목도리", "장갑", "귀걸이", "목걸이", "팔찌", "반지",
    "향수", "화장품", "립스틱", "스킨", "로션",
]


def is_accessory(title: str, content: str = "") -> bool:
    """True nếu tin rao là PHỤ KIỆN (vỏ, ốp, cáp, sạc, kính cường lực...)."""
    t = (title or "").lower()
    return any(w in t for w in ACCESSORY_WORDS)


def looks_like_phone(title: str, content: str = "") -> bool:
    """True nếu tin rao đúng là một chiếc điện thoại."""
    low = (title + " " + content).lower()
    return any(w in low for w in PHONE_WORDS)


def clearly_not_phone(title: str, content: str = "") -> bool:
    """True nếu nội dung nghiêng rõ ràng về mặt hàng thời trang/mỹ phẩm."""
    low = (title + " " + content).lower()
    return any(w in low for w in NON_PHONE_WORDS)

# ---------------------------------------------------------------------------
# Phân tích tình trạng máy
# ---------------------------------------------------------------------------
BROKEN_WORDS = [
    "고장", "파손", "깨짐", "깨진", "액정깨", "액정 깨", "유리깨",
    "침수", "먹통", "부품용", "부품 용", "수리용", "수리요", "불량",
    "안켜", "안 켜", "켜지지", "안나와", "미작동", "작동안", "작동 안",
    "광탈", "배터리광탈", "as-is", "에이에스", "기능이상", "터치불량",
    # chập nguồn / lỗi sạc / quá nóng / màn ố
    "충전불량", "충전안", "충전 안", "전원불량", "전원안", "전원 안",
    "발열", "과열", "백화", "액정나감", "액정 나감", "터치안", "터치 안",
    "명품", "액정줄", "세로줄심", "가로줄", "리퍼비시", "기교환",
]
SOFT_FLAGS = [
    "잔상", "번인", "하자", "줄가", "세로줄", "멍",
    "얼룩", "변색", "기스많", "스크래치", "흔집많", "찍힘",
]
GOOD_WORDS = [
    "s급", "에스급", "a급", "에이급", "최상급", "상태좋", "상태 좋",
    "깨끗", "무잔상", "잔상없", "기스없", "흠집없", "하자없", "이상없",
    "문제없", "새상품", "새제품", "미개봉", "미사용", "풀박스", "풀셋",
    "정상작동", "정상 작동", "s급상태",
]
NEGO_YES = [
    "네고가능", "네고 가능", "가격제안", "가격 제안", "제안주세요",
    "에누리", "흥정", "네고환영", "약간네고", "조정가능", "절충",
    "네고됩니다", "깎아", "네고ㅇ",
]
NEGO_NO = [
    "네고사절", "네고 사절", "네고x", "네고 x", "네고불가", "노네고",
    "정찰", "가격제안사절", "에누리없", "네고없", "가격다운x", "직거래만",
]
BATTERY_RES = [
    re.compile(r"(?:배터리|베터리|밧데리|배뎌리|효율|성능|battery)\D{0,8}(\d{2,3})\s*%"),
    re.compile(r"(\d{2,3})\s*%\D{0,6}(?:배터리|베터리|효율|성능)"),
]


def is_electronics(title: str, content: str = "") -> bool:
    low = (title + " " + content).lower()
    return any(w in low for w in ELECTRONICS_WORDS)


def analyze_condition(text: str) -> dict:
    low = text.lower()
    battery = None
    for rx in BATTERY_RES:
        m = rx.search(low)
        if m:
            val = int(m.group(1))
            if 1 <= val <= 100:
                battery = val
                break

    good_hits = [w for w in GOOD_WORDS if w in low]
    broken_hits = [w for w in BROKEN_WORDS if w in low]

    soft_bad = []
    for w in SOFT_FLAGS:
        idx = low.find(w)
        if idx == -1:
            continue
        before = low[max(0, idx - 1):idx]
        tail = low[idx + len(w): idx + len(w) + 10]
        if before in ("무", "노") or "없" in tail or "안" in tail or "x" in tail:
            good_hits.append(f"{w}없음")
        else:
            soft_bad.append(w)

    broken = bool(broken_hits)
    signals = []
    if good_hits:
        signals += good_hits[:3]
    if soft_bad:
        signals += [f"⚠{w}" for w in soft_bad[:2]]

    if broken:
        label = "⚠️ Nghi hỏng/lỗi"
    elif good_hits or (battery is not None and battery >= 90):
        label = "✅ Tốt"
    elif battery is not None and battery < 80:
        label = "🟡 Pin yếu"
    elif soft_bad:
        label = "🟡 Có lưu ý nhỏ"
    else:
        label = "🟡 Bình thường"

    return {
        "battery": battery,
        "label": label,
        "broken": broken,
        "signals": signals,
        "broken_hits": broken_hits,
        "soft_bad": soft_bad,
    }


def detect_negotiable(text: str) -> str:
    low = text.lower()
    if any(w in low for w in NEGO_NO):
        return "Mua ngay, không thương lượng"
    if any(w in low for w in NEGO_YES):
        return "Có thể thương lượng"
    return "Mua ngay, chưa thấy dấu hiệu thương lượng"


# ---------------------------------------------------------------------------
# Tiện ích
# ---------------------------------------------------------------------------

def to_int_price(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def region_label(region: dict) -> str:
    if not isinstance(region, dict):
        return ""
    parts = [region.get("name1"), region.get("name2"), region.get("name3")]
    parts = [p for p in parts if p]
    return " ".join(parts) if parts else (region.get("name") or "")


def _parse_articles(articles: list, fallback_region: str) -> list[dict]:
    results = []
    for a in articles or []:
        href = a.get("href") or ""
        m = ITEM_ID_RE.search(a.get("id") or href)
        if not m:
            continue
        results.append(
            {
                "id": m.group(1),
                "title": (a.get("title") or "").strip(),
                "price": to_int_price(a.get("price")),
                "status": a.get("status", ""),
                "content": (a.get("content") or "").strip(),
                "region": region_label(a.get("region") or {}) or fallback_region,
                "seller": ((a.get("user") or {}).get("nickname") or "").strip(),
                "link": href if href.startswith("http") else f"https://www.daangn.com{href}",
            }
        )
    return results


def _fetch(page, url: str) -> list[dict]:
    """Điều hướng và bắt JSON từ API fleamarket/search."""
    captured: list[dict] = []

    def on_response(resp):
        if "/api/v1/fleamarket/search" in resp.url:
            try:
                captured.append(resp.json())
            except Exception:  # noqa: BLE001
                pass

    page.on("response", on_response)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        for _ in range(25):
            if captured:
                break
            page.wait_for_timeout(1000)
    finally:
        page.remove_listener("response", on_response)

    if not captured:
        return []
    return captured[-1].get("fleamarketArticles", []) or []


def scrape_keyword(page, region_id: str, region_name: str, keyword: str,
                   min_price: int | None = None, max_price: int | None = None) -> list[dict]:
    url = (
        f"{SEARCH_PAGE}?in={quote(region_name + '-' + region_id)}"
        f"&search={quote(keyword)}"
    )
    if min_price is not None or max_price is not None:
        lo = int(min_price) if min_price else 0
        hi = int(max_price) if max_price else 0
        url += f"&price={lo}__{hi}"
    return _parse_articles(_fetch(page, url), region_name)


def scrape_free(page, region_id: str, region_name: str) -> list[dict]:
    """Lấy đồ MIỄN PHÍ (price=0__0) rồi lọc đồ điện tử."""
    url = (
        f"{SEARCH_PAGE}?in={quote(region_name + '-' + region_id)}"
        f"&price=0__0"
    )
    items = _parse_articles(_fetch(page, url), region_name)
    return [it for it in items if is_electronics(it["title"], it["content"])]
