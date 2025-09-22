import os
import logging
import asyncio
import aiohttp
import asyncpg
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import json
import hashlib
import hmac
import base64
from urllib.parse import urlencode

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from aiohttp import web

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class KalshiClient:
    """Enhanced Kalshi API client for Railway deployment"""
    
    def __init__(self, email: str, password: str, private_key_content: str = None):
        self.base_url = "https://trading-api.kalshi.com/trade-api/v2"
        self.email = email
        self.password = password
        self.private_key = self._load_private_key(private_key_content)
        self.session = None
        self.token = None
        self.last_auth = None

    def _load_private_key(self, key_content: str = None):
        """Load RSA private key from Kalshi's provided format"""
        import logging
        
        logger = logging.getLogger(__name__)
        
        if not key_content:
            raise ValueError("Private key content is required")
        
        logger.info("Loading Kalshi-provided private key...")
        
        try:
            # Kalshi typically provides keys in PEM format
            # Clean up any extra whitespace but preserve line breaks
            clean_key = key_content.strip()
            
            # Ensure proper line endings
            if '\\n' in clean_key:
                # If escaped newlines, convert them
                clean_key = clean_key.replace('\\n', '\n')
            
            # Load the PEM private key directly
            private_key = serialization.load_pem_private_key(
                clean_key.encode('utf-8'),
                password=None
            )
            
            logger.info("Successfully loaded Kalshi private key")
            return private_key
            
        except Exception as e:
            logger.error(f"Failed to load Kalshi private key: {e}")
            logger.error(f"Key preview: {key_content[:100]}...")
            
            # Try with different line ending formats
            try:
                # Try with normalized line endings
                normalized_key = key_content.replace('\\n', '\n').replace('\r\n', '\n').replace('\r', '\n')
                private_key = serialization.load_pem_private_key(
                    normalized_key.encode('utf-8'),
                    password=None
                )
                logger.info("Successfully loaded key with normalized line endings")
                return private_key
            except Exception as e2:
                logger.error(f"Normalized format also failed: {e2}")
                raise ValueError(f"Unable to load Kalshi private key: {e}")

    def _create_signature(self, method: str, path: str, body: str = "") -> str:
        """Create RSA signature for API requests"""
        try:
            message = f"{method}{path}{body}"
            signature = self.private_key.sign(
                message.encode(),
                padding.PKCS1v15(),
                hashes.SHA256()
            )
            return base64.b64encode(signature).decode()
        except Exception as e:
            logger.error(f"Failed to create signature: {e}")
            raise

    async def _ensure_session(self):
        """Ensure aiohttp session exists"""
        if not self.session:
            self.session = aiohttp.ClientSession()

    async def _authenticate(self) -> bool:
        """Authenticate with Kalshi API"""
        try:
            await self._ensure_session()
            
            # Check if we have a valid token (within last 30 minutes)
            if self.token and self.last_auth:
                if datetime.now() - self.last_auth < timedelta(minutes=30):
                    return True

            # Login to get token
            login_data = {
                "email": self.email,
                "password": self.password
            }
            
            path = "/login"
            body = json.dumps(login_data)
            signature = self._create_signature("POST", path, body)
            
            headers = {
                "Content-Type": "application/json",
                "KALSHI-ACCESS-SIGNATURE": signature
            }
            
            async with self.session.post(
                f"{self.base_url}{path}",
                headers=headers,
                data=body
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    self.token = data.get("token")
                    self.last_auth = datetime.now()
                    logger.info("Successfully authenticated with Kalshi")
                    return True
                else:
                    logger.error(f"Authentication failed: {response.status}")
                    return False
                    
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return False

    async def get_markets(self, limit: int = 10) -> List[Dict]:
        """Get prediction markets from Kalshi"""
        try:
            if not await self._authenticate():
                return []

            path = f"/exchange/markets?limit={limit}&status=open"
            signature = self._create_signature("GET", path)
            
            headers = {
                "Authorization": f"Bearer {self.token}",
                "KALSHI-ACCESS-SIGNATURE": signature
            }
            
            async with self.session.get(
                f"{self.base_url}{path}",
                headers=headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("markets", [])
                else:
                    logger.error(f"Failed to get markets: {response.status}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error getting markets: {e}")
            return []

    async def get_market_details(self, market_ticker: str) -> Optional[Dict]:
        """Get detailed information about a specific market"""
        try:
            if not await self._authenticate():
                return None

            path = f"/exchange/markets/{market_ticker}"
            signature = self._create_signature("GET", path)
            
            headers = {
                "Authorization": f"Bearer {self.token}",
                "KALSHI-ACCESS-SIGNATURE": signature
            }
            
            async with self.session.get(
                f"{self.base_url}{path}",
                headers=headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("market")
                else:
                    logger.error(f"Failed to get market details: {response.status}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting market details: {e}")
            return None

    async def close(self):
        """Close the aiohttp session"""
        if self.session:
            await self.session.close()

@dataclass
class League:
    id: int
    name: str
    creator_id: int
    created_at: datetime
    is_active: bool = True
    max_members: int = 50

@dataclass
class UserPrediction:
    id: int
    user_id: int
    league_id: int
    market_ticker: str
    prediction: str
    confidence: int
    created_at: datetime
    points: int = 0

class DatabaseManager:
    """Database operations for PostgreSQL"""
    
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool = None

    async def connect(self):
        """Create database connection pool"""
        try:
            self.pool = await asyncpg.create_pool(self.database_url)
            logger.info("Database connection pool created")
            await self.init_tables()
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise

    async def init_tables(self):
        """Initialize database tables"""
        try:
            async with self.pool.acquire() as conn:
                # Users table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id BIGINT PRIMARY KEY,
                        username VARCHAR(255),
                        first_name VARCHAR(255),
                        last_name VARCHAR(255),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        total_points INTEGER DEFAULT 0,
                        predictions_made INTEGER DEFAULT 0
                    )
                ''')
                
                # Leagues table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS leagues (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        creator_id BIGINT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_active BOOLEAN DEFAULT TRUE,
                        max_members INTEGER DEFAULT 50,
                        FOREIGN KEY (creator_id) REFERENCES users(id)
                    )
                ''')
                
                # League members table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS league_members (
                        league_id INTEGER,
                        user_id BIGINT,
                        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (league_id, user_id),
                        FOREIGN KEY (league_id) REFERENCES leagues(id),
                        FOREIGN KEY (user_id) REFERENCES users(id)
                    )
                ''')
                
                # Predictions table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS predictions (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        league_id INTEGER NOT NULL,
                        market_ticker VARCHAR(255) NOT NULL,
                        prediction VARCHAR(10) NOT NULL,
                        confidence INTEGER NOT NULL CHECK (confidence >= 1 AND confidence <= 100),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        points INTEGER DEFAULT 0,
                        is_resolved BOOLEAN DEFAULT FALSE,
                        FOREIGN KEY (user_id) REFERENCES users(id),
                        FOREIGN KEY (league_id) REFERENCES leagues(id)
                    )
                ''')
                
            logger.info("Database tables initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize tables: {e}")
            raise

    async def get_or_create_user(self, user_id: int, username: str = None, 
                                first_name: str = None, last_name: str = None):
        """Get existing user or create new one"""
        try:
            async with self.pool.acquire() as conn:
                # Try to get existing user
                user = await conn.fetchrow(
                    "SELECT * FROM users WHERE id = $1", user_id
                )
                
                if not user:
                    # Create new user
                    await conn.execute('''
                        INSERT INTO users (id, username, first_name, last_name)
                        VALUES ($1, $2, $3, $4)
                    ''', user_id, username, first_name, last_name)
                    logger.info(f"Created new user: {user_id}")
                
                return await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
                
        except Exception as e:
            logger.error(f"Error with user operations: {e}")
            return None

    async def create_league(self, name: str, creator_id: int) -> Optional[int]:
        """Create a new prediction league"""
        try:
            async with self.pool.acquire() as conn:
                league_id = await conn.fetchval('''
                    INSERT INTO leagues (name, creator_id)
                    VALUES ($1, $2)
                    RETURNING id
                ''', name, creator_id)
                
                # Add creator as member
                await conn.execute('''
                    INSERT INTO league_members (league_id, user_id)
                    VALUES ($1, $2)
                ''', league_id, creator_id)
                
                logger.info(f"Created league: {league_id}")
                return league_id
                
        except Exception as e:
            logger.error(f"Error creating league: {e}")
            return None

    async def join_league(self, league_id: int, user_id: int) -> bool:
        """Join a user to a league"""
        try:
            async with self.pool.acquire() as conn:
                # Check if league exists and is active
                league = await conn.fetchrow(
                    "SELECT * FROM leagues WHERE id = $1 AND is_active = TRUE", 
                    league_id
                )
                
                if not league:
                    return False
                
                # Check if already a member
                existing = await conn.fetchrow('''
                    SELECT * FROM league_members 
                    WHERE league_id = $1 AND user_id = $2
                ''', league_id, user_id)
                
                if existing:
                    return True  # Already a member
                
                # Check member limit
                member_count = await conn.fetchval('''
                    SELECT COUNT(*) FROM league_members WHERE league_id = $1
                ''', league_id)
                
                if member_count >= league['max_members']:
                    return False
                
                # Add member
                await conn.execute('''
                    INSERT INTO league_members (league_id, user_id)
                    VALUES ($1, $2)
                ''', league_id, user_id)
                
                logger.info(f"User {user_id} joined league {league_id}")
                return True
                
        except Exception as e:
            logger.error(f"Error joining league: {e}")
            return False

    async def get_user_leagues(self, user_id: int) -> List[Dict]:
        """Get all leagues a user is member of"""
        try:
            async with self.pool.acquire() as conn:
                leagues = await conn.fetch('''
                    SELECT l.*, lm.joined_at
                    FROM leagues l
                    JOIN league_members lm ON l.id = lm.league_id
                    WHERE lm.user_id = $1 AND l.is_active = TRUE
                    ORDER BY lm.joined_at DESC
                ''', user_id)
                
                return [dict(league) for league in leagues]
                
        except Exception as e:
            logger.error(f"Error getting user leagues: {e}")
            return []

    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()

