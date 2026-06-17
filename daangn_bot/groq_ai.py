#!/usr/bin/env python3
"""Dịch & đánh giá tin daangn sang tiếng Việt bằng Groq AI."""
from __future__ import annotations

import json
import os
import sys

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "Bạn là chuyên gia thẩm định đồ cũ trên chợ Hàn Quốc 당근마켓, nói tiếng Việt có dấu. "
    "Bạn rất nghiêm khắc: chỉ chấp nhận điện thoại ĐANG DÙNG TỐT, đúng tầm giá. "
    "Luôn trả về JSON hợp lệ, không thêm chữ nào ngoài JSON."
)


def _user_prompt(item: dict, cond: dict, is_free: bool) -> str:
    battery = f"{cond['battery']}%" if cond.get("battery") is not None else "không rõ"
    gia = "MIỄN PHÍ (đồ cho tặng)" if is_free else f"{item['price']:,} won"
    loai = "đồ điện tử miễn phí" if is_free else "điện thoại"
    return (
        f"Phân tích tin rao {loai} sau (tiếng Hàn). Trả JSON với các khóa:\n"
        '- "ten": tên món đồ dịch sang tiếng Việt ngắn gọn (kèm đời máy/dung lượng nếu có).\n'
        '- "danhgia": 2-3 câu tiếng Việt: tình trạng máy (màn, pin, vỏ), có sửa/thay gì không, '
        'mức độ hời so với giá. Nếu có dấu hiệu lỗi/hỏng/màn ố/chập nguồn thì CẢNH BÁO rõ.\n'
        '- "la_dien_thoai": true nếu đây ĐÚNG là một chiếc điện thoại nguyên chiếc dùng được, '
        'false nếu là VỎ/ỐP/CÁP/KÍNH/PHỤ KIỆN hoặc máy hỏng/chỉ bán linh kiện.\n'
        '- "con_tot": true nếu máy còn hoạt động tốt (không chập nguồn, không ố/sọc màn, không bể nát), '
        'false nếu có hư hỏng đáng kể.\n'
        '- "bo_qua": true nếu nên BỎ QUA tin này (không phải điện thoại tốt), ngược lại false.\n\n'
        f"Tiêu đề: {item['title']}\n"
        f"Giá: {gia}\n"
        f"Pin (ước tính): {battery}\n"
        f"Mô tả: {item['content'][:900]}\n"
    )


def describe_vi(item: dict, cond: dict, key: str, model: str = DEFAULT_MODEL,
                is_free: bool = False) -> dict | None:
    """Trả về {'ten', 'danhgia', 'la_dien_thoai', 'con_tot', 'bo_qua'} hoặc None nếu lỗi."""
    if not key:
        return None
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(item, cond, is_free)},
        ],
        "temperature": 0.1,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  [Groq lỗi] {resp.status_code}: {resp.text[:160]}", file=sys.stderr)
            return None
        text = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(text)
        la_dt = data.get("la_dien_thoai")
        con_tot = data.get("con_tot")
        bo_qua = data.get("bo_qua")
        # Suy ra bo_qua nếu model không trả: chỉ áp dụng cho điện thoại (không free).
        if bo_qua is None and not is_free:
            bo_qua = (la_dt is False) or (con_tot is False)
        return {
            "ten": (data.get("ten") or "").strip(),
            "danhgia": (data.get("danhgia") or "").strip(),
            "la_dien_thoai": la_dt,
            "con_tot": con_tot,
            "bo_qua": bool(bo_qua) if not is_free else False,
        }
    except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as exc:
        print(f"  [Groq lỗi] {exc}", file=sys.stderr)
        return None


if __name__ == "__main__":
    k = os.environ.get("GROQ_API_KEY", "")
    sample = {
        "title": "아이폰 15 프로 256GB 자급제 S급 무잔상 배터리 100%",
        "price": 750000,
        "content": "액정 잔상 없음, 기스 없음, 풀박스. 네고 가능합니다.",
    }
    print(describe_vi(sample, {"battery": 100}, k))
