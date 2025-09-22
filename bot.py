import os
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import aiohttp
import asyncpg
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
import base64
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
import re

# Configure logging for Railway
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),  # Console output for Railway logs
    ]
)
logger = logging.getLogger(__name__)

class KalshiClient:
    """Kalshi API client with RSA authentication"""
    
    def __init__(self, email: str, password: str, private_key_content: str = None):
        self.base_url = "https://trading-api.kalshi.com/trade-api/v2"
        self.email = email
        self.password = password
        self.private_key = self._load_private_key(private_key_content)
        self.session = None
        self.token = None
        self.last_auth = None
        
    def _load_private_key(self, key_content):
    try:
        if not key_content:
            return None

        # If it's already bytes, just use it
        if isinstance(key_content, bytes):
            decoded_key = key_content
        else:
            # Otherwise treat as base64 string
            decoded_key = base64.b64decode(key_content)

        return serialization.load_pem_private_key(
            decoded_key,
            password=None,
            backend=default_backend()
        )
    except Exception as e:
        logger.error(f"Failed to load private key: {e}")
        raise
    
    def _create_signature(self, method: str, path: str, body: str = "") -> str:
        """Create RSA signature for API requests"""
        message = f"{method}{path}{body}"
        signature = self.private_key.sign(
            message.encode(),
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        return base64.b64encode(signature).decode()
    
    async def authenticate(self):
        """Authenticate with Kalshi API"""
        if self.last_auth and (datetime.now() - self.last_auth).seconds < 3600:
            return True
            
        if not self.session:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
        
        login_data = {
            "email": self.email,
            "password": self.password
        }
        
        path = "/login"
        signature = self._create_signature("POST", path, json.dumps(login_data))
        
        headers = {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-SIGNATURE": signature
        }
        
        try:
            async with self.session.post(
                f"{self.base_url}{path}", 
                json=login_data, 
                headers=headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    self.token = data.get("token")
                    self.last_auth = datetime.now()
                    logger.info("Successfully authenticated with Kalshi")
                    return True
                else:
                    logger.error(f"Kalshi auth failed: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Kalshi auth error: {e}")
            return False
    
    async def get_markets(self, status: str = "open", limit: int = 20) -> List[Dict]:
        """Get markets from Kalshi"""
        if not await self.authenticate():
            return []
        
        path = f"/markets?status={status}&limit={limit}"
        signature = self._create_signature("GET", path)
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "KALSHI-ACCESS-SIGNATURE": signature
        }
        
        try:
            async with self.session.get(f"{self.base_url}{path}", headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("markets", [])
                else:
                    logger.warning(f"Get markets failed: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Get markets error: {e}")
            return []
    
    async def close(self):
        if self.session:
            await self.session.close()

class DatabaseManager:
    """Supabase database manager"""
    
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool = None
        
    async def init_pool(self):
        """Initialize connection pool"""
        self.pool = await asyncpg.create_pool(
            self.database_url,
            min_size=1,
            max_size=5,
            command_timeout=60
        )
        await self.create_tables()
    
    async def create_tables(self):
        """Create tables if they don't exist"""
        async with self.pool.acquire() as conn:
            # Basic users table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    total_points INTEGER DEFAULT 0,
                    weekly_points INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Weekly markets
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS weekly_markets (
                    id SERIAL PRIMARY KEY,
                    week_start DATE,
                    market_ticker TEXT,
                    title TEXT,
                    category TEXT,
                    close_time TIMESTAMP,
                    resolved BOOLEAN DEFAULT FALSE,
                    resolution_value BOOLEAN,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Predictions
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS predictions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(telegram_id),
                    market_id INTEGER REFERENCES weekly_markets(id),
                    prediction BOOLEAN,
                    points_earned INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, market_id)
                )
            ''')
            
            # Leagues
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS leagues (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    admin_id BIGINT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # League members
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS league_members (
                    league_id INTEGER REFERENCES leagues(id),
                    user_id BIGINT REFERENCES users(telegram_id),
                    joined_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (league_id, user_id)
                )
            ''')
    
    async def get_or_create_user(self, telegram_id: int, username: str = None, first_name: str = None):
        """Get or create user"""
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow(
                "SELECT * FROM users WHERE telegram_id = $1", telegram_id
            )
            if not user:
                await conn.execute(
                    """INSERT INTO users (telegram_id, username, first_name) 
                       VALUES ($1, $2, $3)""",
                    telegram_id, username, first_name
                )
                user = await conn.fetchrow(
                    "SELECT * FROM users WHERE telegram_id = $1", telegram_id
                )
            return user
    
    async def get_weekly_markets(self, week_start: datetime.date):
        """Get weekly markets"""
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM weekly_markets WHERE week_start = $1",
                week_start
            )
    
    async def save_prediction(self, user_id: int, market_id: int, prediction: bool):
        """Save prediction"""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO predictions (user_id, market_id, prediction) 
                   VALUES ($1, $2, $3) 
                   ON CONFLICT (user_id, market_id) DO UPDATE SET prediction = $3""",
                user_id, market_id, prediction
            )
    
    async def create_league(self, name: str, admin_id: int):
        """Create league"""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "INSERT INTO leagues (name, admin_id) VALUES ($1, $2) RETURNING id",
                name, admin_id
            )
    
    async def join_league(self, league_id: int, user_id: int):
        """Join league"""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO league_members (league_id, user_id) 
                   VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                league_id, user_id
            )
    
    async def get_leaderboard(self, limit: int = 10):
        """Get leaderboard"""
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """SELECT telegram_id, username, first_name, total_points
                   FROM users ORDER BY total_points DESC LIMIT $1""",
                limit
            )

class PredictionBot:
    """Main bot class"""
    
    def __init__(self):
        # Get environment variables
        self.telegram_token = os.getenv("TELEGRAM_TOKEN")
        self.kalshi_email = os.getenv("KALSHI_EMAIL")
        self.kalshi_password = os.getenv("KALSHI_PASSWORD")
        self.private_key = os.getenv("KALSHI_PRIVATE_KEY")
        self.database_url = os.getenv("DATABASE_URL")
        
        if not all([self.telegram_token, self.kalshi_email, self.kalshi_password, self.database_url]):
            raise ValueError("Missing required environment variables")
        
        # Initialize components
        self.application = Application.builder().token(self.telegram_token).build()
        self.kalshi = KalshiClient(self.kalshi_email, self.kalshi_password, self.private_key)
        self.db = DatabaseManager(self.database_url)
        
        # Register handlers
        self._register_handlers()
    
    def _register_handlers(self):
        """Register command handlers"""
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("markets", self.markets_command))
        self.application.add_handler(CommandHandler("leaderboard", self.leaderboard_command))
        self.application.add_handler(CommandHandler("createleague", self.create_league_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command"""
        user = update.effective_user
        await self.db.get_or_create_user(user.id, user.username, user.first_name)
        
        welcome = """
🎉 **Welcome to Prediction League!**

Turn Kalshi markets into a fantasy game!

📊 /markets - See this week's predictions
🏆 /leaderboard - Check rankings
⚙️ /createleague - Start a private league

Ready to predict the future? 🔮
        """
        
        await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)
    
    async def markets_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show markets"""
        user = update.effective_user
        await self.db.get_or_create_user(user.id, user.username, user.first_name)
        
        # Get current week
        today = datetime.now().date()
        week_start = today - timedelta(days=today.weekday())
        
        markets = await self.db.get_weekly_markets(week_start)
        
        if not markets:
            # Fetch and store new markets
            await self.fetch_weekly_markets()
            markets = await self.db.get_weekly_markets(week_start)
        
        if not markets:
            await update.message.reply_text("No markets available this week!")
            return
        
        message = f"📊 **Week of {week_start.strftime('%B %d')}**\n\n"
        keyboard = []
        
        for i, market in enumerate(markets[:5], 1):
            message += f"{i}. {market['title'][:50]}...\n"
            if market['close_time']:
                message += f"   Closes: {market['close_time'].strftime('%m/%d %I:%M%p')}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(f"✅ YES #{i}", callback_data=f"predict_yes_{market['id']}"),
                InlineKeyboardButton(f"❌ NO #{i}", callback_data=f"predict_no_{market['id']}")
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    async def leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show leaderboard"""
        leaderboard = await self.db.get_leaderboard()
        
        message = "🏆 **Leaderboard** 🏆\n\n"
        for i, player in enumerate(leaderboard, 1):
            emoji = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"{i}."
            name = player['first_name'] or player['username'] or f"User {player['telegram_id']}"
            message += f"{emoji} {name} — {player['total_points']} pts\n"
        
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
    
    async def create_league_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Create league"""
        user = update.effective_user
        league_name = ' '.join(context.args) if context.args else f"{user.first_name}'s League"
        
        league_id = await self.db.create_league(league_name, user.id)
        await self.db.join_league(league_id, user.id)
        
        await update.message.reply_text(
            f"🎉 Created league: **{league_name}**\n"
            f"League ID: `{league_id}`\n"
            f"Share this ID for others to join!",
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button presses"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        
        if data.startswith("predict_"):
            _, prediction, market_id = data.split("_")
            prediction_value = prediction == "yes"
            
            await self.db.save_prediction(user_id, int(market_id), prediction_value)
            
            prediction_text = "✅ YES" if prediction_value else "❌ NO"
            await query.edit_message_text(
                f"🎯 **Prediction saved:** {prediction_text}\n\n"
                f"Good luck! Use /markets to make more predictions."
            )
    
    async def fetch_weekly_markets(self):
        """Fetch and store weekly markets"""
        try:
            markets = await self.kalshi.get_markets(limit=10)
            
            today = datetime.now().date()
            week_start = today - timedelta(days=today.weekday())
            
            # Filter interesting markets
            interesting_markets = []
            for market in markets:
                title = market.get('title', '').lower()
                
                # Categorize market
                category = 'other'
                if any(word in title for word in ['nfl', 'nba', 'sports', 'football', 'basketball']):
                    category = 'sports'
                elif any(word in title for word in ['election', 'president', 'politics', 'vote']):
                    category = 'politics'
                elif any(word in title for word in ['bitcoin', 'crypto', 'eth', 'price']):
                    category = 'finance'
                
                # Parse close time
                close_time = None
                if market.get('close_time'):
                    try:
                        close_time = datetime.fromisoformat(
                            market['close_time'].replace('Z', '+00:00')
                        )
                    except:
                        continue
                
                interesting_markets.append({
                    'ticker': market.get('ticker'),
                    'title': market.get('title'),
                    'category': category,
                    'close_time': close_time
                })
            
            # Store in database
            async with self.db.pool.acquire() as conn:
                for market in interesting_markets[:5]:
                    await conn.execute(
                        """INSERT INTO weekly_markets 
                           (week_start, market_ticker, title, category, close_time)
                           VALUES ($1, $2, $3, $4, $5)""",
                        week_start, market['ticker'], market['title'], 
                        market['category'], market['close_time']
                    )
            
            logger.info(f"Stored {len(interesting_markets[:5])} markets for week {week_start}")
            
        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
    
    async def run(self):
        """Run the bot"""
        try:
            # Initialize database
            await self.db.init_pool()
            logger.info("Database initialized")
            
            # Test Kalshi connection
            if await self.kalshi.authenticate():
                logger.info("Kalshi authenticated")
            else:
                logger.warning("Kalshi authentication failed")
            
            # Start bot
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(
                allowed_updates=['message', 'callback_query'],
                drop_pending_updates=True
            )
            
            logger.info("Bot started successfully")
            
            # Keep running
            await asyncio.Future()
            
        except Exception as e:
            logger.error(f"Bot error: {e}")
            raise
        finally:
            # Cleanup
            if self.application.updater.running:
                await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
            await self.kalshi.close()

async def main():
    """Main function"""
    try:
        bot = PredictionBot()
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    # Load environment variables if .env file exists
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # python-dotenv not required for Railway
    
    asyncio.run(main())
