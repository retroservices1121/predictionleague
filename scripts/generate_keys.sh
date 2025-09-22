#!/bin/bash
# Generate RSA keys for Kalshi API

echo "🔐 Generating RSA keys for Kalshi API..."

# Generate private key
openssl genrsa -out kalshi_private_key.pem 2048
echo "✓ Private key generated: kalshi_private_key.pem"

# Generate public key
openssl rsa -in kalshi_private_key.pem -pubout -out kalshi_public_key.pem
echo "✓ Public key generated: kalshi_public_key.pem"

# Convert to base64 for Railway
base64 -i kalshi_private_key.pem | tr -d '\n' > private_key_base64.txt
echo "✓ Base64 encoded key saved: private_key_base64.txt"

echo ""
echo "📤 Next steps:"
echo "1. Upload kalshi_public_key.pem to Kalshi dashboard"
echo "2. Copy content of private_key_base64.txt to KALSHI_PRIVATE_KEY env var"
echo "3. Keep kalshi_private_key.pem secure (never commit to git!)"
