/**
 * Daangn Phone Hunter — Cloudflare Worker (menu Telegram 24/7).
 *
 * Bot CHỈ săn ĐIỆN THOẠI dùng được trên daangn (tuyệt đối không quét thứ khác).
 *
 * Menu rút gọn:
 *  - 💰 Đặt khoảng giá
 *  - ⏱ Đặt thời gian quét
 *  - 🔍 Quét ngay  /  ⏹ Dừng quét
 *  - 📊 Trạng thái
 *
 * Vai trò:
 *  - Nhận webhook Telegram, hiển thị menu, lưu cài đặt vào KV (binding BOT_KV).
 *  - Nút "Quét ngay" gọi GitHub Actions (repository_dispatch) chạy Playwright.
 *  - Worker cron tự bắn quét mỗi scan_interval_minutes (có gate chống dội).
 *  - GET /export?key=SECRET cho GitHub Actions lấy cài đặt + người nhận.
 *
 * Biến môi trường (wrangler.toml [vars] / secret):
 *  BOT_TOKEN       (bắt buộc) token Telegram
 *  EXPORT_SECRET   (bắt buộc) chuỗi bí mật, khớp WORKER_SECRET bên Actions
 *  GH_TOKEN        (tùy chọn) PAT GitHub có quyền "repo" để bấm Quét ngay
 *  GH_REPO         (tùy chọn) "user/ten-repo"
 * KV binding: BOT_KV
 */

// Cài đặt CHỈNH ĐƯỢC qua menu (chỉ 2 thứ).
const USER_SETTINGS = {
  phone_min_price: 0,
  phone_max_price: 600000,
  scan_interval_minutes: 30,
};

// Cài đặt ÉP CỨNG — đảm bảo bot chỉ săn điện thoại dùng được, không gì khác.
// Không hiển thị trên menu, không cho người dùng đổi.
const FIXED_CONFIG = {
  // Từ khóa quét — chỉ điện thoại.
  phone_keywords: ["아이폰", "갤럭시", "핸드폰", "휴대폰", "스마트폰"],
  // TẮT hoàn toàn đồ miễn phí — tuyệt đối không quét.
  free_electronics: false,
  free_first: false,
  free_limit: 0,
  // Chỉ điện thoại thật, dùng được, không lỗi.
  phones_only: true,
  strict_good: true,
  min_battery_percent: 70,
  skip_sold: true,
  skip_reserved: true,
  skip_broken: true,
  // Mỗi lượt 40 tin, cách nhau 8 giây.
  phone_limit: 40,
  send_delay_seconds: 8,
  digest_mode: false,
  // Chỉ tin đăng trong 48h; tin đã gửi không lặp lại trong 48h.
  listing_max_age_hours: 48,
  seen_ttl_hours: 48,
  // Quét toàn quốc.
  nationwide: true,
  nationwide_regions: [
    { id: "6035", name: "역삼동" }, { id: "355", name: "신림동" },
    { id: "6052", name: "마곡동" }, { id: "6543", name: "송도동" },
    { id: "1766", name: "봉담읍" }, { id: "1604", name: "별내동" },
    { id: "4245", name: "배곧동" }, { id: "4656", name: "옥정동" },
    { id: "2134", name: "오창읍" }, { id: "2292", name: "불당동" },
    { id: "2333", name: "배방읍" }, { id: "3662", name: "물금읍" },
    { id: "2899", name: "고흥읍" },
  ],
  regions: [
    { id: 6035, name: "역삼동" }, { id: 355, name: "신림동" },
    { id: 6052, name: "마곡동" }, { id: 6543, name: "송도동" },
    { id: 1766, name: "봉담읍" }, { id: 1604, name: "별내동" },
    { id: 4245, name: "배곧동" }, { id: 2292, name: "불당동" },
    { id: 3662, name: "물금읍" }, { id: 2899, name: "고흥읍" },
  ],
  // AI Groq thẩm định + dịch sang tiếng Việt.
  use_ai: true,
  ai_model: "llama-3.3-70b-versatile",
  ai_max_calls: 40,
  groq_api_keys: [],
  exclude_words: ["부품", "수리용", "잠금", "아이클라우드"],
  // Không dùng nữa nhưng giữ để tương thích scan_once/bot.py.
  quiet_hours_enabled: false,
  quiet_start_hour: 23,
  quiet_end_hour: 7,
  region_filter_enabled: false,
  region_filter_terms: [],
};

