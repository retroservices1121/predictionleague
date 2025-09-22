# 🚀 Deployment Checklist

## Prerequisites Setup ✅
- [ ] Created Telegram bot with @BotFather
- [ ] Created Kalshi account with API access
- [ ] Created Supabase project
- [ ] Created Railway account

## Database Setup (Supabase) 📊
- [ ] Created new Supabase project
- [ ] Executed `database/supabase_setup.sql` in SQL Editor
- [ ] Copied DATABASE_URL from Settings → Database
- [ ] Verified tables created in Table Editor

## API Keys Configuration 🔐
- [ ] Generated RSA keys with `setup_deployment.sh`
- [ ] Uploaded public key to Kalshi dashboard
- [ ] Copied private key base64 from `private_key_base64.txt`
- [ ] Saved all credentials securely

## Railway Deployment 🚂
- [ ] Pushed code to GitHub repository
- [ ] Created new Railway project from GitHub
- [ ] Added all environment variables:
  - [ ] `TELEGRAM_TOKEN`
  - [ ] `KALSHI_EMAIL`
  - [ ] `KALSHI_PASSWORD`
  - [ ] `KALSHI_PRIVATE_KEY`
  - [ ] `DATABASE_URL`
- [ ] Verified deployment succeeded in Railway logs

## Testing & Verification 🧪
- [ ] Bot responds to `/start` command
- [ ] `/markets` command loads prediction markets
- [ ] Can create leagues with `/createleague`
- [ ] Database connections working (check logs)
- [ ] Kalshi API authentication successful

## Post-Deployment 🎯
- [ ] Tested bot with multiple users
- [ ] Verified leaderboard functionality
- [ ] Set up monitoring/alerts (optional)
- [ ] Documented bot usage for users
- [ ] Shared bot with initial user group

## Optional Enhancements 🔧
- [ ] Custom domain for Railway deployment
- [ ] Enhanced error monitoring
- [ ] Analytics dashboard setup
- [ ] RLS policies enabled (security)
- [ ] Automated backups configured

---
**All green? You're ready to launch! 🎉**
