#!/usr/bin/env python3
"""Dịch & đánh giá tin daangn sang tiếng Việt bằng Groq AI."""
from __future__ import annotations

import json
import os
import sys
from typing import Iterable

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "Bạn là chuyên gia thẩm định đồ cũ trên chợ Hàn Quốc 당근마켓, nói tiếng Việt có dấu. "
    "Bạn rất nghiêm khắc: chỉ chấp nhận điện thoại ĐANG DÙNG TỐT, đúng tầm giá. "
    "Luôn trả về JSON hợp lệ, không thêm chữ nào ngoài JSON. "
    "Trong mọi trường văn bản, KHÔNG để lại chữ Hàn."
)


def _user_prompt(item: dict, cond: dict, is_free: bool) -> str:
    battery = f"{cond['battery']}%" if cond.get("battery") is not None else "không rõ"
    gia = "MIỄN PHÍ (đồ cho tặng)" if is_free else f"{item['price']:,} won"
    loai = "đồ điện tử miễn phí" if is_free else "điện thoại"
    return (
        f"Phân tích tin rao {loai} sau (tiếng Hàn). Trả JSON với các khóa:\n"
        '- "ten_goc": chép lại tên gốc tiếng Hàn ngắn gọn, chuẩn hóa khoảng trắng.\n'
        '- "ten": tên món đồ dịch sang tiếng Việt ngắn gọn (kèm đời máy/dung lượng nếu có).\n'
        '- "tomtat": 1 câu tóm tắt nhanh, dễ đọc, tiếng Việt.\n'
        '- "danhgia": 4-6 câu tiếng Việt, cụ thể: màn hình, pin, vỏ, lỗi tiềm ẩn, '
        'mức độ hợp lý so với giá, và khuyến nghị mua/không mua.\n'
        '- "vung": dịch vùng/khu vực sang tiếng Việt, không để lại tiếng Hàn.\n'
        '- "nguoi_ban": dịch/tên hóa người bán sang tiếng Việt ngắn gọn (nếu không rõ thì để rỗng).\n'
        '- "la_dien_thoai": true nếu đây ĐÚNG là một chiếc điện thoại nguyên chiếc dùng được, '
        'false nếu là VỎ/ỐP/CÁP/KÍNH/PHỤ KIỆN hoặc máy hỏng/chỉ bán linh kiện.\n'
        '- "con_tot": true nếu máy còn hoạt động tốt (không chập nguồn, không ố/sọc màn, không bể nát), '
        'false nếu có hư hỏng đáng kể.\n'
        '- "bo_qua": true nếu nên BỎ QUA tin này (không phải điện thoại tốt), ngược lại false.\n\n'
        f"Tiêu đề: {item['title']}\n"
        f"Khu vực: {item.get('region', '')}\n"
        f"Người bán: {item.get('seller', '')}\n"
        f"Giá: {gia}\n"
        f"Pin (ước tính): {battery}\n"
        f"Mô tả: {item['content'][:900]}\n"
    )


def _normalize_keys(key: str | Iterable[str] | None) -> list[str]:
    if not key:
        return []
    if isinstance(key, str):
        return [key.strip()] if key.strip() else []
    out = []
    for k in key:
        ks = str(k).strip()
        if ks:
            out.append(ks)
    return out


def describe_vi(item: dict, cond: dict, key: str | Iterable[str], model: str = DEFAULT_MODEL,
                is_free: bool = False) -> dict | None:
    """Trả về dict thẩm định tiếng Việt đầy đủ hoặc None nếu lỗi."""
    keys = _normalize_keys(key)
    if not keys:
        return None
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(item, cond, is_free)},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }
    last_error = None
    for api_key in keys:
        try:
            resp = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=30,
            )
            if resp.status_code == 429:
                last_error = f"429 rate limit với key {api_key[:8]}..."
                continue
            if resp.status_code != 200:
                last_error = f"{resp.status_code}: {resp.text[:160]}"
                continue
            text = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(text)
            la_dt = data.get("la_dien_thoai")
            con_tot = data.get("con_tot")
            bo_qua = data.get("bo_qua")
            if bo_qua is None and not is_free:
                bo_qua = (la_dt is False) or (con_tot is False)
            return {
                "ten_goc": (data.get("ten_goc") or item.get("title") or "").strip(),
                "ten": (data.get("ten") or "").strip(),
                "tomtat": (data.get("tomtat") or "").strip(),
                "danhgia": (data.get("danhgia") or "").strip(),
                "vung": (data.get("vung") or "").strip(),
                "nguoi_ban": (data.get("nguoi_ban") or "").strip(),
                "la_dien_thoai": la_dt,
                "con_tot": con_tot,
                "bo_qua": bool(bo_qua) if not is_free else False,
            }
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            continue
    if last_error:
        print(f"  [Groq lỗi] {last_error}", file=sys.stderr)
    return None


if __name__ == "__main__":
    k = os.environ.get("GROQ_API_KEY", "")
    sample = {
        "title": "아이폰 15 프로 256GB 자급제 S급 무잔상 배터리 100%",
        "price": 750000,
        "content": "액정 잔상 없음, 기스 없음, 풀박스. 네고 가능합니다.",
    }
    print(describe_vi(sample, {"battery": 100}, k))