const INTERVALS = [10, 15, 30, 60, 120];
// Khoảng giá gợi ý nhanh (won): [từ, đến]
const PRICE_PRESETS = [[0, 60000], [0, 150000], [0, 300000], [0, 500000], [0, 600000]];

// --------------------------------------------------------------------------
// KV helpers — chỉ lưu USER_SETTINGS; FIXED_CONFIG luôn ghép vào lúc đọc.
// --------------------------------------------------------------------------
async function getSettings(env) {
  const raw = await env.BOT_KV.get("config");
  let s = raw ? JSON.parse(raw) : {};
  const out = {};
  for (const k of Object.keys(USER_SETTINGS)) {
    out[k] = s[k] !== undefined ? s[k] : USER_SETTINGS[k];
  }
  return out;
}
async function saveSettings(env, settings) {
  // Chỉ ghi các khóa người dùng chỉnh được.
  const clean = {};
  for (const k of Object.keys(USER_SETTINGS)) clean[k] = settings[k];
  await env.BOT_KV.put("config", JSON.stringify(clean));
}
// Config đầy đủ để xuất cho GitHub Actions = user settings + fixed.
async function getFullConfig(env) {
  const s = await getSettings(env);
  return { ...FIXED_CONFIG, ...s };
}
async function getSubs(env) {
  const raw = await env.BOT_KV.get("subscribers");
  return raw ? JSON.parse(raw) : [];
}
async function addSub(env, chatId) {
  const subs = await getSubs(env);
  if (!subs.includes(chatId)) {
    subs.push(chatId);
    await env.BOT_KV.put("subscribers", JSON.stringify(subs));
  }
}
async function getPending(env, chatId) {
  const raw = await env.BOT_KV.get("pending:" + chatId);
  return raw ? JSON.parse(raw) : null;
}
async function setPending(env, chatId, obj) {
  await env.BOT_KV.put("pending:" + chatId, JSON.stringify(obj), { expirationTtl: 600 });
}
async function clearPending(env, chatId) {
  await env.BOT_KV.delete("pending:" + chatId);
}

// --------------------------------------------------------------------------
// Telegram API
// --------------------------------------------------------------------------
async function tg(env, method, params) {
  const r = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return r.json();
}
const send = (env, chatId, text, markup) =>
  tg(env, "sendMessage", {
    chat_id: chatId, text, parse_mode: "HTML",
    disable_web_page_preview: true,
    ...(markup ? { reply_markup: markup } : {}),
  });
const edit = (env, chatId, msgId, text, markup) =>
  tg(env, "editMessageText", {
    chat_id: chatId, message_id: msgId, text, parse_mode: "HTML",
    disable_web_page_preview: true,
    ...(markup ? { reply_markup: markup } : {}),
  });
const answer = (env, id, text) =>
  tg(env, "answerCallbackQuery", { callback_query_id: id, ...(text ? { text } : {}) });

const kb = (rows) => ({ inline_keyboard: rows });
const btn = (text, data) => ({ text, callback_data: data });

