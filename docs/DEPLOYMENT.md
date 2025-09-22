# 🚀 Railway + Supabase Deployment Guide

## Prerequisites

- [Railway Account](https://railway.app)
- [Supabase Account](https://supabase.com)
- [Telegram Bot Token](https://core.telegram.org/bots#botfather)
- [Kalshi Account](https://kalshi.com) with API access

## Step 1: Setup Supabase Database

1. **Create Project**:
   - Go to [supabase.com/dashboard](https://supabase.com/dashboard)
   - Click "New Project"
   - Name: "prediction-league", set password, select region

2. **Run Database Schema**:
   - Go to SQL Editor
   - Copy and paste `database/supabase_setup.sql`
   - Click "Run" to execute

3. **Get Connection String**:
   - Settings → Database → Connection string
   - Copy the `postgresql://` URL

## Step 2: Generate RSA Keys

Run the setup script:
```bash
chmod +x setup_deployment.sh
./setup_deployment.sh
```

This creates:
- `kalshi_private_key.pem` (keep secure!)
- `kalshi_public_key.pem` (upload to Kalshi)
- `private_key_base64.txt` (for Railway env var)

## Step 3: Deploy to Railway

### Method A: GitHub (Recommended)

1. **Push to GitHub**:
   ```bash
   git add .
   git commit -m "Initial deployment"
   git push origin main
   ```

2. **Deploy on Railway**:
   - Go to [railway.app/dashboard](https://railway.app/dashboard)
   - "New Project" → "Deploy from GitHub repo"
   - Select your repository

3. **Add Environment Variables**:
   ```
   TELEGRAM_TOKEN=your_bot_token
   KALSHI_EMAIL=your_kalshi_email
   KALSHI_PASSWORD=your_kalshi_password
   KALSHI_PRIVATE_KEY=content_of_private_key_base64.txt
   DATABASE_URL=your_supabase_connection_string
   ```

## Step 4: Configure APIs

1. **Upload Kalshi Public Key**:
   - Go to [Kalshi API Settings](https://kalshi.com/profile/api)
   - Upload `kalshi_public_key.pem`

2. **Test Bot**:
   - Find your bot on Telegram
   - Send `/start`
   - Try `/markets` to verify Kalshi connection

## Troubleshooting

### Bot Not Responding
- Check Railway logs: `railway logs`
- Verify `TELEGRAM_TOKEN` is correct
- Ensure bot is not sleeping (Railway Hobby plan)

### Database Connection Failed
- Verify `DATABASE_URL` format
- Check Supabase project status
- Test connection from Railway logs

### Kalshi API Errors
- Confirm RSA keys are correct
- Check `KALSHI_PRIVATE_KEY` base64 encoding
- Verify Kalshi account has API access

## Production Checklist

- [ ] Database schema deployed successfully
- [ ] All environment variables configured
- [ ] RSA keys generated and uploaded
- [ ] Bot responds to `/start`
- [ ] Markets load with `/markets`
- [ ] Leaderboard displays users
- [ ] Railway deployment logs clean

## Cost Estimates

- **Railway Hobby**: Free (500h/month)
- **Railway Pro**: $20/month (unlimited)
- **Supabase Free**: $0 (500MB database)
- **Supabase Pro**: $25/month (8GB database)

**Recommended for production**: Railway Pro ($20) + Supabase Free = $20/month
