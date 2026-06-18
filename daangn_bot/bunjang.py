#!/usr/bin/env python3
"""Nguồn dữ liệu 번개장터 (Bunjang).

Daangn/Karrot và Bunjang là hai chợ khác nhau. Các tin có nhãn "바로구매"
trong app Bunjang không xuất hiện trong API web Daangn, nên cần quét riêng.
"""
from __future__ import annotations

from datetime import datetime

import requests

SEARCH_URL = "https://api.bunjang.co.kr/api/search/v8/pw/product/specs/keyword"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    ),
    "Accept": "application/json",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://m.bunjang.co.kr/",
}

BUNJANG_BLOCK_WORDS = [
    "매입", "삽니다", "구매합니다", "구해요", "구합니다", "삽니다",
    "대리점", "기기변경", "번호이동", "요금제", "렌탈", "개통",
    "재고정리", "최저가", "번장1등", "후기", "원격설치",
    "대여", "추가결제", "문의", "뒷판", "폰없음", "구성품",
    "키링", "가챠", "프로젝터", "갤럭시북", "카라티", "패키지",
    "메이드모먼", "케이드", "팬츠", "닌텐도", "스위치", "마리오",
    "캔뱃지", "뱃지", "콜라보",
]


def _parse_ts(value) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _extract_products(payload: dict) -> list[dict]:
    blocks = (((payload.get("data") or {}).get("searchSpec") or {}).get("uiBlockList") or [])
    for block in blocks:
        response = block.get("searchResponse")
        if response and isinstance(response.get("data"), list):
            return response["data"]
    return []


def is_noise(title: str) -> bool:
    low = (title or "").lower()
    return any(w.lower() in low for w in BUNJANG_BLOCK_WORDS)


def search_keyword(keyword: str, min_price: int | None, max_price: int | None,
                   limit: int = 60) -> list[dict]:
    params = {"q": keyword}
    if min_price is not None:
        params["minPrice"] = str(int(min_price))
    if max_price is not None and max_price > 0:
        params["maxPrice"] = str(int(max_price))

    resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    products = _extract_products(resp.json())

    out: list[dict] = []
    for p in products:
        pid = p.get("pid")
        if not pid or p.get("type") != "PRODUCT":
            continue
        title = (p.get("name") or "").strip()
        if not title:
            continue
        if p.get("status") and p.get("status") != "SELLING":
            continue
        # Search ads are usually shops buying phones or bait listings.
        if p.get("ad") is True:
            continue
        try:
            price = int(float(p.get("price")))
        except (TypeError, ValueError):
            price = None
        out.append({
            "id": f"bj:{pid}",
            "title": title,
            "price": price,
            "status": "Ongoing",
            "content": title,
            "region": "번개장터",
            "seller": str(((p.get("shop") or {}).get("uid") or "")),
            "link": f"https://m.bunjang.co.kr/products/{pid}",
            "published_at": _parse_ts(p.get("updatedAt")),
            "source": "번개장터",
            "thumbnail": (p.get("productImage") or "").replace("{res}", "480"),
        })
        if len(out) >= limit:
            break
    return out