// --------------------------------------------------------------------------
// Tiện ích số
// --------------------------------------------------------------------------
function parsePrice(text) {
  let t = text.replace(/,/g, "").replace(/\./g, "").replace(/\s/g, "").replace(/원/g, "").toLowerCase();
  if (t.includes("만")) {
    const man = t.replace("만", "");
    if (man === "") return null;
    const v = parseFloat(man);
    return isNaN(v) ? null : Math.round(v * 10000);
  }
  const v = parseFloat(t);
  if (isNaN(v)) return null;
  return v <= 1000 ? Math.round(v) * 10000 : Math.round(v);
}
function won(v) {
  const s = (v || 0).toLocaleString("en-US");
  return v >= 10000 ? `${s}원 (${Math.floor(v / 10000)}만)` : `${s}원`;
}
function parseRange(text) {
  const parts = text.split(/[^0-9만.원]+/).filter((s) => s.trim());
  const nums = parts.map(parsePrice).filter((n) => n !== null && !isNaN(n));
  if (nums.length < 2) return null;
  const lo = Math.min(nums[0], nums[1]);
  const hi = Math.max(nums[0], nums[1]);
  return [lo, hi];
}
const esc = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// --------------------------------------------------------------------------
// MENU
// --------------------------------------------------------------------------
function mainMenu() {
  return kb([
    [btn("🔍 Quét ngay", "scan"), btn("⏹ Dừng quét", "stopscan")],
    [btn("💰 Đặt khoảng giá", "price")],
    [btn("⏱ Đặt thời gian quét", "interval")],
    [btn("📊 Trạng thái", "status")],
  ]);
}
function mainText(s) {
  const lo = s.phone_min_price || 0, hi = s.phone_max_price || 0;
  return (
    "🥕 <b>Daangn Phone Hunter</b>\n\n" +
    "📱 Bot chỉ săn <b>ĐIỆN THOẠI còn dùng được</b> (không lỗi).\n\n" +
    `💰 Khoảng giá: <b>${won(lo)} → ${won(hi)}</b>\n` +
    `⏱ Tự quét mỗi: <b>${s.scan_interval_minutes}</b> phút\n` +
    "📦 Mỗi lượt tối đa <b>40</b> tin, cách nhau <b>8</b>s\n" +
    "🕒 Chỉ tin đăng trong <b>48h</b>, không gửi lại tin cũ.\n\n" +
    "Chọn một mục bên dưới:"
  );
}
function priceMenu() {
  const rows = [[btn("✏️ Nhập khoảng giá (từ – đến)", "setrange")]];
  for (const [lo, hi] of PRICE_PRESETS) rows.push([btn(`${won(lo)} → ${won(hi)}`, `pr:${lo}:${hi}`)]);
  rows.push([btn("⬅️ Về menu chính", "home")]);
  return kb(rows);
}
function priceText(s) {
  const lo = s.phone_min_price || 0, hi = s.phone_max_price || 0;
  return (
    "💰 <b>Khoảng giá điện thoại muốn săn</b>\n\n" +
    `Hiện tại: từ <b>${won(lo)}</b> đến <b>${won(hi)}</b>\n\n` +
    "Chọn nhanh hoặc bấm “Nhập khoảng giá”:"
  );
}
function intervalMenu(s) {
  const rows = INTERVALS.map((m) => [
    btn((m === s.scan_interval_minutes ? "● " : "") + `${m} phút`, `int:${m}`),
  ]);
  rows.push([btn("⬅️ Về menu chính", "home")]);
  return kb(rows);
}

// --------------------------------------------------------------------------
// Gọi GitHub Actions chạy quét ngay
// --------------------------------------------------------------------------
async function triggerScan(env, payload = { manual: true }) {
  if (!env.GH_TOKEN || !env.GH_REPO) return false;
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "daangn-bot-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ event_type: "scan", client_payload: payload }),
  });
  return r.status === 204;
}

async function triggerAutoScanIfDue(env) {
  const now = Date.now();
  // Worker cron chạy mỗi 5 phút nhưng chỉ ĐƯỢC bắn quét khi đã quá
  // scan_interval_minutes kể từ lần bắn trước — nếu không sẽ dội dispatch
  // liên tục khiến mọi run GitHub Actions bị hủy giữa chừng.
  const s = await getSettings(env);
  const intervalMs = Math.max(5, s.scan_interval_minutes || 30) * 60 * 1000;
  const lastRaw = await env.BOT_KV.get("auto_last_dispatch");
  const last = lastRaw ? parseInt(lastRaw, 10) : 0;
  if (last > 0 && now - last < intervalMs) {
    const leftMin = Math.ceil((intervalMs - (now - last)) / 60000);
    console.log(`Auto scan chưa đến kỳ (còn ~${leftMin} phút), bỏ qua.`);
    return false;
  }
  const ok = await triggerScan(env, { manual: false, source: "worker_cron" });
  if (ok) {
    await env.BOT_KV.put("auto_last_dispatch", String(now));
    console.log("Auto scan dispatched");
  } else {
    console.log("Auto scan dispatch failed");
  }
  return ok;
}

