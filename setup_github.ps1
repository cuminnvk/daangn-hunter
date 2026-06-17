# ============================================================================
# setup_github.ps1 — Hoàn tất phần GitHub Actions (chạy MỘT lần).
#
# Việc duy nhất bạn cần làm: chạy file này. Khi nó mở trình duyệt yêu cầu
# đăng nhập GitHub thì bạn đăng nhập nick của mình, xong nó tự làm hết:
#   - Tạo repo PUBLIC + đẩy code lên.
#   - Đặt secrets cho GitHub Actions (đọc từ daangn_bot/.env).
#   - Bật nút "Quét ngay" (đặt GH_TOKEN + GH_REPO cho Cloudflare Worker).
#   - Chạy thử 1 lượt quét.
#
# Cách chạy (trong PowerShell, tại thư mục f:\1):
#   powershell -ExecutionPolicy Bypass -File .\setup_github.ps1            # tên repo mặc định: daangn-hunter
#   powershell -ExecutionPolicy Bypass -File .\setup_github.ps1 ten-repo   # tên repo tùy chọn
# ============================================================================
param([string]$Repo = "daangn-hunter")

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> Đọc bí mật từ daangn_bot\.env" -ForegroundColor Cyan
$envMap = @{}
Get-Content .\daangn_bot\.env | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') { $envMap[$matches[1].Trim()] = $matches[2].Trim() }
}

Write-Host "==> Kiểm tra đăng nhập GitHub (trình duyệt sẽ mở nếu chưa)" -ForegroundColor Cyan
gh auth status 2>$null
if ($LASTEXITCODE -ne 0) {
    gh auth login --web --git-protocol https --scopes "repo,workflow"
}
$me = (gh api user --jq .login).Trim()
Write-Host "    Đăng nhập với: $me" -ForegroundColor Green

Write-Host "==> Tạo/đẩy repo PUBLIC: $me/$Repo" -ForegroundColor Cyan
gh repo view "$me/$Repo" 2>$null
if ($LASTEXITCODE -ne 0) {
    gh repo create $Repo --public --source=. --remote=origin --push
} else {
    git remote remove origin 2>$null
    git remote add origin "https://github.com/$me/$Repo.git"
    git push -u origin main
}

Write-Host "==> Đặt secrets cho GitHub Actions" -ForegroundColor Cyan
foreach ($k in @("TELEGRAM_BOT_TOKEN", "GROQ_API_KEY", "WORKER_URL", "WORKER_SECRET", "TELEGRAM_CHAT_ID")) {
    if ($envMap[$k]) {
        gh secret set $k --body $envMap[$k] -R "$me/$Repo"
        Write-Host "    đã đặt $k" -ForegroundColor Green
    }
}

Write-Host "==> Bật nút 'Quét ngay' (GH_TOKEN + GH_REPO cho Worker)" -ForegroundColor Cyan
$ghtoken = (gh auth token).Trim()
Push-Location .\daangn_bot\worker
(Get-Content wrangler.toml) -replace 'GH_REPO = ".*"', "GH_REPO = `"$me/$Repo`"" | Set-Content wrangler.toml
$ghtoken | npx --yes wrangler secret put GH_TOKEN
npx --yes wrangler deploy
Pop-Location

Write-Host "==> Chạy thử 1 lượt quét trên GitHub Actions" -ForegroundColor Cyan
gh workflow run hunt.yml -R "$me/$Repo"

Write-Host ""
Write-Host "🎉 XONG! Mọi thứ đã chạy 100% trên cloud, không cần máy bạn bật." -ForegroundColor Green
Write-Host "   Repo:    https://github.com/$me/$Repo"
Write-Host "   Actions: https://github.com/$me/$Repo/actions"
Write-Host "   Bot:     mở Telegram, gõ /start để dùng menu."
