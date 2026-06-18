# Daangn Phone Hunter Bot 🥕📱

Bot Telegram **luôn-bật** tự động **săn điện thoại ngon-rẻ** và **đồ điện tử cho tặng (miễn phí)** trên [당근마켓 (daangn.com)](https://www.daangn.com/kr/). Mọi tin được **dịch sang tiếng Việt có dấu** và đánh giá bằng **AI Groq**.

✨ **Điểm mới:**
- 🗣️ Tin nhắn luôn **tiếng Việt** (tên máy + đánh giá do AI Groq viết).
- 🎁 Ưu tiên quét **đồ điện tử miễn phí** (나눔 – cho tặng).
- 📱 **Menu trên Telegram** để đặt giá, thêm/bớt máy, bật/tắt đồ miễn phí, đổi tần suất quét — không cần sửa file.
- ⏱ **Tự quét định kỳ** trong nền + chống trùng tin.

## Bot gửi về cho bạn những gì?

Mỗi tin là 1 tin nhắn Telegram đầy đủ, **tiếng Việt**:

```
📱 Điện thoại iPhone 15 Pro 128GB màu trắng   ← tên (AI dịch)
💰 850,000원                                   ← giá (đồ miễn phí ghi "Miễn phí")
📍 역삼동                                        ← khu vực
🔋 Pin: 100%                                   ← % pin (nếu mô tả có)
🩺 Tình trạng: ✅ Tốt  (미사용, 잔상없음)        ← bot tự đánh giá
🤝 Thương lượng: Có thể                         ← có mặc cả được không
💬 Liên hệ: nhắn qua app 당근 (Daangn)          ← cách mua (chat trong app)
🤖 Tình trạng khá tốt, pin còn 100%...          ← AI Groq đánh giá (tiếng Việt)
👤 붕어빵전문가                                  ← người bán
🔗 https://www.daangn.com/kr/buy-sell/...       ← link bấm vào xem
🇰🇷 아이폰 15 프로 128g 화이트                   ← tên gốc tiếng Hàn (đối chiếu)
```

Đồ điện tử **miễn phí** (나눔) hiển thị `🎁 [MIỄN PHÍ]` ở đầu.

### Cơ chế đánh giá tình trạng (tự động, không cần AI)
Bot đọc **mô tả gốc tiếng Hàn** của tin và phân loại:
- **⚠️ Nghi hỏng/lỗi** → tự **bỏ qua** (고장 hỏng, 파손 vỡ, 침수 ngập nước, 부품용 bán xác, 안켜짐 không lên nguồn...).
- **✅ Tốt** → ưu tiên (S급/A급, 무잔상 không lưu ảnh, 기스없음 không trầy, 새상품 như mới, hoặc pin ≥ 90%).
- **🟡 Pin yếu / Bình thường / Có lưu ý nhỏ** → vẫn báo nhưng ghi rõ.
- Tự đọc **% pin** từ mô tả (배터리 효율 87% → `🔋 Pin: 87%`).

### Mua thế nào / thương lượng?
Trên 당근 **mọi giao dịch đều liên hệ qua chat trong app** (không mua tức thì như sàn TMĐT). Bot phát hiện người bán có cho mặc cả không:
- **Có thể** (네고가능, 가격제안...), **Không – giá cố định** (네고사절, 정찰...), hoặc **Không rõ** (cứ chat hỏi).

---

## 1. Cài đặt & chạy

```powershell
cd daangn_bot
pip install -r requirements.txt
python -m playwright install chromium

# Chạy bot luôn-bật:
python bot.py
```

Bí mật (token Telegram, key Groq) đặt trong file **`.env`** (đã tạo sẵn, không commit lên git):

```
TELEGRAM_BOT_TOKEN=...
GROQ_API_KEY=...
TELEGRAM_CHAT_ID=        # tùy chọn, cứ /start là bot tự lưu
```

### Dùng bot trên Telegram
1. Mở chat với bot → gõ **`/start`** (hoặc `/menu`).
2. Bấm các nút trong menu:
   - 🔍 **Quét ngay** – quét liền không chờ lịch.
   - 💰 **Giá & máy săn** – xem/thêm/xóa máy, đặt giá min/max.
   - 🎁 **Đồ điện tử miễn phí: BẬT/TẮT**.
   - ⏱ **Tần suất** – 10/15/30/60/120 phút.
   - ⚙️ **Cài đặt lọc** – bỏ máy hỏng / đã bán / giữ chỗ, bật/tắt AI.
   - 📊 **Trạng thái**.
3. Gõ giá kiểu `700000` hoặc `70만` đều được.

> Mọi thiết lập trong menu được lưu vào `config.json` — không cần sửa tay.

---

## 2. Cấu hình `config.json`

| Trường | Ý nghĩa |
|---|---|
| `regions` | Danh sách khu vực quét (xem mục 4). Mỗi mục `{ "id": số, "name": "tên동" }`. |
| `watch` | Dòng máy cần săn: `keyword` (tiếng Hàn), `min_price`, `max_price` (Won). |
| `skip_sold` | Bỏ tin **đã bán** (거래완료). Mặc định `true`. |
| `skip_reserved` | Bỏ tin **đang giữ chỗ** (예약중). Mặc định `true`. |
| `skip_broken` | Bỏ tin **máy hỏng/lỗi**. Mặc định `true`. |
| `broad_price_scan` | Quét thêm một lượt chỉ theo khoảng giá, giống khi lọc giá trên app, rồi bot mới lọc lại điện thoại/phụ kiện. |
| `bunjang_enabled` | Quét thêm **번개장터/Bunjang** để bắt các tin `바로구매` không xuất hiện trong API Daangn. |
| `listing_max_age_hours` | Chỉ lấy tin đăng hoặc được đẩy lên trong N giờ gần đây. Mặc định `48`. |
| `free_electronics` | Quét thêm **đồ điện tử miễn phí** (나눔). Mặc định `true`. |
| `scan_interval_minutes` | Tần suất quét tự động (phút). Mặc định `30`. |
| `use_ai` | Bật AI dịch & đánh giá (cần `GROQ_API_KEY`). |
| `ai_model` | Model Groq, mặc định `llama-3.3-70b-versatile`. |
| `ai_max_calls` | Giới hạn số lần gọi AI mỗi lần chạy. |
| `exclude_words` | Bỏ tin chứa các từ này. |

> Hầu hết thiết lập trên có thể đổi trực tiếp trong **menu Telegram**.

**Mức giá** đặt trong từng mục `watch`. Quy đổi: **1만원 = 10.000 Won**.

| Bạn muốn dưới | `max_price` |
|---|---|
| 30만원 | `300000` |
| 50만원 | `500000` |
| 70만원 | `700000` |
| 100만원 | `1000000` |

> Mẹo: đặt `min_price` (vd `200000`) để lọc tin giá rác / bán phụ kiện / gõ nhầm giá.

**Từ khóa tiếng Hàn:** iPhone = `아이폰`, iPhone 15 = `아이폰 15`, Galaxy = `갤럭시`, Galaxy S24 = `갤럭시 S24`, điện thoại chung = `핸드폰`.

---

## 3. Tạo bot Telegram (~2 phút)

1. Telegram → tìm **@BotFather** → `/newbot` → đặt tên → nhận **token**.
2. Tìm **@userinfobot** → `/start` → nhận **Chat ID**.
3. Nhắn 1 tin bất kỳ cho bot vừa tạo (để bot được phép nhắn lại bạn).

## 3b. AI dịch & đánh giá bằng Groq (miễn phí)

1. Vào https://groq.com → tạo **API key** (miễn phí).
2. Đặt vào `.env`: `GROQ_API_KEY=gsk_...`.
3. AI dịch tên máy sang tiếng Việt + viết 1-2 câu đánh giá tình trạng/độ hời. Không có key thì bot vẫn chạy, chỉ thiếu dòng dịch tiếng Việt.

> ⚠️ **Bảo mật:** bạn đã dán token Telegram & key Groq công khai khi cài đặt — nên **tạo lại key mới** sau khi xong (Groq tại groq.com, Telegram dùng `/revoke` với @BotFather) rồi cập nhật lại `.env`.

---

## 4. Quét "cả nước" & thêm khu vực

당근 là chợ **theo khu vực (hyperlocal)**: mỗi lần tìm chỉ thấy tin quanh 1 phường (동). Để phủ cả nước, bot quét **nhiều phường ở nhiều tỉnh/thành** rồi gộp & lọc trùng. `config.json` mặc định đã có 13 khu vực trải khắp Seoul, Gyeonggi, Incheon, Chungcheong, Gyeongnam, Jeonnam.

**Thêm khu vực của bạn:**
1. Vào https://www.daangn.com/kr/buy-sell/ → chọn 동네 (khu vực) của bạn.
2. Nhìn URL phần `?in=...`, ví dụ `?in=역삼동-6035` → `name = "역삼동"`, `id = 6035`.
3. Thêm vào `regions`:
   ```json
   { "id": 6035, "name": "역삼동" }
   ```

> Càng nhiều khu vực → phủ rộng hơn nhưng chạy lâu hơn. ~13 vùng × 3 từ khóa ≈ 4-6 phút/lần.

---

## 5. Cách chạy

Có **2 cách**:

### Cách A — Chạy trên máy của bạn (đơn giản, đủ tính năng)
```powershell
python bot.py
```
Menu + tự quét chạy trong 1 tiến trình. Cần máy luôn bật.

### Cách B — ⭐ 100% MIỄN PHÍ, KHÔNG cần máy tính (Cloudflare + GitHub Actions)

Chia làm 2 phần, cả hai đều **miễn phí, không cần thẻ tín dụng**:

| Phần | Chạy ở đâu | Làm gì |
|---|---|---|
| **Menu** | Cloudflare Worker (luôn bật) | Lắng nghe nút bấm, lưu cài đặt vào KV |
| **Quét** | GitHub Actions (cron 30') | Chạy Playwright, dịch, gửi Telegram |

Mọi cài đặt giá/máy bấm trên menu được lưu ở Cloudflare; GitHub Actions đọc về để quét.

#### B1. Đẩy code lên GitHub (repo **public** = phút chạy không giới hạn)
```powershell
cd f:\1
git init; git add .; git commit -m "daangn bot"
# tạo repo public trên github.com rồi:
git remote add origin https://github.com/<ban>/<repo>.git
git push -u origin main
```
> `.gitignore` đã loại `.env`, `seen.json` nên bí mật không bị lộ. **Đừng** commit file `.env`.

#### B2. Tạo secrets cho GitHub Actions
Repo → **Settings → Secrets and variables → Actions → New repository secret**:
- `TELEGRAM_BOT_TOKEN` — token bot
- `GROQ_API_KEY` — key Groq
- `WORKER_SECRET` — một chuỗi bí mật tự đặt (vd `daangn-abc123`)
- `WORKER_URL` — điền sau khi có Worker ở bước B3 (vd `https://daangn-bot.<ban>.workers.dev`)
- `TELEGRAM_CHAT_ID` — *(tùy chọn)* dự phòng nếu chưa dùng Worker

#### B3. Deploy Cloudflare Worker (menu 24/7)
Cần tài khoản Cloudflare (miễn phí) + Node.js trên máy **chỉ để deploy 1 lần**:
```powershell
cd f:\1\daangn_bot\worker
npx wrangler login
npx wrangler kv namespace create BOT_KV   # copy "id" nhận được, dán vào wrangler.toml
npx wrangler secret put BOT_TOKEN         # dán token Telegram
npx wrangler secret put EXPORT_SECRET     # dán ĐÚNG chuỗi WORKER_SECRET ở B2
npx wrangler secret put GH_TOKEN          # (tùy chọn) PAT GitHub quyền "repo" -> bật nút Quét ngay
npx wrangler secret put GROQ_API_KEY      # (tùy chọn) dịch tên máy Việt -> Hàn
# sửa GH_REPO trong wrangler.toml thành "<ban>/<repo>"
npx wrangler deploy
```
Lệnh `deploy` in ra URL dạng `https://daangn-bot.<ban>.workers.dev` → **dán vào secret `WORKER_URL`** (B2).

#### B4. Nối Telegram vào Worker (webhook)
```powershell
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://daangn-bot.<ban>.workers.dev"
```
Xong! Mở Telegram → `/start` để dùng menu. GitHub Actions tự quét mỗi 30 phút; nút **🔍 Quét ngay** kích hoạt quét tức thì (nếu đã đặt `GH_TOKEN` + `GH_REPO`).

> **Lưu ý phút miễn phí:** repo **public** = không giới hạn. Repo private chỉ có 2.000 phút/tháng (không đủ cho cron 30'), nên hãy để public (bí mật vẫn an toàn trong Secrets).

### Cách C — VPS miễn phí (Oracle Cloud Always Free)
Nếu muốn giữ nguyên `bot.py` 1 tiến trình: tạo VM Ubuntu miễn phí trên Oracle Cloud (cần thẻ để đăng ký, **không bị trừ tiền**), cài Python + `playwright install chromium`, chạy bằng `systemd`.

---

## Lưu ý
- Bot chỉ **đọc dữ liệu công khai**, mô phỏng trình duyệt thật, không phá vỡ bảo mật của trang.
- Đánh giá tình trạng/AI chỉ **tham khảo** — luôn xem ảnh & chat hỏi kỹ người bán trước khi mua.