// Hủy MỌI lượt quét đang chạy trên GitHub Actions (nút "Dừng quét").
async function cancelScans(env) {
  if (!env.GH_TOKEN || !env.GH_REPO) return false;
  const headers = {
    Authorization: `Bearer ${env.GH_TOKEN}`,
    Accept: "application/vnd.github+json",
    "User-Agent": "daangn-bot-worker",
  };
  try {
    const r = await fetch(
      `https://api.github.com/repos/${env.GH_REPO}/actions/runs?status=in_progress&per_page=20`,
      { headers });
    if (!r.ok) return false;
    const data = await r.json();
    const runs = (data.workflow_runs || []);
    const r2 = await fetch(
      `https://api.github.com/repos/${env.GH_REPO}/actions/runs?status=queued&per_page=20`,
      { headers });
    if (r2.ok) {
      const d2 = await r2.json();
      runs.push(...(d2.workflow_runs || []));
    }
    let n = 0;
    for (const run of runs) {
      const c = await fetch(
        `https://api.github.com/repos/${env.GH_REPO}/actions/runs/${run.id}/cancel`,
        { method: "POST", headers });
      if (c.status === 202) n++;
    }
    return n;
  } catch (_) {
    return false;
  }
}

// --------------------------------------------------------------------------
// Xử lý callback (nút bấm)
// --------------------------------------------------------------------------
async function handleCallback(env, cb) {
  const data = cb.data || "";
  const chatId = cb.message.chat.id;
  const msgId = cb.message.message_id;
  await addSub(env, chatId);
  const s = await getSettings(env);

  if (data === "home") {
    await answer(env, cb.id);
    return edit(env, chatId, msgId, mainText(s), mainMenu());
  }

  if (data === "price") {
    await answer(env, cb.id);
    return edit(env, chatId, msgId, priceText(s), priceMenu());
  }
  if (data === "setrange") {
    await setPending(env, chatId, { action: "setrange" });
    await answer(env, cb.id);
    return send(env, chatId, "✏️ Gửi khoảng giá <b>TỪ ĐẾN</b> (won), ví dụ:\n<b>0 600000</b>  hoặc  <b>0 60만</b>");
  }
  if (data.startsWith("pr:")) {
    const [, lo, hi] = data.split(":");
    s.phone_min_price = parseInt(lo, 10);
    s.phone_max_price = parseInt(hi, 10);
    await saveSettings(env, s);
    await answer(env, cb.id, "Đã đặt khoảng giá");
    return edit(env, chatId, msgId, priceText(s), priceMenu());
  }

  if (data === "interval") {
    await answer(env, cb.id);
    return edit(env, chatId, msgId, "⏱ <b>Thời gian tự quét</b>\nChọn khoảng thời gian:", intervalMenu(s));
  }
  if (data.startsWith("int:")) {
    s.scan_interval_minutes = parseInt(data.split(":")[1], 10);
    await saveSettings(env, s);
    await answer(env, cb.id, "Đã đổi");
    return edit(env, chatId, msgId, mainText(s), mainMenu());
  }

  if (data === "status") {
    await answer(env, cb.id);
    const last = (await env.BOT_KV.get("last_scan")) || "chưa quét";
    const lastStatsRaw = await env.BOT_KV.get("last_scan_stats");
    const lastStats = lastStatsRaw ? JSON.parse(lastStatsRaw) : null;
    const statLine = lastStats
      ? `Tin mới lần trước: <b>${lastStats.found || lastStats.phone || 0}</b> điện thoại\n`
      : "";
    const txt =
      "📊 <b>Trạng thái</b>\n\n" +
      `Lần quét gần nhất: ${last}\n` +
      statLine +
      `💰 Khoảng giá: ${won(s.phone_min_price || 0)} → ${won(s.phone_max_price || 0)}\n` +
      `⏱ Tự quét mỗi: ${s.scan_interval_minutes} phút`;
    return edit(env, chatId, msgId, txt, kb([[btn("⬅️ Về menu chính", "home")]]));
  }

  if (data === "scan") {
    const ok = await triggerScan(env, { manual: true, source: "telegram_button" });
    if (ok) await env.BOT_KV.put("auto_last_dispatch", String(Date.now()));
    await answer(env, cb.id, ok ? "Đã kích hoạt quét!" : "Sẽ quét ở lần kế tiếp");
    const note = ok
      ? "🔍 Đã kích hoạt quét trên GitHub Actions — kết quả sẽ tới trong vài phút."
      : "🔍 Chưa cấu hình GH_TOKEN/GH_REPO nên không quét ngay được. Bot vẫn tự quét theo lịch.";
    return edit(env, chatId, msgId, note, kb([[btn("⏹ Dừng quét", "stopscan")], [btn("⬅️ Về menu chính", "home")]]));
  }

  if (data === "stopscan") {
    await answer(env, cb.id, "Đang dừng...");
    const n = await cancelScans(env);
    const note = (n === false)
      ? "⏹ Chưa cấu hình GH_TOKEN nên không dừng từ xa được."
      : (n > 0 ? `⏹ Đã yêu cầu dừng <b>${n}</b> lượt quét đang chạy.` : "⏹ Hiện không có lượt quét nào đang chạy.");
    return edit(env, chatId, msgId, note, kb([[btn("⬅️ Về menu chính", "home")]]));
  }

  await answer(env, cb.id);
}