class PredictionBot:
    """Main Telegram bot class"""
    
    def __init__(self):
        # Get environment variables
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.kalshi_email = os.getenv("KALSHI_EMAIL")
        self.kalshi_password = os.getenv("KALSHI_PASSWORD")
        self.private_key = os.getenv("KALSHI_PRIVATE_KEY")
        self.database_url = os.getenv("DATABASE_URL")
        
        if not all([self.token, self.kalshi_email, self.kalshi_password, 
                   self.private_key, self.database_url]):
            raise ValueError("Missing required environment variables")
        
        # Initialize components
        self.kalshi = KalshiClient(self.kalshi_email, self.kalshi_password, self.private_key)
        self.db = DatabaseManager(self.database_url)
        
        # Create application
        self.application = Application.builder().token(self.token).build()
        
        # Add handlers
        self._setup_handlers()

    def _setup_handlers(self):
        """Setup command and callback handlers"""
        # Command handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("markets", self.markets_command))
        self.application.add_handler(CommandHandler("createleague", self.create_league_command))
        self.application.add_handler(CommandHandler("joinleague", self.join_league_command))
        self.application.add_handler(CommandHandler("myleagues", self.my_leagues_command))
        self.application.add_handler(CommandHandler("leaderboard", self.leaderboard_command))
        
        # Callback handlers
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Message handlers
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        await self.db.get_or_create_user(
            user.id, user.username, user.first_name, user.last_name
        )
        
        welcome_text = f"""
🎯 **Welcome to Prediction League!** 🎯

Hey {user.first_name}! Ready to test your prediction skills?

**What you can do:**
📊 Browse prediction markets
🏆 Create or join leagues
🎲 Make predictions and earn points
📈 Compete on leaderboards

**Quick Start:**
• `/markets` - See available markets
• `/createleague MyLeague` - Create a league
• `/myleagues` - View your leagues
• `/help` - Full command list

Let's make some predictions! 🚀
        """
        
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_text = """
🎯 **Prediction League Commands** 🎯

**Markets & Predictions:**
• `/markets` - Browse available prediction markets
• `/predict [market] [yes/no] [confidence]` - Make a prediction

**League Management:**
• `/createleague [name]` - Create a new league
• `/joinleague [id]` - Join existing league
• `/myleagues` - View your leagues
• `/leaderboard [league_id]` - View league leaderboard

**Account:**
• `/profile` - View your profile and stats
• `/start` - Welcome message

**How to Play:**
1. Join or create a league
2. Browse markets and make predictions
3. Earn points based on accuracy
4. Climb the leaderboard!

Need help? Contact @YourUsername
        """
        
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

    async def markets_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /markets command"""
        try:
            await update.message.reply_text("🔍 Loading markets...")
            
            markets = await self.kalshi.get_markets(limit=10)
            
            if not markets:
                await update.message.reply_text(
                    "❌ No markets available right now. Please try again later."
                )
                return
            
            keyboard = []
            text = "📊 **Available Prediction Markets:**\n\n"
            
            for i, market in enumerate(markets[:5], 1):
                title = market.get('title', 'Unknown Market')
                ticker = market.get('ticker', 'N/A')
                yes_price = market.get('yes_bid', 0) / 100 if market.get('yes_bid') else 0
                
                text += f"{i}. **{title}**\n"
                text += f"   🎯 Ticker: `{ticker}`\n"
                text += f"   💰 Yes Price: {yes_price:.2f}¢\n\n"
                
                keyboard.append([
                    InlineKeyboardButton(
                        f"📈 Predict: {title[:30]}...", 
                        callback_data=f"predict_{ticker}"
                    )
                ])
            
            keyboard.append([
                InlineKeyboardButton("🔄 Refresh Markets", callback_data="refresh_markets")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                text, 
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
            
        except Exception as e:
            logger.error(f"Error in markets command: {e}")
            await update.message.reply_text(
                "❌ Error loading markets. Please try again later."
            )

    async def create_league_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /createleague command"""
        if not context.args:
            await update.message.reply_text(
                "❌ Please provide a league name!\n"
                "Example: `/createleague My Awesome League`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        league_name = " ".join(context.args)
        user_id = update.effective_user.id
        
        # Ensure user exists
        await self.db.get_or_create_user(
            user_id, 
            update.effective_user.username,
            update.effective_user.first_name,
            update.effective_user.last_name
        )
        
        league_id = await self.db.create_league(league_name, user_id)
        
        if league_id:
            await update.message.reply_text(
                f"🎉 **League Created!**\n\n"
                f"📛 Name: {league_name}\n"
                f"🆔 League ID: `{league_id}`\n"
                f"👤 Creator: {update.effective_user.first_name}\n\n"
                f"Share this ID with friends to let them join!\n"
                f"Command: `/joinleague {league_id}`",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                "❌ Failed to create league. Please try again."
            )

    async def join_league_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /joinleague command"""
        if not context.args:
            await update.message.reply_text(
                "❌ Please provide a league ID!\n"
                "Example: `/joinleague 123`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        try:
            league_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid league ID. Must be a number.")
            return
        
        user_id = update.effective_user.id
        
        # Ensure user exists
        await self.db.get_or_create_user(
            user_id,
            update.effective_user.username,
            update.effective_user.first_name,
            update.effective_user.last_name
        )
        
        success = await self.db.join_league(league_id, user_id)
        
        if success:
            await update.message.reply_text(
                f"🎉 **Successfully joined league!**\n\n"
                f"🆔 League ID: {league_id}\n"
                f"👤 Welcome, {update.effective_user.first_name}!\n\n"
                f"Use `/myleagues` to see all your leagues."
            )
        else:
            await update.message.reply_text(
                "❌ Failed to join league. It might be full, inactive, or not exist."
            )

    async def my_leagues_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /myleagues command"""
        user_id = update.effective_user.id
        leagues = await self.db.get_user_leagues(user_id)
        
        if not leagues:
            await update.message.reply_text(
                "📭 **No leagues yet!**\n\n"
                "Create one: `/createleague MyLeague`\n"
                "Or join one: `/joinleague [ID]`"
            )
            return
        
        text = f"🏆 **Your Leagues ({len(leagues)}):**\n\n"
        keyboard = []
        
        for league in leagues:
            text += f"📛 **{league['name']}**\n"
            text += f"🆔 ID: `{league['id']}`\n"
            text += f"📅 Joined: {league['joined_at'].strftime('%Y-%m-%d')}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"📊 {league['name']} Leaderboard",
                    callback_data=f"leaderboard_{league['id']}"
                )
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )

    async def leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /leaderboard command"""
        await update.message.reply_text(
            "🏆 **Leaderboard feature coming soon!**\n\n"
            "We're working on implementing the scoring system and leaderboards.\n"
            "Stay tuned for updates! 🚀"
        )

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard callbacks"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data.startswith("predict_"):
            ticker = data.replace("predict_", "")
            await self.handle_prediction(query, ticker)
        elif data.startswith("leaderboard_"):
            league_id = data.replace("leaderboard_", "")
            await self.show_leaderboard(query, int(league_id))
        elif data == "refresh_markets":
            await self.refresh_markets(query)

    async def handle_prediction(self, query, ticker: str):
        """Handle prediction selection"""
        # Get market details
        market = await self.kalshi.get_market_details(ticker)
        
        if not market:
            await query.edit_message_text("❌ Market not found or unavailable.")
            return
        
        title = market.get('title', 'Unknown Market')
        
        keyboard = [
            [
                InlineKeyboardButton("📈 YES", callback_data=f"pred_yes_{ticker}"),
                InlineKeyboardButton("📉 NO", callback_data=f"pred_no_{ticker}")
            ],
            [InlineKeyboardButton("🔙 Back to Markets", callback_data="refresh_markets")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = f"🎯 **Make Prediction**\n\n"
        text += f"📊 Market: {title}\n"
        text += f"🎫 Ticker: `{ticker}`\n\n"
        text += f"What's your prediction?"
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )

    async def show_leaderboard(self, query, league_id: int):
        """Show league leaderboard"""
        text = f"🏆 **League #{league_id} Leaderboard**\n\n"
        text += "📊 Leaderboard coming soon!\n"
        text += "We're implementing the scoring system.\n\n"
        text += "Features in development:\n"
        text += "• Point calculation based on accuracy\n"
        text += "• Confidence scoring\n"
        text += "• Weekly/monthly rankings\n"
        text += "• Achievement badges"
        
        keyboard = [[
            InlineKeyboardButton("🔙 Back to Leagues", callback_data="back_to_leagues")
        ]]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )

    async def refresh_markets(self, query):
        """Refresh markets display"""
        try:
            markets = await self.kalshi.get_markets(limit=10)
            
            if not markets:
                await query.edit_message_text("❌ No markets available right now.")
                return
            
            keyboard = []
            text = "📊 **Available Prediction Markets:**\n\n"
            
            for i, market in enumerate(markets[:5], 1):
                title = market.get('title', 'Unknown Market')
                ticker = market.get('ticker', 'N/A')
                yes_price = market.get('yes_bid', 0) / 100 if market.get('yes_bid') else 0
                
                text += f"{i}. **{title}**\n"
                text += f"   🎯 Ticker: `{ticker}`\n"
                text += f"   💰 Yes Price: {yes_price:.2f}¢\n\n"
                
                keyboard.append([
                    InlineKeyboardButton(
                        f"📈 Predict: {title[:30]}...", 
                        callback_data=f"predict_{ticker}"
                    )
                ])
            
            keyboard.append([
                InlineKeyboardButton("🔄 Refresh Markets", callback_data="refresh_markets")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
            
        except Exception as e:
            logger.error(f"Error refreshing markets: {e}")
            await query.edit_message_text("❌ Error loading markets.")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular text messages"""
        await update.message.reply_text(
            "🤖 I understand commands better!\n\n"
            "Try `/help` to see what I can do, or `/markets` to get started!"
        )

    async def health_check(self, request):
        """Health check endpoint for Railway"""
        return web.Response(text="OK", status=200)

    async def run(self):
        """Run the bot"""
        try:
            # Initialize database
            await self.db.connect()
            logger.info("Database connected")
            
            # Initialize bot
            await self.application.initialize()
            await self.application.start()
            
            # Start health check server for Railway
            if os.getenv('RAILWAY_ENVIRONMENT'):
                app = web.Application()
                app.router.add_get('/health', self.health_check)
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', 8080)))
                await site.start()
                logger.info(f"Health check server started on port {os.getenv('PORT', 8080)}")
            
            # Start polling
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
            await self.db.close()

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
