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
    "Bạn là trợ lý mua đồ cũ trên chợ Hàn Quốc 당근마켓, nói tiếng Việt có dấu. "
    "Luôn trả về JSON hợp lệ, không thêm chữ nào ngoài JSON."
)


def _user_prompt(item: dict, cond: dict, is_free: bool) -> str:
    battery = f"{cond['battery']}%" if cond.get("battery") is not None else "không rõ"
    gia = "MIỄN PHÍ (đồ cho tặng)" if is_free else f"{item['price']:,} won"
    return (
        "Dưới đây là một tin rao (tiếng Hàn). Hãy trả về JSON với 2 khóa:\n"
        '- "ten": tên món đồ dịch sang tiếng Việt ngắn gọn (kèm dung lượng/đời máy nếu có).\n'
        '- "danhgia": 1-2 câu tiếng Việt đánh giá tình trạng & độ hời. '
        "Nếu mô tả cho thấy đồ lỗi/hỏng thì cảnh báo rõ.\n\n"
        f"Tiêu đề: {item['title']}\n"
        f"Giá: {gia}\n"
        f"Pin (ước tính): {battery}\n"
        f"Mô tả: {item['content'][:700]}\n"
    )


def describe_vi(item: dict, cond: dict, key: str, model: str = DEFAULT_MODEL,
                is_free: bool = False) -> dict | None:
    """Trả về {'ten': str, 'danhgia': str} hoặc None nếu lỗi."""
    if not key:
        return None
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(item, cond, is_free)},
        ],
        "temperature": 0.2,
        "max_tokens": 300,
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
        return {
            "ten": (data.get("ten") or "").strip(),
            "danhgia": (data.get("danhgia") or "").strip(),
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
