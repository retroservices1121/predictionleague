#!/bin/bash
# Automated setup script for Railway + Supabase deployment

set -e  # Exit on any error

echo "🚀 Prediction League Bot - Railway + Supabase Setup"
echo "=================================================="

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }
print_info() { echo -e "${BLUE}ℹ${NC} $1"; }

# Check prerequisites
echo -e "\n${BLUE}Checking prerequisites...${NC}"

if ! command -v openssl &> /dev/null; then
    print_error "OpenSSL is required but not installed."
    exit 1
fi

if ! command -v git &> /dev/null; then
    print_error "Git is required but not installed."
    exit 1
fi

print_status "Prerequisites check passed"

# Generate RSA keys
echo -e "\n${BLUE}Generating RSA keys for Kalshi API...${NC}"

if [ ! -f "kalshi_private_key.pem" ]; then
    openssl genrsa -out kalshi_private_key.pem 2048
    print_status "Private key generated: kalshi_private_key.pem"
else
    print_warning "Private key already exists, skipping"
fi

if [ ! -f "kalshi_public_key.pem" ]; then
    openssl rsa -in kalshi_private_key.pem -pubout -out kalshi_public_key.pem
    print_status "Public key generated: kalshi_public_key.pem"
else
    print_warning "Public key already exists, skipping"
fi

# Convert to base64
base64 -i kalshi_private_key.pem | tr -d '\n' > private_key_base64.txt
print_status "Base64 key saved: private_key_base64.txt"

# Create environment template
echo -e "\n${BLUE}Creating environment template...${NC}"

cat > .env.railway.template << EOF
# Copy these to Railway Environment Variables

TELEGRAM_TOKEN=YOUR_BOT_TOKEN_FROM_BOTFATHER
KALSHI_EMAIL=your-kalshi-email@example.com
KALSHI_PASSWORD=your-kalshi-password
KALSHI_PRIVATE_KEY=$(cat private_key_base64.txt)
DATABASE_URL=postgresql://postgres:password@db.project.supabase.co:5432/postgres
RAILWAY_ENVIRONMENT=production
PORT=8080
EOF

print_status "Environment template: .env.railway.template"

# Create deployment checklist
cat > DEPLOYMENT_CHECKLIST.md << 'EOF'
# 🚀 Deployment Checklist

## Prerequisites ✅
- [ ] Telegram bot created with @BotFather
- [ ] Kalshi account with API access
- [ ] Supabase project created
- [ ] Railway account ready

## Database Setup 📊  
- [ ] Run `database/supabase_setup.sql` in Supabase SQL Editor
- [ ] Copy DATABASE_URL from Supabase Settings
- [ ] Verify tables created

## API Configuration 🔐
- [ ] Upload `kalshi_public_key.pem` to Kalshi dashboard
- [ ] Copy private key from `private_key_base64.txt`
- [ ] Test all credentials

## Railway Deployment 🚂
- [ ] Push to GitHub
- [ ] Connect Railway to repository
- [ ] Add environment variables from `.env.railway.template`
- [ ] Verify successful deployment

## Testing 🧪
- [ ] Bot responds to `/start`
- [ ] `/markets` loads markets
- [ ] Leaderboard works
- [ ] Database connections stable

Ready to launch! 🎉
EOF

print_status "Checklist created: DEPLOYMENT_CHECKLIST.md"

# Final instructions
echo -e "\n${GREEN}🎉 Setup Complete!${NC}"
echo -e "\n${BLUE}Next Steps:${NC}"
echo "1. 📤 Upload kalshi_public_key.pem to Kalshi dashboard"
echo "2. 🗄️  Run database/supabase_setup.sql in Supabase"
echo "3. 🚂 Deploy to Railway with .env.railway.template variables"
echo "4. ✅ Follow DEPLOYMENT_CHECKLIST.md"

echo -e "\n${BLUE}Files Created:${NC}"
echo "• kalshi_private_key.pem (KEEP SECURE!)"
echo "• kalshi_public_key.pem (upload to Kalshi)"
echo "• private_key_base64.txt (Railway env var)"
echo "• .env.railway.template (Railway variables)"
echo "• DEPLOYMENT_CHECKLIST.md (step-by-step guide)"

echo -e "\n${YELLOW}Security Note:${NC}"
echo "🔒 Never commit private keys to version control!"

echo -e "\n${GREEN}Happy deploying! 🚀${NC}"
