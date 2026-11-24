#!/usr/bin/env bash
# Bash Script to read .env and set all production secrets in GitHub Actions
# Requires GitHub CLI (run 'gh auth login' first)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "Error: Could not find .env file at $ENV_FILE" >&2
  exit 1
fi

echo "Reading local .env to configure GitHub Actions Secrets..."

TARGET_KEYS="AWS_HOST AWS_USER BASE_DOMAIN PLATFORM_ADMIN_HOST TENANT_ZERO_SUBDOMAIN SECRET_KEY HASHIDS_SALT EMAIL_PROVIDER MAIL_SERVER MAIL_PORT MAIL_USE_TLS MAIL_USERNAME MAIL_PASSWORD MAIL_DEFAULT_SENDER WHATSAPP_PROVIDER WHATSAPP_ACCESS_TOKEN WHATSAPP_PHONE_NUMBER_ID WHATSAPP_BUSINESS_ACCOUNT_ID WHATSAPP_WEBHOOK_VERIFY_TOKEN RAZORPAY_KEY_ID RAZORPAY_KEY_SECRET GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET ADMIN_EMAIL ADMIN_PASSWORD PLATFORM_ADMIN_EMAIL PLATFORM_ADMIN_PASSWORD TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN TWILIO_WHATSAPP_NUMBER"

while IFS='=' read -r key val || [ -n "$key" ]; do
  # Strip comments and trim whitespace
  key=$(echo "$key" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  val=$(echo "$val" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  
  if [[ -n "$key" && ! "$key" =~ ^# && -n "$val" ]]; then
    for target in $TARGET_KEYS; do
      if [ "$key" = "$target" ]; then
        echo "Setting GitHub Secret: $key"
        echo -n "$val" | gh secret set "$key"
        break
      fi
    done
  fi
done < "$ENV_FILE"

if [ -f "$HOME/.ssh/my-server-key.pem" ]; then
  echo "Setting secret: AWS_SSH_KEY from $HOME/.ssh/my-server-key.pem"
  gh secret set AWS_SSH_KEY < "$HOME/.ssh/my-server-key.pem"
fi

echo "All secrets from .env successfully synced to GitHub Repository Secrets!"
