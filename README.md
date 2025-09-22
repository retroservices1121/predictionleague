# 🔥 Prediction League Bot

A Telegram bot that turns Kalshi prediction markets into a fantasy-style game! Compete with friends by making predictions on sports, politics, crypto, and finance markets.

![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Railway](https://img.shields.io/badge/deploy-Railway-purple.svg)
![Supabase](https://img.shields.io/badge/database-Supabase-green.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## 🎯 Features

- **Weekly Market Selection**: Curated Kalshi markets across multiple categories
- **Fantasy Scoring**: Base points + bonuses for contrarian picks, streaks, and early predictions
- **Private Leagues**: Create custom leagues for friend groups with invite codes
- **Achievement System**: Unlock badges for milestones and special accomplishments
- **Real-time Leaderboards**: Weekly and all-time rankings with detailed statistics
- **Social Integration**: Works seamlessly in Telegram group chats

## 🚀 Quick Deploy to Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template/your-template-id)

### Prerequisites

- [Railway Account](https://railway.app)
- [Supabase Account](https://supabase.com) 
- [Telegram Bot Token](https://core.telegram.org/bots#3-how-do-i-create-a-bot)
- [Kalshi Account](https://kalshi.com) with API access

### One-Click Setup

1. **Run the setup script**:
   ```bash
   chmod +x setup_deployment.sh
   ./setup_deployment.sh
   ```

2. **Setup Supabase database**:
   - Create new project at [supabase.com](https://supabase.com)
   - Run SQL from `database/supabase_setup.sql` in SQL Editor
   - Copy DATABASE_URL from Settings → Database

3. **Deploy to Railway**:
   - Fork this repository
   - Connect to Railway
   - Add environment variables from `.env.example`
   - Deploy automatically!

4. **Configure APIs**:
   - Upload `kalshi_public_key.pem` to Kalshi dashboard
   - Test bot with `/start` command

## 📱 Bot Commands

- `/start` - Welcome and setup your account
- `/markets` - View this week's prediction markets
- `/leaderboard` - Check current rankings
- `/mystats` - Personal performance statistics
- `/createleague [name]` - Create a private league
- `/joinleague [id]` - Join a league by ID
- `/achievements` - View unlocked badges

## 🏆 Scoring System

### Base Points
- ✅ Correct prediction: **10 points**
- ❌ Wrong prediction: **0 points**

### Bonus Multipliers
- 🧠 **Contrarian Bonus**: +50% for predictions with <30% market odds
- ⚡ **Early Bird**: +3 points for predictions made >24h before close
- 🔥 **Streak Bonus**: +2 points per prediction after 3+ correct in a row

### Achievement Badges
- 👶 First Steps, 🔥 Hot Streak, 🧠 Contrarian Genius
- 🏈 Sports Prophet, 💯 Perfect Week, 💰 Century Club
- And 6 more unlockable achievements!

## 🛠️ Development

### Local Setup

```bash
# Clone repository
git clone https://github.com/yourusername/prediction-league-bot.git
cd prediction-league-bot

# Install dependencies
pip install -r requirements.txt

# Setup environment
cp .env.example .env
# Fill in your API credentials

# Run database setup
# Execute database/supabase_setup.sql in your Supabase project

# Start bot
python bot.py
```

### Environment Variables

```bash
TELEGRAM_TOKEN=your_telegram_bot_token
KALSHI_EMAIL=your_kalshi_email
KALSHI_PASSWORD=your_kalshi_password  
KALSHI_PRIVATE_KEY=base64_encoded_private_key
DATABASE_URL=postgresql://postgres:password@host:5432/database
```

## 📊 Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Telegram      │    │   Railway       │    │   Supabase      │
│   Bot API       │◄──►│   Python Bot    │◄──►│   PostgreSQL    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                              │
                              ▼
                       ┌─────────────────┐
                       │   Kalshi API    │
                       │   (Markets)     │
                       └─────────────────┘
```

- **Telegram**: User interface and command handling
- **Railway**: Hosting platform with auto-deployment
- **Supabase**: PostgreSQL database with real-time features
- **Kalshi API**: Prediction market data and resolution

## 🔐 Security

- RSA signature authentication for Kalshi API
- Rate limiting on bot commands (2-second cooldown)
- Input validation and SQL injection prevention
- Optional Row Level Security (RLS) for database
- Secure environment variable handling

## 📈 Scaling

The bot is optimized for:
- **Free tier**: Up to 100 active users
- **Pro tier**: 1000+ users with Railway Pro + Supabase Pro
- **Enterprise**: Custom scaling with connection pooling and caching

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Make your changes and test thoroughly
4. Submit a pull request with detailed description

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

- 📖 [Deployment Guide](docs/DEPLOYMENT.md)
- 📋 [Setup Checklist](docs/DEPLOYMENT_CHECKLIST.md)
- 🐛 [Issue Tracker](https://github.com/yourusername/prediction-league-bot/issues)
- 💬 [Telegram Community](https://t.me/predictionleague)

## 🙏 Acknowledgments

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) for the excellent Telegram library
- [Kalshi](https://kalshi.com) for prediction market data
- [Railway](https://railway.app) for simple deployment
- [Supabase](https://supabase.com) for the database platform

---

**Built with ❤️ for the prediction market community**

[⭐ Star this repo](https://github.com/yourusername/prediction-league-bot) | [🐛 Report Bug](https://github.com/yourusername/prediction-league-bot/issues) | [💡 Request Feature](https://github.com/yourusername/prediction-league-bot/issues)
