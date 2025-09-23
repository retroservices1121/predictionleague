import os
import logging
import asyncio
import asyncpg
import aiohttp
import hashlib
import hmac
import base64
import json
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, MessageHandler, filters
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

@dataclass
class Market:
    id: str
    title: str
    category: str
    close_time: datetime
    volume: float = 0
    yes_price: float = 0.5
    no_price: float = 0.5
    status: str = 'active'

class KalshiAPI:
    def __init__(self, api_key: str, private_key: str, base_url: str = "https://trading-api.kalshi.com/trade-api/v2"):
        self.api_key = api_key
        self.private_key = private_key
        self.base_url = base_url
        self.session = None
        self.token = None
        self.token_expires = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def _create_signature(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """Create RSA signature for Kalshi API"""
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
            
            # Parse the private key
            if self.private_key.startswith('-----BEGIN'):
                key_data = self.private_key.encode()
            else:
                # Add PEM wrapper if missing
                key_data = f"-----BEGIN PRIVATE KEY-----\n{self.private_key}\n-----END PRIVATE KEY-----".encode()
            
            private_key = serialization.load_pem_private_key(key_data, password=None)
            
            # Create message to sign
            message = f"{timestamp}{method}{path}{body}".encode()
            
            # Sign the message
            signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
            return base64.b64encode(signature).decode()
        except Exception as e:
            logger.error(f"Signature creation failed: {e}")
            return ""

    async def login(self) -> bool:
        """Login to Kalshi API"""
        try:
            timestamp = str(int(datetime.now().timestamp() * 1000))
            path = "/login"
            method = "POST"
            
            signature = self._create_signature(timestamp, method, path)
            if not signature:
                return False

            headers = {
                'KALSHI-ACCESS-KEY': self.api_key,
                'KALSHI-ACCESS-SIGNATURE': signature,
                'KALSHI-ACCESS-TIMESTAMP': timestamp,
                'Content-Type': 'application/json'
            }

            async with self.session.post(f"{self.base_url}{path}", headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    self.token = data.get('token')
                    self.token_expires = datetime.now() + timedelta(hours=1)
                    logger.info("Successfully logged in to Kalshi")
                    return True
                else:
                    logger.error(f"Login failed: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    async def get_markets(self, limit: int = 20) -> List[Dict]:
        """Get active markets from Kalshi"""
        try:
            if not self.token or datetime.now() >= self.token_expires:
                if not await self.login():
                    return []

            headers = {'Authorization': f'Bearer {self.token}'}
            
            params = {
                'limit': limit,
                'status': 'open',
                'with_nested_markets': 'true'
            }
            
            async with self.session.get(f"{self.base_url}/markets", headers=headers, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('markets', [])
                else:
                    logger.error(f"Failed to get markets: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error getting markets: {e}")
            return []

class DatabaseManager:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool = None

    async def connect(self):
        """Connect to PostgreSQL database"""
        try:
            self.pool = await asyncpg.create_pool(self.database_url)
            await self.create_tables()
            logger.info("Database connected successfully")
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise

    async def create_tables(self):
        """Create necessary database tables"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id BIGINT PRIMARY KEY,
                    username VARCHAR(255),
                    first_name VARCHAR(255),
                    total_score INTEGER DEFAULT 0,
                    weekly_score INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS leagues (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) UNIQUE NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS league_members (
                    league_id INTEGER REFERENCES leagues(id),
                    user_id BIGINT REFERENCES users(id),
                    joined_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (league_id, user_id)
                );
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS markets (
                    id VARCHAR(255) PRIMARY KEY,
                    title TEXT NOT NULL,
                    category VARCHAR(255),
                    close_time TIMESTAMP,
                    week_start DATE,
                    is_resolved BOOLEAN DEFAULT FALSE,
                    resolution BOOLEAN,
                    volume DECIMAL DEFAULT 0,
                    yes_price DECIMAL DEFAULT 0.5,
                    no_price DECIMAL DEFAULT 0.5,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS predictions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id),
                    market_id VARCHAR(255) REFERENCES markets(id),
                    league_id INTEGER REFERENCES leagues(id),
                    prediction BOOLEAN,
                    confidence INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, market_id, league_id)
                );
            ''')

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS weekly_scores (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id),
                    league_id INTEGER REFERENCES leagues(id),
                    week_start DATE,
                    score INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, league_id, week_start)
                );
            ''')

    async def get_or_create_user(self, user_id: int, username: str, first_name: str):
        """Get or create user in database"""
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow('SELECT * FROM users WHERE id = $1', user_id)
            if not user:
                await conn.execute(
                    'INSERT INTO users (id, username, first_name) VALUES ($1, $2, $3)',
                    user_id, username, first_name
                )
                return await conn.fetchrow('SELECT * FROM users WHERE id = $1', user_id)
            return user

    async def get_weekly_markets(self, week_start: date) -> List[Dict]:
        """Get markets for a specific week"""
        async with self.pool.acquire() as conn:
            markets = await conn.fetch(
                'SELECT * FROM markets WHERE week_start = $1 AND is_resolved = FALSE ORDER BY close_time',
                week_start
            )
            return [dict(market) for market in markets]

    async def store_weekly_markets(self, markets: List[Dict], week_start: date):
        """Store weekly markets in database"""
        async with self.pool.acquire() as conn:
            for market in markets:
                # Convert string date to proper date object if needed
                close_time = market.get('close_time')
                if isinstance(close_time, str):
                    close_time = datetime.fromisoformat(close_time.replace('Z', '+00:00'))
                elif isinstance(close_time, datetime):
                    pass  # Already a datetime
                else:
                    close_time = datetime.now() + timedelta(days=7)  # Default fallback

                await conn.execute('''
                    INSERT INTO markets (id, title, category, close_time, week_start, volume, yes_price, no_price)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    category = EXCLUDED.category,
                    close_time = EXCLUDED.close_time,
                    volume = EXCLUDED.volume,
                    yes_price = EXCLUDED.yes_price,
                    no_price = EXCLUDED.no_price
                ''', 
                    market['ticker'], 
                    market['title'],
                    market.get('category', 'General'),
                    close_time,
                    week_start,
                    market.get('volume', 0),
                    market.get('yes_bid', 0.5),
                    market.get('no_bid', 0.5)
                )

    async def make_prediction(self, user_id: int, market_id: str, league_id: int, prediction: bool):
        """Record a user's prediction"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO predictions (user_id, market_id, league_id, prediction)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, market_id, league_id) 
                DO UPDATE SET prediction = EXCLUDED.prediction, created_at = NOW()
            ''', user_id, market_id, league_id, prediction)

    async def get_user_predictions(self, user_id: int, market_ids: List[str]) -> Dict[str, bool]:
        """Get user's predictions for given markets"""
        async with self.pool.acquire() as conn:
            predictions = await conn.fetch(
                'SELECT market_id, prediction FROM predictions WHERE user_id = $1 AND market_id = ANY($2)',
                user_id, market_ids
            )
            return {pred['market_id']: pred['prediction'] for pred in predictions}

    async def get_leaderboard(self, league_id: int = None) -> List[Dict]:
        """Get leaderboard for league or global"""
        async with self.pool.acquire() as conn:
            if league_id:
                query = '''
                    SELECT u.id, u.username, u.first_name, SUM(ws.score) as total_score
                    FROM users u
                    JOIN weekly_scores ws ON u.id = ws.user_id
                    WHERE ws.league_id = $1
                    GROUP BY u.id, u.username, u.first_name
                    ORDER BY total_score DESC
                    LIMIT 10
                '''
                results = await conn.fetch(query, league_id)
            else:
                query = '''
                    SELECT id, username, first_name, total_score
                    FROM users
                    ORDER BY total_score DESC
                    LIMIT 10
                '''
                results = await conn.fetch(query)
            
            return [dict(row) for row in results]

    async def get_default_league(self) -> Optional[int]:
        """Get or create default league"""
        async with self.pool.acquire() as conn:
            league = await conn.fetchrow('SELECT id FROM leagues WHERE name = $1', 'Global League')
            if not league:
                league_id = await conn.fetchval(
                    'INSERT INTO leagues (name) VALUES ($1) RETURNING id', 
                    'Global League'
                )
                return league_id
            return league['id']

class FantasyLeagueBot:
    def __init__(self, token: str, database_url: str, kalshi_api_key: str = None, kalshi_private_key: str = None):
        self.token = token
        self.db = DatabaseManager(database_url)
        self.kalshi_api_key = kalshi_api_key
        self.kalshi_private_key = kalshi_private_key
        self.kalshi_available = bool(kalshi_api_key and kalshi_private_key)
        
        # Rate limiting
        self.rate_limits = {}
        self.rate_limit_window = 60  # 1 minute
        self.rate_limit_max = 10     # 10 requests per minute

        # Build application
        self.application = Application.builder().token(token).build()
        self.setup_handlers()

    def setup_handlers(self):
        """Setup command and callback handlers"""
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("markets", self.markets_command))
        self.application.add_handler(CommandHandler("leaderboard", self.leaderboard_command))
        self.application.add_handler(CommandHandler("mystats", self.mystats_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CallbackQueryHandler(self.button_handler))

    async def rate_limit_check(self, user_id: int) -> bool:
        """Check if user is rate limited"""
        now = datetime.now().timestamp()
        if user_id not in self.rate_limits:
            self.rate_limits[user_id] = []
        
        # Clean old requests
        self.rate_limits[user_id] = [
            req_time for req_time in self.rate_limits[user_id] 
            if now - req_time < self.rate_limit_window
        ]
        
        if len(self.rate_limits[user_id]) >= self.rate_limit_max:
            return False
        
        self.rate_limits[user_id].append(now)
        return True

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        
        if not await self.rate_limit_check(user.id):
            await update.message.reply_text("⏰ Please wait a moment before trying again.")
            return

        await self.db.get_or_create_user(user.id, user.username, user.first_name)
        
        message = f"""🎯 **Welcome to Fantasy League Bot!**

Hi {user.first_name}! Ready to test your prediction skills?

🎮 **How it works:**
• Pick YES/NO on weekly prediction markets
• Earn points for correct predictions
• Compete on the leaderboard
• Win weekly and seasonal championships

🚀 **Get Started:**
• View this week's markets: /markets
• Check the leaderboard: /leaderboard
• See your stats: /mystats

Good luck! 🍀"""

        keyboard = [
            [InlineKeyboardButton("📊 View Markets", callback_data="markets")],
            [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
            [InlineKeyboardButton("📈 My Stats", callback_data="mystats")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            message, 
            reply_markup=reply_markup, 
            parse_mode=ParseMode.MARKDOWN
        )

    async def fetch_and_store_weekly_markets(self) -> bool:
        """Fetch markets from Kalshi and store for the week"""
        try:
            if not self.kalshi_available:
                # Store demo markets if Kalshi not available
                demo_markets = self.get_demo_markets()
                today = datetime.now().date()
                week_start = today - timedelta(days=today.weekday())
                await self.db.store_weekly_markets(demo_markets, week_start)
                return True
            
            async with KalshiAPI(self.kalshi_api_key, self.kalshi_private_key) as kalshi:
                markets = await kalshi.get_markets(limit=10)
                
                if markets:
                    today = datetime.now().date()
                    week_start = today - timedelta(days=today.weekday())
                    await self.db.store_weekly_markets(markets, week_start)
                    logger.info(f"Stored {len(markets)} markets for week {week_start}")
                    return True
                
        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
        
        return False

    def get_demo_markets(self) -> List[Dict]:
        """Get demo markets when Kalshi API is not available"""
        base_time = datetime.now()
        return [
            {
                'ticker': 'DEMO_BTC_100K',
                'title': 'Will Bitcoin reach $100,000 by end of 2024?',
                'category': 'Crypto',
                'close_time': base_time + timedelta(days=30),
                'volume': 15420,
                'yes_bid': 0.65,
                'no_bid': 0.35
            },
            {
                'ticker': 'DEMO_ELECTION_2024',
                'title': 'Will turnout exceed 150M in 2024 US election?',
                'category': 'Politics',
                'close_time': base_time + timedelta(days=45),
                'volume': 8930,
                'yes_bid': 0.72,
                'no_bid': 0.28
            },
            {
                'ticker': 'DEMO_STOCKS_SPY',
                'title': 'Will SPY close above $500 this week?',
                'category': 'Finance',
                'close_time': base_time + timedelta(days=5),
                'volume': 5670,
                'yes_bid': 0.45,
                'no_bid': 0.55
            },
            {
                'ticker': 'DEMO_TECH_AI',
                'title': 'Will any company announce AGI breakthrough in 2024?',
                'category': 'Technology',
                'close_time': base_time + timedelta(days=60),
                'volume': 12100,
                'yes_bid': 0.38,
                'no_bid': 0.62
            },
            {
                'ticker': 'DEMO_WEATHER_TEMP',
                'title': 'Will December 2024 be warmest on record?',
                'category': 'Climate',
                'close_time': base_time + timedelta(days=90),
                'volume': 3450,
                'yes_bid': 0.41,
                'no_bid': 0.59
            }
        ]

    async def markets_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show weekly markets"""
        user = update.effective_user
        
        if not await self.rate_limit_check(user.id):
            return

        try:
            await self.db.get_or_create_user(user.id, user.username, user.first_name)
            
            # Get current week's markets
            today = datetime.now().date()
            week_start = today - timedelta(days=today.weekday())
            
            markets = await self.db.get_weekly_markets(week_start)
            
            if not markets:
                # Fetch fresh markets
                await self.fetch_and_store_weekly_markets()
                markets = await self.db.get_weekly_markets(week_start)
            
            if not markets:
                await update.message.reply_text(
                    "🔄 **Markets Loading**\n\n"
                    "We're fetching fresh prediction markets.\n"
                    "Try again in 30 seconds! ⏰"
                )
                return
            
            # Get user's predictions
            market_ids = [m['id'] for m in markets]
            user_predictions = await self.db.get_user_predictions(user.id, market_ids)
            
            message = f"📊 **Week of {week_start.strftime('%B %d')} - Markets**\n\n"
            keyboard = []
            
            for i, market in enumerate(markets[:5], 1):
                # Status indicator
                if market['id'] in user_predictions:
                    prediction_emoji = "✅" if user_predictions[market['id']] else "❌"
                    status = f" {prediction_emoji}"
                else:
                    status = ""
                
                # Market details
                close_time = market['close_time'].strftime('%m/%d %I:%M%p')
                category = market.get('category', '📈').upper()
                
                message += f"**{i}. {market['title'][:55]}{'...' if len(market['title']) > 55 else ''}**\n"
                message += f"📅 {close_time} | 🏷️ {category}{status}\n\n"
                
                # Add prediction buttons if not yet predicted
                if market['id'] not in user_predictions and market['close_time'] > datetime.now():
                    keyboard.append([
                        InlineKeyboardButton(f"✅ YES #{i}", callback_data=f"predict_yes_{market['id']}"),
                        InlineKeyboardButton(f"❌ NO #{i}", callback_data=f"predict_no_{market['id']}")
                    ])
            
            if not keyboard:
                message += "ℹ️ *All markets predicted or closed*"
            
            keyboard.extend([
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_markets")],
                [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
                [InlineKeyboardButton("📈 My Stats", callback_data="mystats")]
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(
                    message, 
                    reply_markup=reply_markup, 
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    message, 
                    reply_markup=reply_markup, 
                    parse_mode=ParseMode.MARKDOWN
                )
            
        except Exception as e:
            logger.error(f"Error in markets_command: {e}")
            error_msg = "❌ Error loading your stats. Please try again."
            
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(error_msg)
            else:
                await update.message.reply_text(error_msg)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help message"""
        message = """🎯 **Fantasy League Bot Help**

**📚 Commands:**
/start - Welcome & main menu
/markets - View this week's prediction markets
/leaderboard - See top players
/mystats - Your personal statistics
/help - Show this help message
/status - Check bot system status

**🎮 How to Play:**
1. View weekly markets with /markets
2. Click YES/NO buttons to make predictions
3. Earn 10 points for each correct prediction
4. Compete on the weekly leaderboard
5. Track your progress with /mystats

**🏆 Scoring:**
• Correct prediction: +10 points
• Incorrect prediction: 0 points
• Bonus points for difficult predictions (coming soon!)

**💡 Tips:**
• Markets close at their scheduled time
• You can only predict once per market
• Check back weekly for new markets
• Study the odds before predicting

**🛟 Need Help?**
Contact @YourBotAdmin for support!

Good luck! 🍀"""

        keyboard = [
            [InlineKeyboardButton("📊 View Markets", callback_data="markets")],
            [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            message, 
            reply_markup=reply_markup, 
            parse_mode=ParseMode.MARKDOWN
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bot status"""
        try:
            # Check database connection
            async with self.db.pool.acquire() as conn:
                await conn.fetchval('SELECT 1')
            db_status = "✅ Connected"
        except:
            db_status = "❌ Error"
        
        # Check Kalshi API
        kalshi_status = "✅ Available" if self.kalshi_available else "⚠️ Demo Mode"
        
        # Get stats
        async with self.db.pool.acquire() as conn:
            total_users = await conn.fetchval('SELECT COUNT(*) FROM users')
            total_predictions = await conn.fetchval('SELECT COUNT(*) FROM predictions')
            active_markets = await conn.fetchval('SELECT COUNT(*) FROM markets WHERE is_resolved = FALSE')
        
        message = f"""🔍 **Bot Status**

**🗄️ Database:** {db_status}
**📡 Kalshi API:** {kalshi_status}
**⚡ Bot:** ✅ Running

**📊 Statistics:**
• Total Users: {total_users}
• Active Markets: {active_markets}
• Total Predictions: {total_predictions}

**🕐 Last Updated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC"""

        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button presses"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user = update.effective_user
        
        if not await self.rate_limit_check(user.id):
            await query.edit_message_text("⏰ Please wait a moment before trying again.")
            return
        
        try:
            if data == "markets" or data == "refresh_markets":
                # Create a fake update object for markets_command
                fake_update = type('obj', (object,), {
                    'callback_query': query,
                    'effective_user': user
                })
                await self.markets_command(fake_update, context)
                
            elif data == "leaderboard":
                fake_update = type('obj', (object,), {
                    'callback_query': query,
                    'effective_user': user
                })
                await self.leaderboard_command(fake_update, context)
                
            elif data == "mystats":
                fake_update = type('obj', (object,), {
                    'callback_query': query,
                    'effective_user': user
                })
                await self.mystats_command(fake_update, context)
                
            elif data.startswith("predict_"):
                await self.handle_prediction(query, data, user)
                
        except Exception as e:
            logger.error(f"Error in button_handler: {e}")
            await query.edit_message_text("❌ Something went wrong. Please try again.")

    async def handle_prediction(self, query, data, user):
        """Handle prediction button clicks"""
        try:
            # Parse the prediction data
            parts = data.split('_')
            if len(parts) < 3:
                await query.edit_message_text("❌ Invalid prediction format.")
                return
                
            prediction_type = parts[1]  # 'yes' or 'no'
            market_id = '_'.join(parts[2:])  # Rejoin in case market_id has underscores
            
            prediction = prediction_type == 'yes'
            
            # Get default league
            league_id = await self.db.get_default_league()
            
            # Make prediction
            await self.db.make_prediction(user.id, market_id, league_id, prediction)
            
            # Get market details for confirmation
            async with self.db.pool.acquire() as conn:
                market = await conn.fetchrow('SELECT * FROM markets WHERE id = $1', market_id)
            
            if market:
                pred_text = "YES ✅" if prediction else "NO ❌"
                message = f"🎯 **Prediction Recorded!**\n\n"
                message += f"**Market:** {market['title'][:60]}{'...' if len(market['title']) > 60 else ''}\n\n"
                message += f"**Your Prediction:** {pred_text}\n"
                message += f"**Closes:** {market['close_time'].strftime('%B %d, %Y at %I:%M %p')}\n\n"
                message += "Good luck! 🍀\n\n"
                message += "_You'll earn 10 points if correct when this market resolves._"
                
                keyboard = [
                    [InlineKeyboardButton("📊 View More Markets", callback_data="markets")],
                    [InlineKeyboardButton("📈 My Stats", callback_data="mystats")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    message, 
                    reply_markup=reply_markup, 
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await query.edit_message_text("❌ Market not found. Please try again.")
                
        except Exception as e:
            logger.error(f"Error in handle_prediction: {e}")
            await query.edit_message_text("❌ Error recording prediction. Please try again.")

    async def run(self):
        """Run the bot"""
        try:
            # Connect to database
            await self.db.connect()
            logger.info("Database connected")
            
            # Set bot commands
            commands = [
                BotCommand("start", "Welcome & main menu"),
                BotCommand("markets", "View prediction markets"),
                BotCommand("leaderboard", "See top players"),
                BotCommand("mystats", "Your statistics"),
                BotCommand("help", "Show help"),
                BotCommand("status", "Bot status")
            ]
            await self.application.bot.set_my_commands(commands)
            
            # Initialize with some demo markets if needed
            today = datetime.now().date()
            week_start = today - timedelta(days=today.weekday())
            existing_markets = await self.db.get_weekly_markets(week_start)
            
            if not existing_markets:
                logger.info("No markets found, creating demo markets...")
                await self.fetch_and_store_weekly_markets()
            
            # Start the bot
            logger.info("Starting Fantasy League Bot...")
            await self.application.run_polling(drop_pending_updates=True)
            
        except Exception as e:
            logger.error(f"Error running bot: {e}")
            raise

def main():
    """Main function"""
    # Get environment variables
    BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    DATABASE_URL = os.getenv('DATABASE_URL')
    KALSHI_API_KEY = os.getenv('KALSHI_API_KEY_ID')
    KALSHI_PRIVATE_KEY = os.getenv('KALSHI_PRIVATE_KEY_PEM')
    
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is required")
        return
    
    if not DATABASE_URL:
        logger.error("DATABASE_URL environment variable is required")
        return
    
    # Create and run bot
    bot = FantasyLeagueBot(BOT_TOKEN, DATABASE_URL, KALSHI_API_KEY, KALSHI_PRIVATE_KEY)
    
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")

if __name__ == "__main__":
    main() "❌ Error loading markets. Please try again."
            
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(error_msg)
            else:
                await update.message.reply_text(error_msg)

    async def leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show leaderboard"""
        user = update.effective_user
        
        if not await self.rate_limit_check(user.id):
            return

        try:
            leaderboard = await self.db.get_leaderboard()
            
            message = "🏆 **Global Leaderboard**\n\n"
            
            if not leaderboard:
                message += "No players yet! Be the first to make predictions! 🎯"
            else:
                for i, player in enumerate(leaderboard, 1):
                    emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                    name = player['first_name'] or player['username'] or f"User {player['id']}"
                    score = player.get('total_score', 0)
                    message += f"{emoji} **{name}** - {score} pts\n"
                
                # Show user's position if not in top 10
                user_in_top = any(p['id'] == user.id for p in leaderboard)
                if not user_in_top:
                    message += f"\n📍 Your position: Check with /mystats"
            
            keyboard = [
                [InlineKeyboardButton("📊 View Markets", callback_data="markets")],
                [InlineKeyboardButton("📈 My Stats", callback_data="mystats")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(
                    message, 
                    reply_markup=reply_markup, 
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    message, 
                    reply_markup=reply_markup, 
                    parse_mode=ParseMode.MARKDOWN
                )
                
        except Exception as e:
            logger.error(f"Error in leaderboard_command: {e}")
            error_msg = "❌ Error loading leaderboard. Please try again."
            
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(error_msg)
            else:
                await update.message.reply_text(error_msg)

    async def mystats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user's personal statistics"""
        user = update.effective_user
        
        if not await self.rate_limit_check(user.id):
            return

        try:
            await self.db.get_or_create_user(user.id, user.username, user.first_name)
            
            async with self.db.pool.acquire() as conn:
                # Get user stats
                user_data = await conn.fetchrow('SELECT * FROM users WHERE id = $1', user.id)
                
                # Get prediction stats
                total_predictions = await conn.fetchval(
                    'SELECT COUNT(*) FROM predictions WHERE user_id = $1', user.id
                )
                
                correct_predictions = await conn.fetchval('''
                    SELECT COUNT(*) FROM predictions p
                    JOIN markets m ON p.market_id = m.id
                    WHERE p.user_id = $1 AND m.is_resolved = TRUE 
                    AND p.prediction = m.resolution
                ''', user.id)
                
                # Calculate accuracy
                accuracy = (correct_predictions / total_predictions * 100) if total_predictions > 0 else 0
                
                message = f"📈 **Your Stats**\n\n"
                message += f"👤 **Player:** {user.first_name}\n"
                message += f"🎯 **Total Score:** {user_data['total_score']} pts\n"
                message += f"📊 **Predictions Made:** {total_predictions}\n"
                message += f"✅ **Correct:** {correct_predictions}\n"
                message += f"🎪 **Accuracy:** {accuracy:.1f}%\n"
                
                # Get recent predictions
                recent = await conn.fetch('''
                    SELECT m.title, p.prediction, m.is_resolved, m.resolution
                    FROM predictions p
                    JOIN markets m ON p.market_id = m.id
                    WHERE p.user_id = $1
                    ORDER BY p.created_at DESC
                    LIMIT 5
                ''', user.id)
                
                if recent:
                    message += "\n**🕐 Recent Predictions:**\n"
                    for pred in recent:
                        title = pred['title'][:40] + "..." if len(pred['title']) > 40 else pred['title']
                        pred_text = "YES" if pred['prediction'] else "NO"
                        
                        if pred['is_resolved']:
                            if pred['prediction'] == pred['resolution']:
                                status = "✅"
                            else:
                                status = "❌"
                        else:
                            status = "⏳"
                        
                        message += f"• {pred_text} on '{title}' {status}\n"
            
            keyboard = [
                [InlineKeyboardButton("📊 View Markets", callback_data="markets")],
                [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(
                    message, 
                    reply_markup=reply_markup, 
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    message, 
                    reply_markup=reply_markup, 
                    parse_mode=ParseMode.MARKDOWN
                )
                
        except Exception as e:
            logger.error(f"Error in mystats_command: {e}")
            error_msg =
