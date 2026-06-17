/**
 * Daangn Phone Hunter — Cloudflare Worker (menu Telegram 24/7).
 *
 * Vai trò:
 *  - Nhận webhook Telegram, hiển thị MENU tương tác (đặt giá, thêm/bớt máy,
 *    bật/tắt đồ miễn phí, đổi tần suất, cài đặt lọc...).
 *  - Lưu cài đặt + danh sách người nhận vào KV (binding BOT_KV).
 *  - Nút "Quét ngay" gọi GitHub Actions (repository_dispatch) chạy Playwright.
 *  - Cung cấp GET /export?key=SECRET cho GitHub Actions lấy cài đặt.
 *
 * Biến môi trường (wrangler.toml [vars] / secret):
 *  BOT_TOKEN       (bắt buộc) token Telegram
 *  EXPORT_SECRET   (bắt buộc) chuỗi bí mật, khớp WORKER_SECRET bên Actions
 *  GH_TOKEN        (tùy chọn) PAT GitHub có quyền "repo" để bấm Quét ngay
 *  GH_REPO         (tùy chọn) "user/ten-repo"
 *  GROQ_API_KEY    (tùy chọn) để dịch tên máy Việt -> Hàn khi thêm máy
 * KV binding: BOT_KV
 */

const DEFAULT_CONFIG = {
  regions: [
    { id: 6035, name: "역삼동" },
    { id: 355, name: "신림동" },
    { id: 6052, name: "마곡동" },
    { id: 6543, name: "송도동" },
    { id: 1766, name: "봉담읍" },
    { id: 1604, name: "별내동" },
    { id: 4245, name: "배곧동" },
    { id: 2292, name: "불당동" },
    { id: 3662, name: "물금읍" },
    { id: 2899, name: "고흥읍" },
  ],
  watch: [
    { keyword: "아이폰 15", min_price: 200000, max_price: 750000 },
    { keyword: "아이폰 14", min_price: 150000, max_price: 550000 },
    { keyword: "갤럭시 S24", min_price: 200000, max_price: 650000 },
  ],
  // Săn MỌI loại máy trong khoảng giá này (không cần thêm từng máy).
  phone_min_price: 20000,
  phone_max_price: 60000,
  phone_keywords: ["아이폰", "갤럭시", "휴대폰", "스마트폰"],
  strict_good: true,
  min_battery_percent: 80,
  phones_only: true,
  free_limit: 20,
  phone_limit: 20,
  send_delay_seconds: 10,
  digest_mode: false,
  quiet_hours_enabled: false,
  quiet_start_hour: 23,
  quiet_end_hour: 7,
  seen_ttl_hours: 48,
  region_filter_enabled: false,
  region_filter_terms: [],
  free_electronics: true,
  free_first: true,
  scan_interval_minutes: 30,
  skip_sold: true,
  skip_reserved: true,
  skip_broken: true,
  use_ai: true,
  ai_model: "llama-3.3-70b-versatile",
  ai_max_calls: 30,
  exclude_words: ["부품", "수리용", "잠금", "아이클라우드"],
};

const PRESETS = [
  ["iPhone 16", "아이폰 16"], ["iPhone 15", "아이폰 15"], ["iPhone 14", "아이폰 14"],
  ["iPhone 13", "아이폰 13"], ["iPhone 12", "아이폰 12"], ["iPhone SE", "아이폰 SE"],
  ["Galaxy S24", "갤럭시 S24"], ["Galaxy S23", "갤럭시 S23"], ["Galaxy Z Flip", "갤럭시 Z 플립"],
  ["Galaxy Z Fold", "갤럭시 Z 폴드"], ["Galaxy Note", "갤럭시 노트"],
  ["iPad", "아이패드"], ["Galaxy Tab", "갤럭시탭"], ["MacBook", "맥북"],
  ["AirPods", "에어팟"], ["Apple Watch", "애플워치"],
];
const INTERVALS = [10, 15, 30, 60, 120];
// Khoảng giá gợi ý nhanh (won): [từ, đến]
const PRICE_PRESETS = [[0, 30000], [20000, 60000], [50000, 100000], [100000, 200000], [0, 300000]];

