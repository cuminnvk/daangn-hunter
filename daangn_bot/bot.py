#!/usr/bin/env python3
"""Daangn Phone Hunter — bot Telegram luôn-bật, có menu thiết lập.

Tính năng:
- Quét điện thoại theo từ khóa + khoảng giá, và đồ ĐIỆN TỬ MIỄN PHÍ (나눔).
- Dịch & đánh giá sang TIẾNG VIỆT bằng Groq AI.
- Menu tương tác trên Telegram: đặt giá, thêm/bớt máy, bật/tắt đồ miễn phí,
  đổi tần suất, quét ngay...
- Tự quét định kỳ trong nền + chống trùng.

Chạy: python bot.py   (cần TELEGRAM_BOT_TOKEN, GROQ_API_KEY trong .env)
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

import scraper
import groq_ai

# In tiếng Hàn/Việt trên console Windows.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "seen.json"
SUBS_PATH = BASE_DIR / "subscribers.json"

# ---------------------------------------------------------------------------
# Nạp .env (đơn giản, không cần thư viện)
# ---------------------------------------------------------------------------

def load_env() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_env()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip()
OWNER_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"

# ---------------------------------------------------------------------------
# Khóa & trạng thái dùng chung
# ---------------------------------------------------------------------------
cfg_lock = threading.Lock()
scan_lock = threading.Lock()
scan_event = threading.Event()      # kích hoạt quét ngay
stop_event = threading.Event()
cancel_scan = threading.Event()     # yêu cầu DỪNG lượt quét đang chạy
pending: dict[int, dict] = {}        # trạng thái nhập liệu theo chat
last_scan_info = {"time": None, "found": 0}

DEFAULT_CONFIG = {
    "regions": [
        {"id": 6035, "name": "역삼동"},
        {"id": 355, "name": "신림동"},
        {"id": 6052, "name": "마곡동"},
        {"id": 6543, "name": "송도동"},
        {"id": 1766, "name": "봉담읍"},
        {"id": 1604, "name": "별내동"},
        {"id": 4245, "name": "배곧동"},
        {"id": 2292, "name": "불당동"},
        {"id": 3662, "name": "물금읍"},
        {"id": 2899, "name": "고흥읍"},
    ],
    "watch": [
        {"keyword": "아이폰 15", "min_price": 200000, "max_price": 750000},
        {"keyword": "아이폰 14", "min_price": 150000, "max_price": 550000},
        {"keyword": "갤럭시 S24", "min_price": 200000, "max_price": 650000},
    ],
    # Săn MOI loại máy trong khoảng giá này (không cần thêm từng máy).
    "phone_min_price": 20000,
    "phone_max_price": 60000,
    "phone_keywords": ["아이폰", "갤럭시", "휴대폰", "스마트폰"],
    "strict_good": True,        # chỉ máy tốt: loại chập nguồn/ố màn/bể nát
    "min_battery_percent": 80,  # pin tối thiểu nếu bật strict_good
    "phones_only": True,        # chỉ điện thoại thật, loại vỏ/ốp/phụ kiện
    "free_limit": 20,           # số tin đồ free tối đa mỗi lượt quét
    "phone_limit": 20,          # số tin điện thoại tối đa mỗi lượt quét
    "send_delay_seconds": 10,   # giãn cách gửi từng tin để tránh lỗi
    "digest_mode": False,       # gộp nhiều tin thành vài bản tin lớn
    "quiet_hours_enabled": False,
    "quiet_start_hour": 23,
    "quiet_end_hour": 7,
    "free_electronics": True,
    "free_first": True,         # ưu tiên quét đồ free trước
    "scan_interval_minutes": 30,
    "headless": True,
    "skip_sold": True,
    "skip_reserved": True,
    "skip_broken": True,
    "use_ai": True,
    "ai_model": groq_ai.DEFAULT_MODEL,
    "ai_max_calls": 30,
    "exclude_words": ["부품", "수리용", "잠금", "아이클라우드"],
}

# Preset máy phổ biến cho menu "Thêm máy".
PRESETS = [
    ("iPhone 16", "아이폰 16"), ("iPhone 15", "아이폰 15"), ("iPhone 14", "아이폰 14"),
    ("iPhone 13", "아이폰 13"), ("iPhone 12", "아이폰 12"), ("iPhone SE", "아이폰 SE"),
    ("Galaxy S24", "갤럭시 S24"), ("Galaxy S23", "갤럭시 S23"), ("Galaxy Z Flip", "갤럭시 Z 플립"),
    ("Galaxy Z Fold", "갤럭시 Z 폴드"), ("Galaxy Note", "갤럭시 노트"),
    ("iPad", "아이패드"), ("Galaxy Tab", "갤럭시탭"), ("MacBook", "맥북"),
    ("AirPods", "에어팟"), ("Apple Watch", "애플워치"),
]
INTERVALS = [10, 15, 30, 60, 120]
# Khoảng giá gợi ý nhanh (won): từ, đến
PRICE_PRESETS = [(0, 30000), (20000, 60000), (50000, 100000),
                 (100000, 200000), (0, 300000)]


# ---------------------------------------------------------------------------
# Đọc/ghi file
# ---------------------------------------------------------------------------

def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
    return default


def load_config() -> dict:
    cfg = load_json(CONFIG_PATH, None)
    if not isinstance(cfg, dict):
        cfg = dict(DEFAULT_CONFIG)
        save_config(cfg)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def load_seen() -> set[str]:
    return set(load_json(STATE_PATH, []))


def save_seen(seen: set[str]) -> None:
    STATE_PATH.write_text(json.dumps(list(seen)[-8000:], ensure_ascii=False), encoding="utf-8")


def load_subs() -> list[int]:
    subs = load_json(SUBS_PATH, [])
    return subs if isinstance(subs, list) else []


def save_subs(subs: list[int]) -> None:
    SUBS_PATH.write_text(json.dumps(subs), encoding="utf-8")


def add_subscriber(chat_id: int) -> None:
    subs = load_subs()
    if chat_id not in subs:
        subs.append(chat_id)
        save_subs(subs)


# ---------------------------------------------------------------------------
# Telegram API
# ---------------------------------------------------------------------------

def tg(method: str, **params):
    try:
        r = requests.post(f"{API}/{method}", json=params, timeout=40)
        return r.json()
    except requests.RequestException as exc:
        print(f"[TG lỗi] {method}: {exc}", file=sys.stderr)
        return {"ok": False}


def send(chat_id: int, text: str, markup: dict | None = None, preview: bool = True):
    params = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": not preview,
    }
    if markup:
        params["reply_markup"] = markup
    return tg("sendMessage", **params)


def edit(chat_id: int, msg_id: int, text: str, markup: dict | None = None):
    params = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True}
    if markup:
        params["reply_markup"] = markup
    return tg("editMessageText", **params)


def answer_cb(cb_id: str, text: str = ""):
    tg("answerCallbackQuery", callback_query_id=cb_id, text=text)


def kb(rows) -> dict:
    return {"inline_keyboard": rows}


def btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


# ---------------------------------------------------------------------------
# Định dạng số
# ---------------------------------------------------------------------------

def parse_price_input(text: str) -> int | None:
    """Chấp nhận '700000', '70만', '70만원', '70', '20.000'."""
    t = text.replace(",", "").replace(".", "").replace(" ", "").replace("원", "").lower()
    try:
        if "만" in t:
            man = t.replace("만", "")
            return int(float(man) * 10000) if man else None
        val = int(float(t))
        # Nếu người dùng gõ số nhỏ (<=1000) coi như 만원.
        return val * 10000 if val <= 1000 else val
    except ValueError:
        return None


def parse_range_input(text: str) -> tuple[int, int] | None:
    """Nhận 'TẪ ĐẺN', ví dụ '20000 60000', '2만-6만', '20.000 đến 60.000'."""
    import re as _re
    parts = _re.split(r"[^0-9만.원]+", text.strip())
    nums = [parse_price_input(p) for p in parts if p.strip()]
    nums = [n for n in nums if n is not None]
    if len(nums) < 2:
        return None
    lo, hi = sorted(nums[:2])
    return lo, hi


def won(v: int) -> str:
    return f"{v:,}원 ({v // 10000}만)" if v >= 10000 else f"{v:,}원"


def fallback_title_vi(title: str) -> str:
    """Dịch nhanh tiêu đề Hàn -> Việt khi AI lỗi hoặc hết lượt."""
    t = (title or "").strip()
    if not t:
        return "Sản phẩm"
    rep = {
        "아이폰": "iPhone",
        "갤럭시": "Galaxy",
        "스마트폰": "điện thoại thông minh",
        "휴대폰": "điện thoại",
        "핸드폰": "điện thoại",
        "자급제": "bản quốc tế",
        "공기계": "máy trần",
        "미개봉": "chưa bóc seal",
        "미사용": "chưa sử dụng",
        "풀박스": "đủ hộp phụ kiện",
        "배터리": "pin",
        "효율": "hiệu suất",
        "무잔상": "không ám màn",
        "잔상": "ám màn",
    }
    for k, v in rep.items():
        t = t.replace(k, v)
    return re.sub(r"\s+", " ", t).strip()


def deal_badge(title: str, price: int | None, is_free: bool) -> str | None:
    """Ước lượng nhanh deal hời theo model phổ biến."""
    if is_free:
        return "🔥 SIÊU HỜI: miễn phí"
    if price is None:
        return None
    t = (title or "").lower()
    if "아이폰 15" in t and price <= 400000:
        return "🔥 GIÁ HỜI (iPhone 15)"
    if "아이폰 14" in t and price <= 300000:
        return "🔥 GIÁ HỜI (iPhone 14)"
    if "아이폰 13" in t and price <= 220000:
        return "🔥 GIÁ HỜI (iPhone 13)"
    if "갤럭시 s24" in t and price <= 350000:
        return "🔥 GIÁ HỜI (Galaxy S24)"
    if "갤럭시 s23" in t and price <= 250000:
        return "🔥 GIÁ HỜI (Galaxy S23)"
    return None


def is_quiet_hours(cfg: dict) -> bool:
    """True nếu đang nằm trong khung giờ yên lặng do người dùng đặt."""
    if not cfg.get("quiet_hours_enabled", False):
        return False
    st = int(cfg.get("quiet_start_hour", 23) or 23) % 24
    en = int(cfg.get("quiet_end_hour", 7) or 7) % 24
    h = time.localtime().tm_hour
    if st == en:
        return True
    if st < en:
        return st <= h < en
    return h >= st or h < en


# ---------------------------------------------------------------------------
# MENU
# ---------------------------------------------------------------------------

def main_menu_markup(cfg: dict) -> dict:
    free = "BẬT ✅" if cfg.get("free_electronics") else "TẮT ⬜"
    lo = cfg.get("phone_min_price", 0) or 0
    hi = cfg.get("phone_max_price", 0) or 0
    return kb([
        [btn("🔍 Quét ngay", "scan"), btn("⏹ Dừng quét", "stopscan")],
        [btn(f"💰 Giá máy: {won(lo)} → {won(hi)}", "price")],
        [btn(f"🎁 Đồ điện tử miễn phí: {free}", "togglefree")],
        [btn(f"⏱ Tần suất: {cfg.get('scan_interval_minutes')} phút", "interval")],
        [btn("⚙️ Cài đặt lọc", "settings")],
        [btn("📊 Trạng thái", "status")],
    ])


def main_menu_text(cfg: dict) -> str:
    lo = cfg.get("phone_min_price", 0) or 0
    hi = cfg.get("phone_max_price", 0) or 0
    n_region = len(cfg.get("regions", []))
    return (
        "🥕 <b>Daangn Phone Hunter</b>\n\n"
        f"📱 Săn MỌI máy giá: <b>{won(lo)} → {won(hi)}</b>\n"
        f"🎁 Ưu tiên đồ miễn phí: <b>{'bật' if cfg.get('free_electronics') else 'tắt'}</b>\n"
        f"🌍 Khu vực: <b>{n_region}</b>\n"
        f"⏱ Quét mỗi <b>{cfg.get('scan_interval_minutes')}</b> phút\n\n"
        "Chỉ quét máy còn tốt (loại chập nguồn / ố màn / bể nát).\n"
        "Chọn một mục bên dưới:"
    )


def show_main(chat_id: int, msg_id: int | None = None):
    cfg = load_config()
    if msg_id:
        edit(chat_id, msg_id, main_menu_text(cfg), main_menu_markup(cfg))
    else:
        send(chat_id, main_menu_text(cfg), main_menu_markup(cfg))


def price_markup(cfg: dict) -> dict:
    rows = [[btn("✏️ Nhập khoảng giá (từ – đến)", "setrange")]]
    for lo, hi in PRICE_PRESETS:
        rows.append([btn(f"{won(lo)} → {won(hi)}", f"pr:{lo}:{hi}")])
    rows.append([btn("⬅️ Về menu chính", "home")])
    return kb(rows)


def show_price(chat_id: int, msg_id: int):
    cfg = load_config()
    lo = cfg.get("phone_min_price", 0) or 0
    hi = cfg.get("phone_max_price", 0) or 0
    txt = (
        "💰 <b>Giá máy muốn săn</b>\n\n"
        f"Hiện tại: từ <b>{won(lo)}</b> đến <b>{won(hi)}</b>\n\n"
        "Bot sẽ tìm MỌI loại máy trong khoảng giá này — không cần thêm từng máy.\n"
        "Chọn nhanh hoặc bấm “Nhập khoảng giá”:"
    )
    edit(chat_id, msg_id, txt, price_markup(cfg))


def watch_markup(cfg: dict) -> dict:
    rows = []
    for i, w in enumerate(cfg.get("watch", [])):
        mx = w.get("max_price")
        label = f"{w['keyword']}  ≤ {mx // 10000}만" if mx else w["keyword"]
        rows.append([btn(f"📱 {label}", f"w:{i}")])
    rows.append([btn("➕ Thêm máy", "addmenu")])
    rows.append([btn("⬅️ Về menu chính", "home")])
    return kb(rows)


def show_watch(chat_id: int, msg_id: int):
    cfg = load_config()
    txt = "💰 <b>Máy đang săn</b>\n\nBấm vào một máy để đặt giá hoặc xóa:"
    edit(chat_id, msg_id, txt, watch_markup(cfg))


def watch_detail_markup(idx: int) -> dict:
    return kb([
        [btn("💵 Đặt giá tối đa", f"setmax:{idx}")],
        [btn("💵 Đặt giá tối thiểu", f"setmin:{idx}")],
        [btn("🗑 Xóa máy này", f"del:{idx}")],
        [btn("⬅️ Quay lại", "watch")],
    ])


def show_watch_detail(chat_id: int, msg_id: int, idx: int):
    cfg = load_config()
    watch = cfg.get("watch", [])
    if idx >= len(watch):
        return show_watch(chat_id, msg_id)
    w = watch[idx]
    mn = w.get("min_price", 0) or 0
    mx = w.get("max_price")
    txt = (
        f"📱 <b>{html.escape(w['keyword'])}</b>\n\n"
        f"Giá tối thiểu: {won(mn) if mn else 'không đặt'}\n"
        f"Giá tối đa: {won(mx) if mx else 'không đặt'}"
    )
    edit(chat_id, msg_id, txt, watch_detail_markup(idx))


def add_menu_markup() -> dict:
    rows = []
    row = []
    for i, (label, _kw) in enumerate(PRESETS):
        row.append(btn(label, f"add:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn("⌨️ Gõ tên khác (Hàn/Việt)", "addcustom")])
    rows.append([btn("⬅️ Quay lại", "watch")])
    return kb(rows)


def interval_markup(cfg: dict) -> dict:
    cur = cfg.get("scan_interval_minutes")
    rows = [[btn(("● " if m == cur else "") + f"{m} phút", f"int:{m}")] for m in INTERVALS]
    rows.append([btn("⬅️ Về menu chính", "home")])
    return kb(rows)


def settings_markup(cfg: dict) -> dict:
    def mark(v):
        return "✅" if v else "⬜"
    fl = cfg.get("free_limit", 20)
    pl = cfg.get("phone_limit", 20)
    sd = int(cfg.get("send_delay_seconds", 10) or 0)
    mb = int(cfg.get("min_battery_percent", 80) or 0)
    q_on = cfg.get("quiet_hours_enabled", False)
    q_st = int(cfg.get("quiet_start_hour", 23) or 23)
    q_en = int(cfg.get("quiet_end_hour", 7) or 7)
    return kb([
        [btn(f"{mark(cfg.get('phones_only', True))} Chỉ điện thoại (loại vỏ/ốp)", "t:phones_only")],
        [btn(f"{mark(cfg.get('strict_good', True))} Chỉ máy còn tốt (nghiêm ngặt)", "t:strict_good")],
        [btn(f"{mark(cfg.get('skip_broken'))} Bỏ máy hỏng/lỗi", "t:skip_broken")],
        [btn(f"{mark(cfg.get('skip_sold'))} Bỏ tin đã bán", "t:skip_sold")],
        [btn(f"{mark(cfg.get('skip_reserved'))} Bỏ tin đang giữ chỗ", "t:skip_reserved")],
        [btn(f"{mark(cfg.get('use_ai'))} AI dịch & phân tích (Groq)", "t:use_ai")],
        [btn(f"{mark(cfg.get('digest_mode', False))} Chế độ gửi gộp (digest)", "t:digest_mode")],
        [btn(f"{mark(q_on)} Giờ yên lặng ({q_st}:00-{q_en}:00)", "t:quiet_hours_enabled")],
        [btn("🌙 Đặt giờ yên lặng", "setquiet")],
        [btn(f"🔋 Pin tối thiểu: {mb}%", "setbattery")],
        [btn(f"🔢 Giới hạn: {fl} free / {pl} máy / lượt", "setlimit")],
        [btn(f"⏳ Giãn gửi: {sd}s / tin", "setdelay")],
        [btn("⬅️ Về menu chính", "home")],
    ])


def show_settings(chat_id: int, msg_id: int):
    cfg = load_config()
    edit(chat_id, msg_id, "⚙️ <b>Cài đặt lọc</b>\n\nBấm để bật/tắt:", settings_markup(cfg))


# ---------------------------------------------------------------------------
# Xử lý callback / message
# ---------------------------------------------------------------------------

def handle_callback(cb: dict):
    data = cb.get("data", "")
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id")
    cb_id = cb.get("id")
    add_subscriber(chat_id)

    if data == "home":
        answer_cb(cb_id)
        return show_main(chat_id, msg_id)
    if data == "price" or data == "watch":
        answer_cb(cb_id)
        return show_price(chat_id, msg_id)
    if data == "setrange":
        pending[chat_id] = {"action": "setrange", "msg_id": msg_id}
        answer_cb(cb_id)
        return send(chat_id, "✏️ Gửi khoảng giá <b>TẪ ĐẺN</b> (won), ví dụ:\n"
                             "<b>20000 60000</b>  hoặc  <b>2만 6만</b>")
    if data.startswith("pr:"):
        _, lo, hi = data.split(":")
        with cfg_lock:
            cfg = load_config()
            cfg["phone_min_price"] = int(lo)
            cfg["phone_max_price"] = int(hi)
            save_config(cfg)
        answer_cb(cb_id, "Đã đặt khoảng giá")
        return show_price(chat_id, msg_id)
    if data == "addmenu":
        answer_cb(cb_id)
        return edit(chat_id, msg_id, "➕ <b>Thêm máy cần săn</b>\nChọn mẫu hoặc gõ tên:", add_menu_markup())
    if data == "settings":
        answer_cb(cb_id)
        return show_settings(chat_id, msg_id)
    if data == "interval":
        answer_cb(cb_id)
        cfg = load_config()
        return edit(chat_id, msg_id, "⏱ <b>Tần suất quét</b>\nChọn khoảng thời gian:", interval_markup(cfg))
    if data == "status":
        answer_cb(cb_id)
        cfg = load_config()
        t = last_scan_info["time"]
        tstr = time.strftime("%H:%M:%S %d/%m", time.localtime(t)) if t else "chưa quét"
        txt = (
            "📊 <b>Trạng thái</b>\n\n"
            f"Lần quét gần nhất: {tstr}\n"
            f"Tin mới lần trước: {last_scan_info['found']}\n"
            f"AI: {'bật' if cfg.get('use_ai') and GROQ_KEY else 'tắt'}\n"
            f"Đồ miễn phí: {'bật' if cfg.get('free_electronics') else 'tắt'}"
        )
        return edit(chat_id, msg_id, txt, kb([[btn("⬅️ Về menu chính", "home")]]))

    if data == "togglefree":
        with cfg_lock:
            cfg = load_config()
            cfg["free_electronics"] = not cfg.get("free_electronics")
            save_config(cfg)
        answer_cb(cb_id, "Đã cập nhật")
        return show_main(chat_id, msg_id)

    if data == "scan":
        answer_cb(cb_id, "Bắt đầu quét...")
        cancel_scan.clear()
        edit(chat_id, msg_id, "🔍 Đang quét... (vài phút)", kb([[btn("⏹ Dừng quét", "stopscan")], [btn("⬅️ Về menu chính", "home")]]))
        threading.Thread(target=run_scan, kwargs={"manual_chat": chat_id}, daemon=True).start()
        return

    if data == "stopscan":
        cancel_scan.set()
        answer_cb(cb_id, "Đang dừng quét...")
        return send(chat_id, "⏹ Đã yêu cầu dừng. Lượt quét sẽ ngừng sau tin hiện tại.")

    if data == "setlimit":
        pending[chat_id] = {"action": "setlimit", "msg_id": msg_id}
        answer_cb(cb_id)
        return send(chat_id, "🔢 Gửi giới hạn <b>FREE MÁY</b> mỗi lượt (2 số), ví dụ:\n"
                             "<b>20 20</b>  (20 đồ free + 20 điện thoại)")

    if data == "setdelay":
        pending[chat_id] = {"action": "setdelay", "msg_id": msg_id}
        answer_cb(cb_id)
        return send(chat_id, "⏳ Gửi số giây giãn cách mỗi tin, ví dụ <b>10</b>")

    if data == "setbattery":
        pending[chat_id] = {"action": "setbattery", "msg_id": msg_id}
        answer_cb(cb_id)
        return send(chat_id, "🔋 Gửi ngưỡng pin tối thiểu (%), ví dụ <b>80</b>")

    if data == "setquiet":
        pending[chat_id] = {"action": "setquiet", "msg_id": msg_id}
        answer_cb(cb_id)
        return send(chat_id, "🌙 Gửi giờ yên lặng <b>BẮT ĐẦU KẾT THÚC</b> (0-23), ví dụ <b>23 7</b>")

    if data.startswith("int:"):
        m = int(data.split(":")[1])
        with cfg_lock:
            cfg = load_config()
            cfg["scan_interval_minutes"] = m
            save_config(cfg)
        answer_cb(cb_id, f"Đặt {m} phút")
        return show_main(chat_id, msg_id)

    if data.startswith("t:"):
        field = data.split(":")[1]
        with cfg_lock:
            cfg = load_config()
            cfg[field] = not cfg.get(field)
            save_config(cfg)
        answer_cb(cb_id, "Đã đổi")
        return show_settings(chat_id, msg_id)

    if data.startswith("w:"):
        answer_cb(cb_id)
        return show_watch_detail(chat_id, msg_id, int(data.split(":")[1]))

    if data.startswith("del:"):
        idx = int(data.split(":")[1])
        with cfg_lock:
            cfg = load_config()
            if idx < len(cfg["watch"]):
                removed = cfg["watch"].pop(idx)
                save_config(cfg)
                answer_cb(cb_id, f"Đã xóa {removed['keyword']}")
        return show_watch(chat_id, msg_id)

    if data.startswith("setmax:") or data.startswith("setmin:"):
        idx = int(data.split(":")[1])
        kind = "max" if data.startswith("setmax") else "min"
        pending[chat_id] = {"action": f"set{kind}", "idx": idx, "msg_id": msg_id}
        answer_cb(cb_id)
        return send(chat_id, f"💵 Gửi mức giá {kind} (ví dụ: <b>700000</b> hoặc <b>70만</b>):")

    if data.startswith("add:"):
        i = int(data.split(":")[1])
        _, kwd = PRESETS[i]
        with cfg_lock:
            cfg = load_config()
            if any(w["keyword"] == kwd for w in cfg["watch"]):
                answer_cb(cb_id, "Đã có rồi")
            else:
                cfg["watch"].append({"keyword": kwd, "min_price": 0, "max_price": 700000})
                save_config(cfg)
                answer_cb(cb_id, f"Đã thêm {kwd}")
        return show_watch(chat_id, msg_id)

    if data == "addcustom":
        pending[chat_id] = {"action": "addkw", "msg_id": msg_id}
        answer_cb(cb_id)
        return send(chat_id, "⌨️ Gõ tên máy (tiếng Hàn tốt nhất, ví dụ <b>아이폰 13 미니</b>). "
                             "Nếu gõ tiếng Việt, AI sẽ tự chuyển.")

    answer_cb(cb_id)


def vi_to_korean_keyword(text: str) -> str:
    """Chuyển tên máy tiếng Việt sang từ khóa tiếng Hàn bằng Groq (nếu cần)."""
    if not GROQ_KEY or any("\uac00" <= c <= "\ud7a3" for c in text):
        return text  # đã có chữ Hàn hoặc không có key
    try:
        body = {
            "model": groq_ai.DEFAULT_MODEL,
            "messages": [
                {"role": "system", "content": "Trả về DUY NHẤT từ khóa tìm kiếm tiếng Hàn cho tên thiết bị, không giải thích."},
                {"role": "user", "content": f"Tên thiết bị: {text}"},
            ],
            "temperature": 0,
            "max_tokens": 30,
        }
        r = requests.post(groq_ai.GROQ_URL,
                          headers={"Authorization": f"Bearer {GROQ_KEY}"},
                          json=body, timeout=20)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip().strip('"')
    except (requests.RequestException, KeyError, IndexError):
        pass
    return text


def handle_message(msg: dict):
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    add_subscriber(chat_id)

    if text in ("/start", "/menu"):
        pending.pop(chat_id, None)
        return show_main(chat_id)
    if text == "/scan":
        send(chat_id, "🔍 Đang quét...")
        return threading.Thread(target=run_scan, kwargs={"manual_chat": chat_id}, daemon=True).start()

    state = pending.get(chat_id)
    if not state:
        return show_main(chat_id)

    action = state["action"]
    if action == "setrange":
        rng = parse_range_input(text)
        if rng is None:
            return send(chat_id, "⚠️ Chưa hiểu. Gửi 2 số TẪ ĐẺN, ví dụ: <b>20000 60000</b>")
        lo, hi = rng
        with cfg_lock:
            cfg = load_config()
            cfg["phone_min_price"] = lo
            cfg["phone_max_price"] = hi
            save_config(cfg)
        pending.pop(chat_id, None)
        send(chat_id, f"✅ Đã đặt khoảng giá: từ <b>{won(lo)}</b> đến <b>{won(hi)}</b>.")
        return show_main(chat_id)
    if action == "setlimit":
        import re as _re
        nums = [int(n) for n in _re.findall(r"\d+", text)]
        if len(nums) < 2:
            return send(chat_id, "⚠️ Gửi 2 số: FREE rồi MÁY, ví dụ <b>20 20</b>")
        with cfg_lock:
            cfg = load_config()
            cfg["free_limit"] = max(0, nums[0])
            cfg["phone_limit"] = max(0, nums[1])
            save_config(cfg)
        pending.pop(chat_id, None)
        send(chat_id, f"✅ Mỗi lượt quét tối đa: <b>{nums[0]}</b> đồ free + <b>{nums[1]}</b> điện thoại.")
        return show_main(chat_id)
    if action == "setdelay":
        nums = [int(n) for n in re.findall(r"\d+", text)]
        if not nums:
            return send(chat_id, "⚠️ Gửi số giây hợp lệ, ví dụ <b>10</b>")
        delay = max(0, min(30, nums[0]))
        with cfg_lock:
            cfg = load_config()
            cfg["send_delay_seconds"] = delay
            save_config(cfg)
        pending.pop(chat_id, None)
        send(chat_id, f"✅ Đã đặt giãn gửi: <b>{delay}</b> giây/tin.")
        return show_main(chat_id)
    if action == "setbattery":
        nums = [int(n) for n in re.findall(r"\d+", text)]
        if not nums:
            return send(chat_id, "⚠️ Gửi % pin hợp lệ, ví dụ <b>80</b>")
        pin = max(50, min(100, nums[0]))
        with cfg_lock:
            cfg = load_config()
            cfg["min_battery_percent"] = pin
            save_config(cfg)
        pending.pop(chat_id, None)
        send(chat_id, f"✅ Đã đặt ngưỡng pin tối thiểu: <b>{pin}%</b>.")
        return show_main(chat_id)
    if action == "setquiet":
        nums = [int(n) for n in re.findall(r"\d+", text)]
        if len(nums) < 2:
            return send(chat_id, "⚠️ Gửi 2 số giờ (0-23), ví dụ <b>23 7</b>")
        st = max(0, min(23, nums[0]))
        en = max(0, min(23, nums[1]))
        with cfg_lock:
            cfg = load_config()
            cfg["quiet_start_hour"] = st
            cfg["quiet_end_hour"] = en
            save_config(cfg)
        pending.pop(chat_id, None)
        send(chat_id, f"✅ Đã đặt giờ yên lặng: <b>{st}:00 → {en}:00</b>.")
        return show_main(chat_id)
    if action in ("setmax", "setmin"):
        price = parse_price_input(text)
        if price is None:
            return send(chat_id, "⚠️ Không hiểu giá. Gửi lại số (vd 700000 hoặc 70만):")
        with cfg_lock:
            cfg = load_config()
            idx = state["idx"]
            if idx < len(cfg["watch"]):
                key = "max_price" if action == "setmax" else "min_price"
                cfg["watch"][idx][key] = price
                save_config(cfg)
        pending.pop(chat_id, None)
        send(chat_id, f"✅ Đã đặt giá {won(price)}.")
        return show_main(chat_id)

    if action == "addkw":
        kwd = vi_to_korean_keyword(text)
        with cfg_lock:
            cfg = load_config()
            if any(w["keyword"] == kwd for w in cfg["watch"]):
                send(chat_id, "Máy này đã có trong danh sách.")
            else:
                cfg["watch"].append({"keyword": kwd, "min_price": 0, "max_price": 700000})
                save_config(cfg)
                send(chat_id, f"✅ Đã thêm: <b>{html.escape(kwd)}</b> (giá tối đa 70만, sửa trong menu).")
        pending.pop(chat_id, None)
        return show_main(chat_id)


# ---------------------------------------------------------------------------
# QUÉT
# ---------------------------------------------------------------------------

def match_phone(item: dict, watch: dict, cfg: dict, cond: dict) -> bool:
    status = item.get("status", "")
    if cfg.get("skip_sold", True) and status in ("Closed", "Traded"):
        return False
    if cfg.get("skip_reserved", True) and status == "Reserved":
        return False
    if cfg.get("skip_broken", True) and cond["broken"]:
        return False
    # Chi may con tot: loai dau hieu loi nhe (o man / xuoc nhieu) va pin yeu.
    if cfg.get("strict_good", True):
        if cond.get("soft_bad"):
            return False
        min_bat = int(cfg.get("min_battery_percent", 80) or 0)
        if cond.get("battery") is not None and cond["battery"] < min_bat:
            return False
    price = item["price"]
    if price is None:
        return False
    if price < (watch.get("min_price", 0) or 0):
        return False
    mx = watch.get("max_price")
    if mx is not None and mx > 0 and price > mx:
        return False
    hay = (item["title"] + " " + item["content"]).lower()
    return not any(b and b.lower() in hay for b in cfg.get("exclude_words", []))


def match_free(item: dict, cfg: dict, cond: dict) -> bool:
    status = item.get("status", "")
    if cfg.get("skip_sold", True) and status in ("Closed", "Traded"):
        return False
    if cfg.get("skip_reserved", True) and status == "Reserved":
        return False
    if cfg.get("skip_broken", True) and cond["broken"]:
        return False
    return True


def build_message(item: dict, cond: dict, keyword: str, is_free: bool, vi: dict | None) -> str:
    esc = html.escape
    ten = (vi or {}).get("ten") or fallback_title_vi(item["title"])
    head = "🎁 <b>[MIỄN PHÍ]</b> " if is_free else "📱 "
    lines = [f"{head}<b>{esc(ten)}</b>"]
    if not is_free:
        lines.append(f"💰 {item['price']:,}원")
    else:
        lines.append("💰 Miễn phí (đồ cho tặng)")
    hot = deal_badge(item.get("title", ""), item.get("price"), is_free)
    if hot:
        lines.append(hot)
    lines.append(f"📍 {esc(item['region'])}")
    if cond["battery"] is not None:
        lines.append(f"🔋 Pin: {cond['battery']}%")
    sig = ("  (" + ", ".join(esc(s) for s in cond["signals"]) + ")") if cond["signals"] else ""
    lines.append(f"🩺 Tình trạng: {cond['label']}{sig}")
    if not is_free:
        lines.append(f"🤝 Thương lượng: {scraper.detect_negotiable(item['content'])}")
    lines.append("💬 Liên hệ: nhắn qua app 당근 (Daangn)")
    if (vi or {}).get("danhgia"):
        lines.append(f"🤖 {esc(vi['danhgia'])}")
    if item["seller"]:
        lines.append(f"👤 {esc(item['seller'])}")
    lines.append(f"🔗 {item['link']}")
    # Tên gốc tiếng Hàn (nhỏ) để đối chiếu
    if (vi or {}).get("ten"):
        lines.append(f"<i>🇰🇷 {esc(item['title'])}</i>")
    return "\n".join(lines)


def run_scan(manual_chat: int | None = None):
    if not scan_lock.acquire(blocking=False):
        if manual_chat:
            send(manual_chat, "⏳ Đang có lượt quét khác chạy, thử lại sau.")
        return
    try:
        cfg = load_config()
        seen = load_seen()
        subs = load_subs()
        targets = [manual_chat] if manual_chat else subs
        if not targets:
            print("[Quét] chưa có người nhận (chưa /start).")
        ai_on = bool(cfg.get("use_ai") and GROQ_KEY)
        ai_budget = int(cfg.get("ai_max_calls", 30))
        found = 0
        free_count = 0
        phone_count = 0
        free_limit = int(cfg.get("free_limit", 20) or 0)
        phone_limit = int(cfg.get("phone_limit", 20) or 0)
        send_delay = float(cfg.get("send_delay_seconds", 10) or 0)
        digest_mode = bool(cfg.get("digest_mode", False))
        quiet_now = is_quiet_hours(cfg) and manual_chat is None
        stopped = False
        processed: set[str] = set()
        digests: dict[int, list[str]] = {t: [] for t in targets}

        def dispatch_item(msg: str) -> None:
            if quiet_now:
                return
            if digest_mode:
                for t in targets:
                    digests[t].append(msg)
                return
            for t in targets:
                send(t, msg)
            if send_delay > 0:
                time.sleep(send_delay)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=cfg.get("headless", True))
            ctx = browser.new_context(
                user_agent=scraper.USER_AGENT,
                locale="ko-KR",
                extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
            )
            page = ctx.new_page()

            gmin = int(cfg.get("phone_min_price", 0) or 0)
            gmax = int(cfg.get("phone_max_price", 0) or 0)
            grange = {"min_price": gmin, "max_price": gmax}
            kws = cfg.get("phone_keywords") or ["아이폰", "갤럭시", "휴대폰", "스마트폰"]

            def scan_free_region(rid, rname):
                nonlocal found, ai_budget, free_count, stopped
                if free_limit and free_count >= free_limit:
                    return
                try:
                    free_items = scraper.scrape_free(page, rid, rname)
                except Exception as exc:  # noqa: BLE001
                    print(f"  [Lỗi free] @ {rname}: {exc}", file=sys.stderr)
                    return
                for it in free_items:
                    if cancel_scan.is_set():
                        stopped = True
                        return
                    if free_limit and free_count >= free_limit:
                        return
                    if it["id"] in processed:
                        continue
                    cond = scraper.analyze_condition(it["title"] + "\n" + it["content"])
                    if not match_free(it, cfg, cond):
                        continue
                    processed.add(it["id"])
                    if it["id"] in seen:
                        continue
                    vi = None
                    if ai_on and ai_budget > 0:
                        vi = groq_ai.describe_vi(it, cond, GROQ_KEY, cfg.get("ai_model"), is_free=True)
                        if vi:
                            ai_budget -= 1
                    msg = build_message(it, cond, "나눔", True, vi)
                    found += 1
                    free_count += 1
                    dispatch_item(msg)
                    if not quiet_now:
                        seen.add(it["id"])

            def scan_phones_region(rid, rname):
                nonlocal found, ai_budget, phone_count, stopped
                for kw in kws:
                    if phone_limit and phone_count >= phone_limit:
                        return
                    try:
                        items = scraper.scrape_keyword(page, rid, rname, kw, gmin, gmax)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  [Lỗi] {kw} @ {rname}: {exc}", file=sys.stderr)
                        continue
                    for it in items:
                        if cancel_scan.is_set():
                            stopped = True
                            return
                        if phone_limit and phone_count >= phone_limit:
                            return
                        if it["id"] in processed:
                            continue
                        # Loại vỏ/ốp/phụ kiện và tin không phải điện thoại.
                        if cfg.get("phones_only", True):
                            if scraper.clearly_not_phone(it["title"], it["content"]):
                                continue
                            if scraper.is_accessory(it["title"], it["content"]):
                                continue
                            if not scraper.looks_like_phone(it["title"], it["content"]):
                                continue
                        cond = scraper.analyze_condition(it["title"] + "\n" + it["content"])
                        if not match_phone(it, grange, cfg, cond):
                            continue
                        processed.add(it["id"])
                        if it["id"] in seen:
                            continue
                        vi = None
                        if ai_on and ai_budget > 0:
                            vi = groq_ai.describe_vi(it, cond, GROQ_KEY, cfg.get("ai_model"))
                            if vi:
                                ai_budget -= 1
                            # AI thẩm định: bỏ qua nếu không phải điện thoại hoặc đang hỏng.
                            if cfg.get("phones_only", True) and vi and vi.get("bo_qua"):
                                continue
                        msg = build_message(it, cond, kw, False, vi)
                        found += 1
                        phone_count += 1
                        dispatch_item(msg)
                        if not quiet_now:
                            seen.add(it["id"])

            for region in cfg.get("regions", []):
                if stopped or cancel_scan.is_set():
                    stopped = True
                    break
                done_free = (not free_limit) or free_count >= free_limit
                done_phone = (not phone_limit) or phone_count >= phone_limit
                if done_free and done_phone:
                    break
                rid = str(region.get("id"))
                rname = region.get("name", "")
                # Ưu tiên đồ MIỄN PHÍ trước
                if cfg.get("free_electronics") and cfg.get("free_first", True):
                    scan_free_region(rid, rname)
                # Quét điện thoại trong khoảng giá
                scan_phones_region(rid, rname)
                # Nếu không ưu tiên free thì quét free sau
                if cfg.get("free_electronics") and not cfg.get("free_first", True):
                    scan_free_region(rid, rname)

            browser.close()

        # Gửi theo chế độ digest sau khi quét xong để giảm spam thông báo.
        if digest_mode and not quiet_now:
            for t, items in digests.items():
                if not items:
                    continue
                send(t, f"📦 Bản tin gộp: <b>{len(items)}</b> tin mới (free {free_count}, máy {phone_count}).")
                chunk_size = 5
                for i in range(0, len(items), chunk_size):
                    send(t, "\n\n━━━━━━━━━━\n\n".join(items[i:i + chunk_size]))
                    if send_delay > 0:
                        time.sleep(send_delay)

        save_seen(seen)
        last_scan_info["time"] = time.time()
        last_scan_info["found"] = found
        cancel_scan.clear()
        print(f"[Quét xong] tin mới: {found} (free {free_count}, máy {phone_count})"
              + (" — ĐÃ DỪNG" if stopped else ""))
        if manual_chat:
            tin = f"✅ Quét xong. Tin mới: <b>{found}</b> (free {free_count}, máy {phone_count})."
            if stopped:
                tin = f"⏹ Đã dừng. Đã gửi: <b>{found}</b> tin (free {free_count}, máy {phone_count})."
            send(manual_chat, tin)
        elif quiet_now and found > 0:
            print(f"[Quiet Hours] Tạm hoãn gửi {found} tin do đang trong giờ yên lặng.")
    except Exception as exc:  # noqa: BLE001
        print(f"[Quét lỗi] {exc}", file=sys.stderr)
        if manual_chat:
            send(manual_chat, f"⚠️ Lỗi khi quét: {exc}")
    finally:
        scan_lock.release()


def scanner_loop():
    # Quét lần đầu sau 5s rồi lặp theo tần suất.
    stop_event.wait(5)
    while not stop_event.is_set():
        run_scan()
        cfg = load_config()
        interval = max(5, int(cfg.get("scan_interval_minutes", 30))) * 60
        # Ngủ theo từng giây để có thể đổi tần suất / dừng nhanh.
        for _ in range(interval):
            if stop_event.is_set():
                return
            time.sleep(1)


# ---------------------------------------------------------------------------
# Vòng lặp Telegram (long polling)
# ---------------------------------------------------------------------------

def setup_commands():
    tg("setMyCommands", commands=[
        {"command": "menu", "description": "Mở menu thiết lập"},
        {"command": "scan", "description": "Quét ngay"},
        {"command": "start", "description": "Bắt đầu"},
    ])


def polling_loop():
    offset = None
    print("[Bot] Đang lắng nghe Telegram...")
    while not stop_event.is_set():
        try:
            r = requests.get(f"{API}/getUpdates",
                             params={"offset": offset, "timeout": 30}, timeout=40)
            data = r.json()
        except requests.RequestException:
            time.sleep(3)
            continue
        if not data.get("ok"):
            time.sleep(3)
            continue
        for upd in data.get("result", []):
            offset = upd["update_id"] + 1
            try:
                if "callback_query" in upd:
                    handle_callback(upd["callback_query"])
                elif "message" in upd:
                    handle_message(upd["message"])
            except Exception as exc:  # noqa: BLE001
                print(f"[Xử lý update lỗi] {exc}", file=sys.stderr)


def main() -> int:
    if not TOKEN:
        print("Thiếu TELEGRAM_BOT_TOKEN trong .env", file=sys.stderr)
        return 1
    load_config()  # tạo config.json nếu chưa có
    setup_commands()
    if OWNER_CHAT.isdigit():
        add_subscriber(int(OWNER_CHAT))
    threading.Thread(target=scanner_loop, daemon=True).start()
    try:
        polling_loop()
    except KeyboardInterrupt:
        stop_event.set()
        print("\n[Bot] Đã dừng.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
