# PowerShell Script to read .env and set all production secrets in GitHub Actions
# Requires GitHub CLI (run 'gh auth login' first)

Write-Host "Reading local .env to configure GitHub Actions Secrets..." -ForegroundColor Cyan

$envPath = Join-Path $PSScriptRoot "..\.env"
if (-not (Test-Path $envPath)) {
    Write-Error "Could not find .env file at $envPath"
    exit 1
}

# Supported secret keys to export to GitHub Actions
$targetKeys = @(
    "AWS_HOST",
    "AWS_USER",
    "BASE_DOMAIN",
    "PLATFORM_ADMIN_HOST",
    "TENANT_ZERO_SUBDOMAIN",
    "SECRET_KEY",
    "HASHIDS_SALT",
    "EMAIL_PROVIDER",
    "MAIL_SERVER",
    "MAIL_PORT",
    "MAIL_USE_TLS",
    "MAIL_USERNAME",
    "MAIL_PASSWORD",
    "MAIL_DEFAULT_SENDER",
    "WHATSAPP_PROVIDER",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_BUSINESS_ACCOUNT_ID",
    "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "ADMIN_EMAIL",
    "ADMIN_PASSWORD",
    "PLATFORM_ADMIN_EMAIL",
    "PLATFORM_ADMIN_PASSWORD",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_WHATSAPP_NUMBER"
)

Get-Content $envPath | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $parts = $line.Split("=", 2)
        $key = $parts[0].Trim()
        $val = $parts[1].Trim()
        if ($targetKeys -contains $key -and $val) {
            Write-Host "Setting GitHub Secret: $key"
            $val | gh secret set $key
        }
    }
}

# Also set SSH key if available
$sshKeyPath = "$HOME\.ssh\my-server-key.pem"
if (Test-Path $sshKeyPath) {
    Write-Host "Setting secret: AWS_SSH_KEY from $sshKeyPath"
    Get-Content $sshKeyPath -Raw | gh secret set AWS_SSH_KEY
}

Write-Host "All secrets from .env successfully synced to GitHub Repository Secrets!" -ForegroundColor Green
