#!/usr/bin/env python3
"""Module quét daangn.com bằng Playwright.

- scrape_keyword(): tìm theo từ khóa (điện thoại...).
- scrape_free(): lấy đồ MIỄN PHÍ (나눔, giá 0원) và lọc đồ điện tử.
- analyze_condition(): phân tích tình trạng máy từ mô tả tiếng Hàn.
- detect_negotiable(): có thương lượng giá được không.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from urllib.parse import quote

SEARCH_PAGE = "https://www.daangn.com/kr/buy-sell/s/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
ITEM_ID_RE = re.compile(r"-([0-9a-z]{8,})/?$")
PHONE_MODEL_RE = re.compile(
    r"(아이폰|iphone)\s*(se\s*\d*|\d{1,2}\s*(pro|max|plus|mini|프로|맥스|플러스|미니)?)?"
    r"|(?:갤럭시|galaxy)\s*(s|a|m|j|z|노트|note|폴드|fold|플립|flip|폴더|그랜드|grand|와이드|wide|온)\s*[0-9a-z가-힣]*"
    r"|(?:샤오미|홍미|레드미|redmi|pixel|픽셀)\s*[0-9a-z가-힣]*",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Danh sách THIẾT BỊ được phép cho đồ MIỄN PHÍ — chỉ check TIÊU ĐỀ
# (chặt hơn, tránh false-positive khi content nhắc đến laptop trong tin bán giường)
# ---------------------------------------------------------------------------
FREE_DEVICE_WORDS = [
    # Điện thoại
    "아이폰", "iphone", "갤럭시", "galaxy", "스마트폰", "휴대폰", "핸드폰",
    "공기계", "자급제", "갤s", "갤z", "갤노트", "플립", "폴드",
    # Máy tính bảng
    "아이패드", "ipad", "갤럭시탭", "갤탭", "태블릿", "키즈탭",
    # Tai nghe
    "에어팟", "airpods", "이어폰", "버즈", "헤드폰", "헤드셋",
    # Đồng hồ thông minh
    "애플워치", "갤럭시워치", "스마트워치",
    # Máy ảnh
    "카메라", "캠코더", "디카", "dslr", "미러리스",
    # Máy tính / laptop
    "노트북", "맥북", "macbook", "컴퓨터", "피씨", "데스크탑", "아이맥", "맥미니",
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
    # Cáp / phụ kiện sạc
    "케이블", "충전선", "충전기", "무선충전", "무선 충전", "고속무선", "고속 무선",
    "충전독", "충전거치대", "트리오", "디지털기기",
    "충전 스테이션", "충전스테이션", "스테이션", "맥세이프",
    "c to c", "c-to-c", "5핀", "8핀", "충전기만",
    "빈박스", "공박", "박스", "박스만", "카드지갑", "카드 지갑",
    # Thiết bị không phải điện thoại
    "워치", "스마트워치", "버즈", "에어팟", "이어폰", "헤드폰", "헤드셋",
    "태블릿", "갤럭시탭", "갤탭", "아이패드", "ipad", "패드", "카플레이",
    "안드로이드오토", "픽셀블럭", "블럭",
    "노트북", "맥북", "컴퓨터", "데스크탑", "pc", "윈도우",
    "파워뱅크", "키보드", "크롬캐스트", "공기청정기", "마사지건",
    "체중계", "인바디", "오디오", "코딩", "살균기", "셀카봉", "삼각대",
    "컨트롤러", "vr", "스마트톡", "차량용", "번호판", "장식", "미스트",
    "필터", "갤럭시핏", "핏3", "핏 3",
    # Giả / trưng bày
    "목업", "모형폰", "더미", "테스트폰", "디스플레이폰",
    # Điều khiển từ xa / không phải thiết bị
    "리모컨", "리모트",
    # Tin người mua đang tìm/đổi máy, không phải tin bán máy.
    "구매해봅니다", "구매합니다", "구합니다", "구해요", "삽니다", "매입",
    "교환원", "교환 원", "교환합니다", "교환",
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
    """True nếu TIÊU ĐỀ cho thấy đây là một chiếc điện thoại.

    Không dùng mô tả để quyết định, vì phụ kiện thường ghi "dùng cho iPhone/Galaxy"
    trong phần mô tả và trước đây bị lọt vào bot.
    """
    t = (title or "").lower()
    if PHONE_MODEL_RE.search(t):
        return True
    strong_title_words = [
        "스마트폰", "휴대폰", "핸드폰", "폴더폰", "공기계", "자급제",
        "폰 팝니다", "폰팝니다", "중고폰",
    ]
    return any(w in t for w in strong_title_words)


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
    """Chỉ kiểm tra TIÊU ĐỀ với danh sách thiết bị chặt để tránh false-positive
    (ví dụ: tin bán giường nhắc laptop trong mô tả sẽ không lọt qua nữa)."""
    low = (title or "").lower()
    return any(w in low for w in FREE_DEVICE_WORDS)


def _parse_ts(ts_str) -> float | None:
    """Chuyển ISO 8601 hoặc unix timestamp (giây hoặc ms) thành float. Trả None nếu lỗi."""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        pass
    try:
        val = float(ts_str)
        # Nếu > 1e10 → milliseconds (unix ms), chia 1000 về giây
        if val > 1e10:
            val = val / 1000.0
        return val
    except (TypeError, ValueError):
        return None


def is_fresh(item: dict, max_age_hours: int = 24) -> bool:
    """True nếu tin đăng trong vòng max_age_hours giờ.
    Nếu không có timestamp thì cho qua (đừng chặn nhầm)."""
    ts = item.get("published_at")
    if ts is None:
        return True
    return (time.time() - ts) <= max_age_hours * 3600


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
        raw_id = a.get("id") or href
        m = ITEM_ID_RE.search(raw_id)
        if not m:
            continue
        ts_candidates = [
            _parse_ts(a.get("publishedAt")),
            _parse_ts(a.get("createdAt")),
            _parse_ts(a.get("writtenAt")),
            _parse_ts(a.get("boostedAt")),
        ]
        ts_candidates = [ts for ts in ts_candidates if ts is not None]
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
                # Daangn often re-surfaces boosted listings in the app.
                "published_at": max(ts_candidates) if ts_candidates else None,
            }
        )
    return results


def _fetch(page, url: str, max_scrolls: int = 8) -> list[dict]:
    """Điều hướng và bắt JSON từ API fleamarket/search.
    Cuộn xuống để kích hoạt infinite-scroll (mỗi lần ~20 item thêm).
    """
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
        # Cuộn để tải thêm kết quả (infinite-scroll)
        stagnant = 0
        for _ in range(max_scrolls):
            prev = len(captured)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
            if len(captured) == prev:
                stagnant += 1
                if stagnant >= 2:
                    break  # Không còn item mới nữa
            else:
                stagnant = 0
    finally:
        page.remove_listener("response", on_response)

    if not captured:
        print(f"  [scraper] 0 kết quả API tại: {url[:120]}", file=sys.stderr)
        return []
    # Gộp và dedup tất cả articles từ mọi trang
    all_articles: list = []
    seen_hrefs: set = set()
    for resp_data in captured:
        for a in (resp_data.get("fleamarketArticles", []) or []):
            uid = a.get("id") or a.get("href", "")
            if uid and uid not in seen_hrefs:
                seen_hrefs.add(uid)
                all_articles.append(a)
    print(f"  [scraper] {len(all_articles)} items ({len(captured)} trang) tại: {url[:80]}")
    return all_articles


def _append_price(url: str, min_price: int | None, max_price: int | None) -> str:
    if min_price is None and max_price is None:
        return url
    lo = int(min_price) if min_price else 0
    hi = int(max_price) if max_price else 0
    sep = "&" if "?" in url and not url.endswith("?") else ""
    return f"{url}{sep}price={lo}__{hi}"


def scrape_price_range(page, region_id: str | None, region_name: str | None,
                       min_price: int | None = None, max_price: int | None = None,
                       max_scrolls: int = 8) -> list[dict]:
    """Lấy kết quả theo khoảng giá, không kèm từ khóa.

    Lượt quét rộng này giống cách người dùng mở app rồi chỉ lọc giá, sau đó bot
    mới lọc tiếp điện thoại/phụ kiện ở tầng ứng dụng.
    """
    if region_id and region_name:
        url = f"{SEARCH_PAGE}?in={quote(region_name + '-' + region_id)}"
    else:
        url = SEARCH_PAGE + "?"
    url = _append_price(url, min_price, max_price)
    return _parse_articles(_fetch(page, url, max_scrolls=max_scrolls), region_name or "전국")


def scrape_keyword(page, region_id: str | None, region_name: str | None, keyword: str,
                   min_price: int | None = None, max_price: int | None = None,
                   max_scrolls: int = 8) -> list[dict]:
    if region_id and region_name:
        url = (
            f"{SEARCH_PAGE}?in={quote(region_name + '-' + region_id)}"
            f"&search={quote(keyword)}"
        )
    else:
        url = f"{SEARCH_PAGE}?search={quote(keyword)}"
    url = _append_price(url, min_price, max_price)
    return _parse_articles(_fetch(page, url, max_scrolls=max_scrolls), region_name or "전국")


def scrape_free(page, region_id: str | None, region_name: str | None, max_scrolls: int = 8) -> list[dict]:
    """Lấy đồ MIỄN PHÍ (price=0__0) rồi lọc đồ điện tử."""
    if region_id and region_name:
        url = (
            f"{SEARCH_PAGE}?in={quote(region_name + '-' + region_id)}"
            f"&price=0__0"
        )
    else:
        url = f"{SEARCH_PAGE}?price=0__0"
    items = _parse_articles(_fetch(page, url, max_scrolls=max_scrolls), region_name or "전국")
    return [it for it in items if is_electronics(it["title"], it["content"])]