// --------------------------------------------------------------------------
// KV helpers
// --------------------------------------------------------------------------
async function getConfig(env) {
  const raw = await env.BOT_KV.get("config");
  let cfg = raw ? JSON.parse(raw) : {};
  for (const k of Object.keys(DEFAULT_CONFIG)) {
    if (cfg[k] === undefined) cfg[k] = DEFAULT_CONFIG[k];
  }
  return cfg;
}
async function saveConfig(env, cfg) {
  await env.BOT_KV.put("config", JSON.stringify(cfg));
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
  const s = v.toLocaleString("en-US");
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
function mainMenu(cfg) {
  const free = cfg.free_electronics ? "BẬT ✅" : "TẮT ⬜";
  const lo = cfg.phone_min_price || 0, hi = cfg.phone_max_price || 0;
  return kb([
    [btn("🔍 Quét ngay", "scan"), btn("⏹ Dừng quét", "stopscan")],
    [btn(`💰 Giá máy: ${won(lo)} → ${won(hi)}`, "price")],
    [btn(`🎁 Đồ điện tử miễn phí: ${free}`, "togglefree")],
    [btn(`⏱ Tần suất: ${cfg.scan_interval_minutes} phút`, "interval")],
    [btn("⚙️ Cài đặt lọc", "settings")],
    [btn("📊 Trạng thái", "status")],
  ]);
}
function mainText(cfg) {
  const lo = cfg.phone_min_price || 0, hi = cfg.phone_max_price || 0;
  return (
    "🥕 <b>Daangn Phone Hunter</b>\n\n" +
    `📱 Săn MỌI máy giá: <b>${won(lo)} → ${won(hi)}</b>\n` +
    `🎁 Ưu tiên đồ miễn phí: <b>${cfg.free_electronics ? "bật" : "tắt"}</b>\n` +
    `🌍 Khu vực: <b>${cfg.regions.length}</b>\n` +
    `⏱ Quét mỗi <b>${cfg.scan_interval_minutes}</b> phút\n\n` +
    "Chỉ quét máy còn tốt (loại chập nguồn / ố màn / bể nát).\n" +
    "Chọn một mục bên dưới:"
  );
}
function priceMenu(cfg) {
  const rows = [[btn("✏️ Nhập khoảng giá (từ – đến)", "setrange")]];
  for (const [lo, hi] of PRICE_PRESETS) rows.push([btn(`${won(lo)} → ${won(hi)}`, `pr:${lo}:${hi}`)]);
  rows.push([btn("⬅️ Về menu chính", "home")]);
  return kb(rows);
}
function priceText(cfg) {
  const lo = cfg.phone_min_price || 0, hi = cfg.phone_max_price || 0;
  return (
    "💰 <b>Giá máy muốn săn</b>\n\n" +
    `Hiện tại: từ <b>${won(lo)}</b> đến <b>${won(hi)}</b>\n\n` +
    "Bot sẽ tìm MỌI loại máy trong khoảng giá này — không cần thêm từng máy.\n" +
    "Chọn nhanh hoặc bấm “Nhập khoảng giá”:"
  );
}
function watchMenu(cfg) {
  const rows = cfg.watch.map((w, i) => {
    const label = w.max_price ? `${w.keyword}  ≤ ${Math.floor(w.max_price / 10000)}만` : w.keyword;
    return [btn(`📱 ${label}`, `w:${i}`)];
  });
  rows.push([btn("➕ Thêm máy", "addmenu")]);
  rows.push([btn("⬅️ Về menu chính", "home")]);
  return kb(rows);
}
function watchDetail(idx) {
  return kb([
    [btn("💵 Đặt giá tối đa", `setmax:${idx}`)],
    [btn("💵 Đặt giá tối thiểu", `setmin:${idx}`)],
    [btn("🗑 Xóa máy này", `del:${idx}`)],
    [btn("⬅️ Quay lại", "watch")],
  ]);
}
function addMenu() {
  const rows = [];
  let row = [];
  PRESETS.forEach(([label], i) => {
    row.push(btn(label, `add:${i}`));
    if (row.length === 2) { rows.push(row); row = []; }
  });
  if (row.length) rows.push(row);
  rows.push([btn("⌨️ Gõ tên khác (Hàn/Việt)", "addcustom")]);
  rows.push([btn("⬅️ Quay lại", "watch")]);
  return kb(rows);
}
function intervalMenu(cfg) {
  const rows = INTERVALS.map((m) => [
    btn((m === cfg.scan_interval_minutes ? "● " : "") + `${m} phút`, `int:${m}`),
  ]);
  rows.push([btn("⬅️ Về menu chính", "home")]);
  return kb(rows);
}
function settingsMenu(cfg) {
  const m = (v) => (v ? "✅" : "⬜");
  const fl = cfg.free_limit ?? 20, pl = cfg.phone_limit ?? 20;
  const sd = cfg.send_delay_seconds ?? 10;
  const mb = cfg.min_battery_percent ?? 80;
  const qs = cfg.quiet_start_hour ?? 23;
  const qe = cfg.quiet_end_hour ?? 7;
  const ttl = cfg.seen_ttl_hours ?? 48;
  const rt = (cfg.region_filter_terms || []).slice(0, 2).join(", ") || "chưa đặt";
  return kb([
    [btn(`${m(cfg.phones_only !== false)} Chỉ điện thoại (loại vỏ/ốp)`, "t:phones_only")],
    [btn(`${m(cfg.strict_good !== false)} Chỉ máy còn tốt (nghiêm ngặt)`, "t:strict_good")],
    [btn(`${m(cfg.skip_broken)} Bỏ máy hỏng/lỗi`, "t:skip_broken")],
    [btn(`${m(cfg.skip_sold)} Bỏ tin đã bán`, "t:skip_sold")],
    [btn(`${m(cfg.skip_reserved)} Bỏ tin đang giữ chỗ`, "t:skip_reserved")],
    [btn(`${m(cfg.use_ai)} AI dịch & phân tích (Groq)`, "t:use_ai")],
    [btn(`${m(cfg.digest_mode)} Chế độ gửi gộp (digest)`, "t:digest_mode")],
    [btn(`${m(cfg.quiet_hours_enabled)} Giờ yên lặng (${qs}:00-${qe}:00)`, "t:quiet_hours_enabled")],
    [btn("🌙 Đặt giờ yên lặng", "setquiet")],
    [btn(`${m(cfg.region_filter_enabled)} Lọc theo vùng`, "t:region_filter_enabled")],
    [btn(`📍 Vùng ưu tiên: ${rt}`, "setregion")],
    [btn(`🕒 Không lặp tin: ${ttl}h`, "setttl")],
    [btn(`🔋 Pin tối thiểu: ${mb}%`, "setbattery")],
    [btn(`🔢 Giới hạn: ${fl} free / ${pl} máy / lượt`, "setlimit")],
    [btn(`⏳ Giãn gửi: ${sd}s / tin`, "setdelay")],
    [btn("⬅️ Về menu chính", "home")],
  ]);
}

// --------------------------------------------------------------------------
// Dịch tên máy Việt -> từ khóa Hàn (Groq, tùy chọn)
// --------------------------------------------------------------------------
async function viToKorean(env, text) {
  const hasKorean = /[\uac00-\ud7a3]/.test(text);
  if (hasKorean || !env.GROQ_API_KEY) return text;
  try {
    const r = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: { Authorization: `Bearer ${env.GROQ_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "llama-3.3-70b-versatile",
        messages: [
          { role: "system", content: "Trả về DUY NHẤT từ khóa tìm kiếm tiếng Hàn cho tên thiết bị, không giải thích." },
          { role: "user", content: `Tên thiết bị: ${text}` },
        ],
        temperature: 0, max_tokens: 30,
      }),
    });
    if (r.ok) {
      const d = await r.json();
      return (d.choices[0].message.content || text).trim().replace(/^"|"$/g, "");
    }
  } catch (_) {}
  return text;
}

// --------------------------------------------------------------------------
// Gọi GitHub Actions chạy quét ngay
// --------------------------------------------------------------------------
async function triggerScan(env) {
  if (!env.GH_TOKEN || !env.GH_REPO) return false;
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "daangn-bot-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ event_type: "scan" }),
  });
  return r.status === 204;
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
    // Hủy cả lượt đang queued.
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
  const cfg = await getConfig(env);

  if (data === "home") { await answer(env, cb.id); return edit(env, chatId, msgId, mainText(cfg), mainMenu(cfg)); }
  if (data === "price" || data === "watch") { await answer(env, cb.id); return edit(env, chatId, msgId, priceText(cfg), priceMenu(cfg)); }
  if (data === "setrange") {
    await setPending(env, chatId, { action: "setrange" });
    await answer(env, cb.id);
    return send(env, chatId, "✏️ Gửi khoảng giá <b>TẪ ĐẺN</b> (won), ví dụ:\n<b>20000 60000</b>  hoặc  <b>2만 6만</b>");
  }
  if (data.startsWith("pr:")) {
    const [, lo, hi] = data.split(":");
    cfg.phone_min_price = parseInt(lo, 10);
    cfg.phone_max_price = parseInt(hi, 10);
    await saveConfig(env, cfg);
    await answer(env, cb.id, "Đã đặt khoảng giá");
    return edit(env, chatId, msgId, priceText(cfg), priceMenu(cfg));
  }
  if (data === "addmenu") { await answer(env, cb.id); return edit(env, chatId, msgId, "➕ <b>Thêm máy cần săn</b>\nChọn mẫu hoặc gõ tên:", addMenu()); }
  if (data === "settings") { await answer(env, cb.id); return edit(env, chatId, msgId, "⚙️ <b>Cài đặt lọc</b>\n\nBấm để bật/tắt:", settingsMenu(cfg)); }
  if (data === "setlimit") {
    await setPending(env, chatId, { action: "setlimit" });
    await answer(env, cb.id);
    return send(env, chatId, "🔢 Gửi giới hạn <b>FREE MÁY</b> mỗi lượt (2 số), ví dụ:\n<b>20 20</b>  (20 đồ free + 20 điện thoại)");
  }
  if (data === "setdelay") {
    await setPending(env, chatId, { action: "setdelay" });
    await answer(env, cb.id);
    return send(env, chatId, "⏳ Gửi số giây giãn cách mỗi tin, ví dụ: <b>10</b>");
  }
  if (data === "setbattery") {
    await setPending(env, chatId, { action: "setbattery" });
    await answer(env, cb.id);
    return send(env, chatId, "🔋 Gửi ngưỡng pin tối thiểu (%), ví dụ: <b>80</b>");
  }
  if (data === "setquiet") {
    await setPending(env, chatId, { action: "setquiet" });
    await answer(env, cb.id);
    return send(env, chatId, "🌙 Gửi giờ yên lặng <b>BẮT ĐẦU KẾT THÚC</b> (0-23), ví dụ: <b>23 7</b>");
  }
  if (data === "setregion") {
    await setPending(env, chatId, { action: "setregion" });
    await answer(env, cb.id);
    return send(env, chatId, "📍 Gửi danh sách vùng ưu tiên, cách nhau dấu phẩy. Ví dụ: <b>역삼동, 송도동</b>");
  }
  if (data === "setttl") {
    await setPending(env, chatId, { action: "setttl" });
    await answer(env, cb.id);
    return send(env, chatId, "🕒 Gửi số giờ không lặp tin, ví dụ: <b>48</b>");
  }
  if (data === "interval") { await answer(env, cb.id); return edit(env, chatId, msgId, "⏱ <b>Tần suất quét</b>\nChọn khoảng thời gian:", intervalMenu(cfg)); }

  if (data === "status") {
    await answer(env, cb.id);
    const last = (await env.BOT_KV.get("last_scan")) || "chưa quét";
    const txt =
      "📊 <b>Trạng thái</b>\n\n" +
      `Lần quét gần nhất: ${last}\n` +
      `AI: ${cfg.use_ai ? "bật" : "tắt"}\n` +
      `Đồ miễn phí: ${cfg.free_electronics ? "bật" : "tắt"}\n` +
      `Tần suất: ${cfg.scan_interval_minutes} phút`;
    return edit(env, chatId, msgId, txt, kb([[btn("⬅️ Về menu chính", "home")]]));
  }

  if (data === "togglefree") {
    cfg.free_electronics = !cfg.free_electronics;
    await saveConfig(env, cfg);
    await answer(env, cb.id, "Đã cập nhật");
    return edit(env, chatId, msgId, mainText(cfg), mainMenu(cfg));
  }

  if (data === "scan") {
    const ok = await triggerScan(env);
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

  if (data.startsWith("int:")) {
    cfg.scan_interval_minutes = parseInt(data.split(":")[1], 10);
    await saveConfig(env, cfg);
    await answer(env, cb.id, "Đã đổi");
    return edit(env, chatId, msgId, mainText(cfg), mainMenu(cfg));
  }

  if (data.startsWith("t:")) {
    const f = data.split(":")[1];
    cfg[f] = !cfg[f];
    await saveConfig(env, cfg);
    await answer(env, cb.id, "Đã đổi");
    return edit(env, chatId, msgId, "⚙️ <b>Cài đặt lọc</b>\n\nBấm để bật/tắt:", settingsMenu(cfg));
  }

  if (data.startsWith("w:")) {
    const idx = parseInt(data.split(":")[1], 10);
    const w = cfg.watch[idx];
    await answer(env, cb.id);
    if (!w) return edit(env, chatId, msgId, "💰 <b>Máy đang săn</b>", watchMenu(cfg));
    const txt =
      `📱 <b>${esc(w.keyword)}</b>\n\n` +
      `Giá tối thiểu: ${w.min_price ? won(w.min_price) : "không đặt"}\n` +
      `Giá tối đa: ${w.max_price ? won(w.max_price) : "không đặt"}`;
    return edit(env, chatId, msgId, txt, watchDetail(idx));
  }

  if (data.startsWith("del:")) {
    const idx = parseInt(data.split(":")[1], 10);
    const removed = cfg.watch.splice(idx, 1)[0];
    await saveConfig(env, cfg);
    await answer(env, cb.id, removed ? `Đã xóa ${removed.keyword}` : "");
    return edit(env, chatId, msgId, "💰 <b>Máy đang săn</b>\n\nBấm vào một máy để đặt giá hoặc xóa:", watchMenu(cfg));
  }

  if (data.startsWith("setmax:") || data.startsWith("setmin:")) {
    const idx = parseInt(data.split(":")[1], 10);
    const kind = data.startsWith("setmax") ? "max" : "min";
    await setPending(env, chatId, { action: "set" + kind, idx });
    await answer(env, cb.id);
    return send(env, chatId, `💵 Gửi mức giá ${kind} (ví dụ: <b>700000</b> hoặc <b>70만</b>):`);
  }

  if (data.startsWith("add:")) {
    const i = parseInt(data.split(":")[1], 10);
    const kwd = PRESETS[i][1];
    if (cfg.watch.some((w) => w.keyword === kwd)) {
      await answer(env, cb.id, "Đã có rồi");
    } else {
      cfg.watch.push({ keyword: kwd, min_price: 0, max_price: 700000 });
      await saveConfig(env, cfg);
      await answer(env, cb.id, `Đã thêm ${kwd}`);
    }
    return edit(env, chatId, msgId, "💰 <b>Máy đang săn</b>\n\nBấm vào một máy để đặt giá hoặc xóa:", watchMenu(cfg));
  }

  if (data === "addcustom") {
    await setPending(env, chatId, { action: "addkw" });
    await answer(env, cb.id);
    return send(env, chatId, "⌨️ Gõ tên máy (tiếng Hàn tốt nhất, vd <b>아이폰 13 미니</b>). Gõ tiếng Việt thì AI sẽ tự chuyển.");
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
    const cfg = await getConfig(env);
    return send(env, chatId, mainText(cfg), mainMenu(cfg));
  }
  if (text === "/scan") {
    const ok = await triggerScan(env);
    return send(env, chatId, ok ? "🔍 Đã kích hoạt quét!" : "🔍 Bot sẽ quét theo lịch (chưa cấu hình quét ngay).");
  }

  const state = await getPending(env, chatId);
  if (!state) {
    const cfg = await getConfig(env);
    return send(env, chatId, mainText(cfg), mainMenu(cfg));
  }

  const cfg = await getConfig(env);
  if (state.action === "setrange") {
    const rng = parseRange(text);
    if (!rng) return send(env, chatId, "⚠️ Chưa hiểu. Gửi 2 số TẪ ĐẺN, ví dụ: <b>20000 60000</b>");
    cfg.phone_min_price = rng[0];
    cfg.phone_max_price = rng[1];
    await saveConfig(env, cfg);
    await clearPending(env, chatId);
    await send(env, chatId, `✅ Đã đặt khoảng giá: từ <b>${won(rng[0])}</b> đến <b>${won(rng[1])}</b>.`);
    return send(env, chatId, mainText(cfg), mainMenu(cfg));
  }
  if (state.action === "setlimit") {
    const nums = (text.match(/\d+/g) || []).map((n) => parseInt(n, 10));
    if (nums.length < 2) return send(env, chatId, "⚠️ Gửi 2 số: FREE rồi MÁY, ví dụ <b>20 20</b>");
    cfg.free_limit = Math.max(0, nums[0]);
    cfg.phone_limit = Math.max(0, nums[1]);
    await saveConfig(env, cfg);
    await clearPending(env, chatId);
    await send(env, chatId, `✅ Mỗi lượt quét tối đa: <b>${nums[0]}</b> đồ free + <b>${nums[1]}</b> điện thoại.`);
    return send(env, chatId, mainText(cfg), mainMenu(cfg));
  }
  if (state.action === "setdelay") {
    const nums = (text.match(/\d+/g) || []).map((n) => parseInt(n, 10));
    if (nums.length < 1) return send(env, chatId, "⚠️ Gửi số giây hợp lệ, ví dụ <b>10</b>");
    cfg.send_delay_seconds = Math.max(0, Math.min(30, nums[0]));
    await saveConfig(env, cfg);
    await clearPending(env, chatId);
    await send(env, chatId, `✅ Đã đặt giãn gửi: <b>${cfg.send_delay_seconds}</b> giây/tin.`);
    return send(env, chatId, mainText(cfg), mainMenu(cfg));
  }
  if (state.action === "setbattery") {
    const nums = (text.match(/\d+/g) || []).map((n) => parseInt(n, 10));
    if (nums.length < 1) return send(env, chatId, "⚠️ Gửi % pin hợp lệ, ví dụ <b>80</b>");
    cfg.min_battery_percent = Math.max(50, Math.min(100, nums[0]));
    await saveConfig(env, cfg);
    await clearPending(env, chatId);
    await send(env, chatId, `✅ Đã đặt pin tối thiểu: <b>${cfg.min_battery_percent}%</b>.`);
    return send(env, chatId, mainText(cfg), mainMenu(cfg));
  }
  if (state.action === "setquiet") {
    const nums = (text.match(/\d+/g) || []).map((n) => parseInt(n, 10));
    if (nums.length < 2) return send(env, chatId, "⚠️ Gửi 2 số giờ (0-23), ví dụ <b>23 7</b>");
    cfg.quiet_start_hour = Math.max(0, Math.min(23, nums[0]));
    cfg.quiet_end_hour = Math.max(0, Math.min(23, nums[1]));
    await saveConfig(env, cfg);
    await clearPending(env, chatId);
    await send(env, chatId, `✅ Đã đặt giờ yên lặng: <b>${cfg.quiet_start_hour}:00 → ${cfg.quiet_end_hour}:00</b>.`);
    return send(env, chatId, mainText(cfg), mainMenu(cfg));
  }
  if (state.action === "setregion") {
    cfg.region_filter_terms = text.split(",").map((s) => s.trim()).filter(Boolean).slice(0, 10);
    await saveConfig(env, cfg);
    await clearPending(env, chatId);
    if (cfg.region_filter_terms.length) {
      await send(env, chatId, `✅ Đã đặt vùng ưu tiên: <b>${esc(cfg.region_filter_terms.slice(0, 5).join(", "))}</b>`);
    } else {
      await send(env, chatId, "✅ Đã xóa danh sách vùng ưu tiên.");
    }
    return send(env, chatId, mainText(cfg), mainMenu(cfg));
  }
  if (state.action === "setttl") {
    const nums = (text.match(/\d+/g) || []).map((n) => parseInt(n, 10));
    if (nums.length < 1) return send(env, chatId, "⚠️ Gửi số giờ hợp lệ, ví dụ <b>48</b>");
    cfg.seen_ttl_hours = Math.max(1, Math.min(168, nums[0]));
    await saveConfig(env, cfg);
    await clearPending(env, chatId);
    await send(env, chatId, `✅ Đã đặt không lặp tin trong <b>${cfg.seen_ttl_hours}</b> giờ.`);
    return send(env, chatId, mainText(cfg), mainMenu(cfg));
  }
  if (state.action === "setmax" || state.action === "setmin") {
    const price = parsePrice(text);
    if (price === null) return send(env, chatId, "⚠️ Không hiểu giá. Gửi lại số (vd 700000 hoặc 70만):");
    const w = cfg.watch[state.idx];
    if (w) {
      if (state.action === "setmax") w.max_price = price;
      else w.min_price = price;
      await saveConfig(env, cfg);
    }
    await clearPending(env, chatId);
    await send(env, chatId, `✅ Đã đặt giá ${won(price)}.`);
    return send(env, chatId, mainText(cfg), mainMenu(cfg));
  }

  if (state.action === "addkw") {
    const kwd = await viToKorean(env, text);
    if (cfg.watch.some((w) => w.keyword === kwd)) {
      await send(env, chatId, "Máy này đã có trong danh sách.");
    } else {
      cfg.watch.push({ keyword: kwd, min_price: 0, max_price: 700000 });
      await saveConfig(env, cfg);
      await send(env, chatId, `✅ Đã thêm: <b>${esc(kwd)}</b> (giá tối đa 70만, sửa trong menu).`);
    }
    await clearPending(env, chatId);
    return send(env, chatId, mainText(cfg), mainMenu(cfg));
  }
}

// --------------------------------------------------------------------------
// Entry point
// --------------------------------------------------------------------------
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // GitHub Actions lấy cài đặt + người nhận
    if (url.pathname === "/export") {
      if (url.searchParams.get("key") !== env.EXPORT_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      const [config, subscribers] = await Promise.all([getConfig(env), getSubs(env)]);
      return Response.json({ config, subscribers });
    }

    // Actions báo lại thời điểm quét xong (tùy chọn)
    if (url.pathname === "/ping" && request.method === "POST") {
      if (url.searchParams.get("key") !== env.EXPORT_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      await env.BOT_KV.put("last_scan", new Date().toISOString());
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
