#!/usr/bin/env python3
"""Quét MỘT lượt — dùng cho GitHub Actions (cron).

- Lấy cài đặt (giá, máy, bật/tắt) từ Cloudflare Worker (KV) nếu có,
  nếu không thì đọc config.json tại chỗ.
- Lấy danh sách người nhận từ Worker, nếu không thì dùng TELEGRAM_CHAT_ID.
- Quét daangn bằng Playwright (scraper.py) + dịch tiếng Việt (groq_ai.py).
- Chống trùng bằng seen.json (GitHub Actions commit lại sau mỗi lần chạy).

Biến môi trường:
  TELEGRAM_BOT_TOKEN  (bắt buộc)
  GROQ_API_KEY        (tùy chọn, để dịch tiếng Việt)
  TELEGRAM_CHAT_ID    (dự phòng nếu không dùng Worker)
  WORKER_URL          (tùy chọn, vd https://daangn-bot.xxx.workers.dev)
  WORKER_SECRET       (tùy chọn, khớp EXPORT_SECRET của Worker)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

import scraper
import groq_ai
import bot  # tái dùng send(), match_phone(), match_free(), build_message()...

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE_DIR = Path(__file__).resolve().parent
WORKER_URL = os.environ.get("WORKER_URL", "").strip().rstrip("/")
WORKER_SECRET = os.environ.get("WORKER_SECRET", "").strip()
LAST_RUN_PATH = BASE_DIR / "last_run.json"
FORCE_SCAN = os.environ.get("FORCE_SCAN", "0").strip() == "1"


def load_last_run_ts() -> float:
    try:
        data = json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))
        return float(data.get("last_run_ts", 0))
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return 0.0


def save_last_run_ts(ts: float) -> None:
    LAST_RUN_PATH.write_text(
        json.dumps({"last_run_ts": ts}, ensure_ascii=False),
        encoding="utf-8",
    )


def notify_worker_scan_done(found: int, free_count: int, phone_count: int) -> None:
    if not WORKER_URL or not WORKER_SECRET:
        return
    try:
        requests.post(
            f"{WORKER_URL}/ping",
            params={"key": WORKER_SECRET},
            json={"found": found, "free": free_count, "phone": phone_count},
            timeout=10,
        )
    except requests.RequestException as exc:
        print(f"[Worker] lỗi báo scan xong: {exc}", file=sys.stderr)


def fetch_remote_state() -> tuple[dict | None, list[int] | None]:
    """Lấy config + subscribers từ Worker. Trả (None, None) nếu không có."""
    if not WORKER_URL:
        return None, None
    try:
        r = requests.get(f"{WORKER_URL}/export",
                         params={"key": WORKER_SECRET}, timeout=20)
        if r.status_code != 200:
            print(f"[Worker] /export trả {r.status_code}: {r.text[:120]}", file=sys.stderr)
            return None, None
        data = r.json()
        cfg = data.get("config")
        subs = data.get("subscribers")
        subs = [int(s) for s in subs] if isinstance(subs, list) else None
        return (cfg if isinstance(cfg, dict) else None), subs
    except (requests.RequestException, ValueError) as exc:
        print(f"[Worker] lỗi lấy state: {exc}", file=sys.stderr)
        return None, None


def resolve_config() -> dict:
    cfg, _ = STATE
    if cfg:
        # Bổ sung khóa thiếu từ mặc định.
        for k, v in bot.DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return bot.load_config()


def resolve_subscribers() -> list[int]:
    _, subs = STATE
    if subs:
        return subs
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return [int(chat)] if chat.isdigit() else []


def main() -> int:
    if not bot.TOKEN:
        print("Thiếu TELEGRAM_BOT_TOKEN.", file=sys.stderr)
        return 1

    global STATE
    STATE = fetch_remote_state()

    cfg = resolve_config()
    quiet_now = bot.is_quiet_hours(cfg) and not FORCE_SCAN
    if quiet_now:
        print("[Skip] Đang trong giờ yên lặng, tạm hoãn gửi tin.")
        return 0

    interval_s = max(5, int(cfg.get("scan_interval_minutes", 30))) * 60
    if not FORCE_SCAN:
        last_ts = load_last_run_ts()
        now = time.time()
        if last_ts > 0 and (now - last_ts) < interval_s:
            left = int(interval_s - (now - last_ts))
            print(f"[Skip] Chưa đến kỳ quét theo cài đặt ({left}s nữa).")
            return 0

    targets = resolve_subscribers()
    if not targets:
        print("[Quét] Chưa có người nhận (đặt TELEGRAM_CHAT_ID hoặc /start trên bot).")
        return 0

    seen = bot.load_seen()
    ai_keys = bot.get_groq_keys(cfg)
    ai_on = bool(cfg.get("use_ai") and ai_keys)
    ai_budget = int(cfg.get("ai_max_calls", 30))
    found = 0
    free_count = 0
    phone_count = 0
    free_limit = int(cfg.get("free_limit", 20) or 0)
    phone_limit = int(cfg.get("phone_limit", 20) or 0)
    seen_ttl = int(cfg.get("seen_ttl_hours", 48) or 48)
    send_delay = float(cfg.get("send_delay_seconds", 10) or 0)
    digest_mode = bool(cfg.get("digest_mode", False))
    nationwide = bool(cfg.get("nationwide", True))
    max_age_hours = int(cfg.get("listing_max_age_hours", 24) or 24)
    gmin = int(cfg.get("phone_min_price", 0) or 0)
    gmax = int(cfg.get("phone_max_price", 0) or 0)
    grange = {"min_price": gmin, "max_price": gmax}
    kws = cfg.get("phone_keywords") or ["아이폰", "갤럭시", "휴대폰", "스마트폰"]
    processed: set[str] = set()
    digests: dict[int, list[str]] = {t: [] for t in targets}
    print(f"[Config] nationwide={nationwide}, max_age={max_age_hours}h, price={gmin}-{gmax}, kws={kws}")
    print(f"[Config] ai_on={ai_on}, budget={ai_budget}, targets={targets}")

    def dispatch_item(msg: str) -> None:
        if digest_mode:
            for t in targets:
                digests[t].append(msg)
            return
        for t in targets:
            bot.send(t, msg)
        if send_delay > 0:
            time.sleep(send_delay)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=scraper.USER_AGENT,
            locale="ko-KR",
            extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
        )
        page = ctx.new_page()

        def scan_free_region(rid, rname):
            nonlocal found, ai_budget, free_count
            if free_limit and free_count >= free_limit:
                return
            try:
                free_items = scraper.scrape_free(page, rid, rname)
            except Exception as exc:  # noqa: BLE001
                print(f"  [Lỗi free] @ {rname}: {exc}", file=sys.stderr)
                return
            for it in free_items:
                if free_limit and free_count >= free_limit:
                    return
                if it["id"] in processed:
                    continue
                if bot.seen_recent(seen, it["id"], seen_ttl):
                    continue
                if not bot.pass_region_filter(it, cfg):
                    continue
                if not scraper.is_fresh(it, max_age_hours):
                    continue
                cond = scraper.analyze_condition(it["title"] + "\n" + it["content"])
                if not bot.match_free(it, cfg, cond):
                    continue
                processed.add(it["id"])
                vi = None
                if ai_on and ai_budget > 0:
                    vi = groq_ai.describe_vi(it, cond, ai_keys, cfg.get("ai_model"), is_free=True)
                    if vi:
                        ai_budget -= 1
                msg = bot.build_message(it, cond, "나눔", True, vi)
                found += 1
                free_count += 1
                dispatch_item(msg)
                bot.mark_seen(seen, it["id"])

        def scan_phones_region(rid, rname):
            nonlocal found, ai_budget, phone_count
            for kw in kws:
                if phone_limit and phone_count >= phone_limit:
                    return
                try:
                    items = scraper.scrape_keyword(page, rid, rname, kw, gmin, gmax)
                except Exception as exc:  # noqa: BLE001
                    print(f"  [Lỗi] {kw} @ {rname}: {exc}", file=sys.stderr)
                    continue
                items = sorted(items, key=lambda it: bot.bot_deal_rank(it))
                for it in items:
                    if phone_limit and phone_count >= phone_limit:
                        return
                    if it["id"] in processed:
                        continue
                    if bot.seen_recent(seen, it["id"], seen_ttl):
                        continue
                    if not bot.pass_region_filter(it, cfg):
                        continue
                    if not scraper.is_fresh(it, max_age_hours):
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
                    if not bot.match_phone(it, grange, cfg, cond):
                        continue
                    processed.add(it["id"])
                    vi = None
                    if ai_on and ai_budget > 0:
                        vi = groq_ai.describe_vi(it, cond, ai_keys, cfg.get("ai_model"))
                        if vi:
                            ai_budget -= 1
                    msg = bot.build_message(it, cond, kw, False, vi)
                    found += 1
                    phone_count += 1
                    dispatch_item(msg)
                    bot.mark_seen(seen, it["id"])

        if nationwide:
            region_list = cfg.get("nationwide_regions") or cfg.get("regions", [])
        else:
            region_list = cfg.get("regions", [])
        print(f"[Config] quét {len(region_list)} vùng")
        for region in region_list:
            done_free = (not free_limit) or free_count >= free_limit
            done_phone = (not phone_limit) or phone_count >= phone_limit
            if done_free and done_phone:
                break
            rid = str(region.get("id"))
            rname = region.get("name", "")
            print(f"[Vùng] {rname}")
            if cfg.get("free_electronics") and cfg.get("free_first", True):
                scan_free_region(rid, rname)
            scan_phones_region(rid, rname)
            if cfg.get("free_electronics") and not cfg.get("free_first", True):
                scan_free_region(rid, rname)

        browser.close()

    if digest_mode:
        for t, items in digests.items():
            if not items:
                continue
            bot.send(t, f"📦 Bản tin gộp: <b>{len(items)}</b> tin mới (free {free_count}, máy {phone_count}).")
            chunk_size = 5
            for i in range(0, len(items), chunk_size):
                bot.send(t, "\n\n━━━━━━━━━━\n\n".join(items[i:i + chunk_size]))
                if send_delay > 0:
                    time.sleep(send_delay)

    bot.save_seen(seen)
    save_last_run_ts(time.time())
    notify_worker_scan_done(found, free_count, phone_count)
    print(f"[Quét xong] Tin mới gửi đi: {found} (free {free_count}, máy {phone_count})")
    if FORCE_SCAN or cfg.get("send_scan_summary", True):
        for t in targets:
            bot.send(t, f"✅ Quét xong. Tin mới: <b>{found}</b> (free {free_count}, máy {phone_count}).")
    return 0


STATE: tuple[dict | None, list[int] | None] = (None, None)

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[Quét lỗi] {exc}", file=sys.stderr)
        if bot.TOKEN and FORCE_SCAN:
            try:
                if STATE == (None, None):
                    STATE = fetch_remote_state()
                for target in resolve_subscribers():
                    bot.send(target, f"⚠️ Lỗi khi quét: {exc}")
            except Exception as notify_exc:  # noqa: BLE001
                print(f"[Telegram báo lỗi thất bại] {notify_exc}", file=sys.stderr)
        raise