// --------------------------------------------------------------------------
// Xử lý tin nhắn văn bản
// --------------------------------------------------------------------------
async function handleMessage(env, msg) {
  const chatId = msg.chat.id;
  const text = (msg.text || "").trim();
  await addSub(env, chatId);

  if (text === "/start" || text === "/menu") {
    await clearPending(env, chatId);
    const s = await getSettings(env);
    return send(env, chatId, mainText(s), mainMenu());
  }
  if (text === "/scan") {
    const ok = await triggerScan(env, { manual: true, source: "telegram_command" });
    if (ok) await env.BOT_KV.put("auto_last_dispatch", String(Date.now()));
    return send(env, chatId, ok ? "🔍 Đã kích hoạt quét!" : "🔍 Bot sẽ quét theo lịch (chưa cấu hình quét ngay).");
  }

  const state = await getPending(env, chatId);
  if (!state) {
    const s = await getSettings(env);
    return send(env, chatId, mainText(s), mainMenu());
  }

  const s = await getSettings(env);
  if (state.action === "setrange") {
    const rng = parseRange(text);
    if (!rng) return send(env, chatId, "⚠️ Chưa hiểu. Gửi 2 số TỪ ĐẾN, ví dụ: <b>0 600000</b>");
    s.phone_min_price = rng[0];
    s.phone_max_price = rng[1];
    await saveSettings(env, s);
    await clearPending(env, chatId);
    await send(env, chatId, `✅ Đã đặt khoảng giá: từ <b>${won(rng[0])}</b> đến <b>${won(rng[1])}</b>.`);
    return send(env, chatId, mainText(s), mainMenu());
  }

  // Hành động lạ -> về menu chính.
  await clearPending(env, chatId);
  return send(env, chatId, mainText(s), mainMenu());
}

// --------------------------------------------------------------------------
// Entry point
// --------------------------------------------------------------------------
export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(triggerAutoScanIfDue(env).catch((e) => console.log("Auto scan error:", e)));
  },

  async fetch(request, env) {
    const url = new URL(request.url);

    // GitHub Actions lấy cài đặt + người nhận
    if (url.pathname === "/export") {
      if (url.searchParams.get("key") !== env.EXPORT_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      const [config, subscribers] = await Promise.all([getFullConfig(env), getSubs(env)]);
      return Response.json({ config, subscribers });
    }

    // Actions báo lại thời điểm quét xong (tùy chọn)
    if (url.pathname === "/ping" && request.method === "POST") {
      if (url.searchParams.get("key") !== env.EXPORT_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      let stats = {};
      try { stats = await request.json(); } catch (_) {}
      await env.BOT_KV.put("last_scan", new Date().toISOString());
      await env.BOT_KV.put("last_scan_stats", JSON.stringify(stats || {}));
      return new Response("ok");
    }

    // Webhook Telegram
    if (request.method === "POST") {
      let update;
      try { update = await request.json(); } catch (_) { return new Response("bad", { status: 400 }); }
      try {
        if (update.callback_query) await handleCallback(env, update.callback_query);
        else if (update.message) await handleMessage(env, update.message);
      } catch (e) {
        console.log("Lỗi xử lý update:", e);
      }
      return new Response("ok");
    }

    return new Response("Daangn bot worker đang chạy.");
  },
};
