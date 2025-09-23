import asyncio
import logging
import os
import threading
import json
import hashlib
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from fastapi import FastAPI
import uvicorn
import asyncpg
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global status tracking
bot_status = {
    "health_server": "starting",
    "database": "not_connected",
    "kalshi": "not_connected", 
    "telegram": "not_connected",
    "started_at": datetime.now().isoformat()
}

# FastAPI app for Railway health checks
app = FastAPI()

@app.get("/")
@app.get("/health")
async def health_check():
    return {
        "status": "healthy", 
        "service": "prediction_league_bot",
        "timestamp": datetime.now().isoformat(),
        "components": bot_status
    }

class DatabaseManager:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool = None
        self.max_retries = 3
        self.retry_delay = 5

    async def connect(self):
        """Connect to Railway PostgreSQL database"""
        if not self.database_url:
            raise ValueError("DATABASE_URL not provided")

        connection_kwargs = {
            'dsn': self.database_url,
            'ssl': 'require',
            'min_size': 1,
            'max_size': 5,
            'command_timeout': 60,
            'server_settings': {
                'application_name': 'prediction_league_bot',
            }
        }

        for attempt in range(self.max_retries):
            try:
                logger.info(f"Database connection attempt {attempt + 1}/{self.max_retries}")
                bot_status["database"] = "connecting"
                
                self.pool = await asyncpg.create_pool(**connection_kwargs)
                
                # Test connection
                async with self.pool.acquire() as conn:
                    await conn.fetchval('SELECT 1')
                
                logger.info("Database connected successfully")
                bot_status["database"] = "connected"
                await self.create_tables()
                return
                
            except Exception as e:
                logger.error(f"Database connection attempt {attempt + 1} failed: {e}")
                bot_status["database"] = f"failed: {str(e)[:50]}"
                
                if attempt < self.max_retries - 1:
                    logger.info(f"Retrying in {self.retry_delay} seconds...")
                    await asyncio.sleep(self.retry_delay)
                else:
                    logger.error("Max database connection retries exceeded")
                    bot_status["database"] = "failed_permanently"
                    raise

    async def disconnect(self):
        """Disconnect from database"""
        if self.pool:
            await self.pool.close()
            logger.info("Database disconnected")

    async def create_tables(self):
        """Create fantasy league database tables"""
        logger.info("Creating fantasy league database tables...")
        async with self.pool.acquire() as conn:
            # Users table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    display_name TEXT,
                    total_points INTEGER DEFAULT 0,
                    weekly_points INTEGER DEFAULT 0,
                    streak INTEGER DEFAULT 0,
                    achievements JSONB DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT NOW(),
                    last_active TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Leagues table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS leagues (
                    league_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    creator_id BIGINT,
                    is_private BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    settings JSONB DEFAULT '{}'
                )
            """)
            
            # League memberships
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS league_memberships (
                    user_id BIGINT,
                    league_id TEXT,
                    joined_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (user_id, league_id)
                )
            """)
            
            # Weekly markets
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS weekly_markets (
                    market_id TEXT PRIMARY KEY,
                    kalshi_ticker TEXT,
                    title TEXT NOT NULL,
                    category TEXT,
                    description TEXT,
                    close_time TIMESTAMP,
                    resolved BOOLEAN DEFAULT FALSE,
                    resolution TEXT,
                    week_start DATE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # User predictions
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    market_id TEXT,
                    league_id TEXT,
                    prediction BOOLEAN,
                    confidence INTEGER DEFAULT 50,
                    points_earned INTEGER DEFAULT 0,
                    is_contrarian BOOLEAN DEFAULT FALSE,
                    is_early_bird BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Leaderboards
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS leaderboards (
                    user_id BIGINT,
                    league_id TEXT,
                    week_start DATE,
                    points INTEGER DEFAULT 0,
                    correct_predictions INTEGER DEFAULT 0,
                    total_predictions INTEGER DEFAULT 0,
                    streak INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, league_id, week_start)
                )
            """)
            
            logger.info("Fantasy league database tables created successfully")

    async def create_user(self, user_id: int, username: str, display_name: str):
        """Create or update a user"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (user_id, username, display_name, last_active)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    username = $2,
                    display_name = $3,
                    last_active = NOW()
            """, user_id, username, display_name)

    async def create_league(self, league_id: str, name: str, creator_id: int, is_private: bool = False):
        """Create a new league"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO leagues (league_id, name, creator_id, is_private)
                VALUES ($1, $2, $3, $4)
            """, league_id, name, creator_id, is_private)
            
            # Add creator to league
            await conn.execute("""
                INSERT INTO league_memberships (user_id, league_id)
                VALUES ($1, $2)
            """, creator_id, league_id)

    async def join_league(self, user_id: int, league_id: str):
        """Join a league"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO league_memberships (user_id, league_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
            """, user_id, league_id)

    async def get_user_leagues(self, user_id: int):
        """Get leagues a user belongs to"""
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT l.league_id, l.name, l.is_private
                FROM leagues l
                JOIN league_memberships lm ON l.league_id = lm.league_id
                WHERE lm.user_id = $1
            """, user_id)

    async def get_weekly_markets(self, week_start: str):
        """Get markets for a specific week"""
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT * FROM weekly_markets 
                WHERE week_start = $1 
                ORDER BY category, created_at
            """, week_start)

    async def make_prediction(self, user_id: int, market_id: str, league_id: str, prediction: bool):
        """Make a prediction on a market"""
        async with self.pool.acquire() as conn:
            # Check if prediction already exists
            existing = await conn.fetchrow("""
                SELECT id FROM predictions 
                WHERE user_id = $1 AND market_id = $2 AND league_id = $3
            """, user_id, market_id, league_id)
            
            if existing:
                # Update existing prediction
                await conn.execute("""
                    UPDATE predictions 
                    SET prediction = $4, created_at = NOW()
                    WHERE user_id = $1 AND market_id = $2 AND league_id = $3
                """, user_id, market_id, league_id, prediction)
            else:
                # Create new prediction
                await conn.execute("""
                    INSERT INTO predictions (user_id, market_id, league_id, prediction)
                    VALUES ($1, $2, $3, $4)
                """, user_id, market_id, league_id, prediction)

    async def get_leaderboard(self, league_id: str, limit: int = 10):
        """Get leaderboard for a league"""
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT u.display_name, u.total_points, u.weekly_points, u.streak
                FROM users u
                JOIN league_memberships lm ON u.user_id = lm.user_id
                WHERE lm.league_id = $1
                ORDER BY u.total_points DESC
                LIMIT $2
            """, league_id, limit)

    async def get_user_stats(self, user_id: int, league_id: str = None):
        """Get user statistics"""
        async with self.pool.acquire() as conn:
            if league_id:
                return await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as total_predictions,
                        COUNT(*) FILTER (WHERE points_earned > 0) as correct_predictions,
                        SUM(points_earned) as total_points,
                        MAX(created_at) as last_prediction
                    FROM predictions 
                    WHERE user_id = $1 AND league_id = $2
                """, user_id, league_id)
            else:
                return await conn.fetchrow("""
                    SELECT total_points, weekly_points, streak, achievements
                    FROM users WHERE user_id = $1
                """, user_id)

class KalshiClient:
    def __init__(self):
        self.base_url = "https://api.elections.kalshi.com/trade-api/v2"
        self.session = None
        self.api_key_id = os.getenv('KALSHI_API_KEY_ID')
        self.private_key_pem = os.getenv('KALSHI_PRIVATE_KEY_PEM')

    async def get_markets(self, category: str = None):
        """Get markets from Kalshi API or return demo markets"""
        try:
            if not self.api_key_id or not self.private_key_pem:
                return self._get_demo_markets()
            
            # In production, implement proper Kalshi API calls here
            # For now, return demo markets
            return self._get_demo_markets()
            
        except Exception as e:
            logger.error(f"Error fetching Kalshi markets: {e}")
            return self._get_demo_markets()

    def _get_demo_markets(self):
        """Return 12 demo markets for testing"""
        current_week = datetime.now().strftime("%Y-%m-%d")
        return [
            # Sports Markets (4)
            {
                'market_id': 'SPORTS-001',
                'kalshi_ticker': 'NFL-KC-TD',
                'title': 'Will Chiefs score 3+ TDs this Sunday?',
                'category': 'Sports',
                'description': 'Kansas City Chiefs total touchdowns vs Raiders',
                'close_time': datetime.now() + timedelta(days=3),
                'week_start': current_week
            },
            {
                'market_id': 'SPORTS-002',
                'kalshi_ticker': 'NBA-LAL-WIN',
                'title': 'Will Lakers win by 5+ points tonight?',
                'category': 'Sports',
                'description': 'Lakers vs Warriors point spread',
                'close_time': datetime.now() + timedelta(hours=8),
                'week_start': current_week
            },
            {
                'market_id': 'SPORTS-003',
                'kalshi_ticker': 'MLB-WS-GAME',
                'title': 'Will World Series Game 4 go to extras?',
                'category': 'Sports',
                'description': 'World Series overtime prediction',
                'close_time': datetime.now() + timedelta(days=2),
                'week_start': current_week
            },
            {
                'market_id': 'SPORTS-004',
                'kalshi_ticker': 'NFL-TOTAL-PTS',
                'title': 'Will Sunday Night Football total exceed 50 points?',
                'category': 'Sports',
                'description': 'Combined score over/under prediction',
                'close_time': datetime.now() + timedelta(days=4),
                'week_start': current_week
            },
            # Crypto Markets (3)
            {
                'market_id': 'CRYPTO-001', 
                'kalshi_ticker': 'BTC-45K',
                'title': 'Will Bitcoin close above $45,000 Friday?',
                'category': 'Crypto',
                'description': 'Bitcoin weekly close price target',
                'close_time': datetime.now() + timedelta(days=5),
                'week_start': current_week
            },
            {
                'market_id': 'CRYPTO-002',
                'kalshi_ticker': 'ETH-3K',
                'title': 'Will Ethereum hit $3,000 this week?',
                'category': 'Crypto',
                'description': 'Ethereum price milestone',
                'close_time': datetime.now() + timedelta(days=6),
                'week_start': current_week
            },
            {
                'market_id': 'CRYPTO-003',
                'kalshi_ticker': 'SOL-100',
                'title': 'Will Solana reach $100 by Friday?',
                'category': 'Crypto',
                'description': 'Solana price target prediction',
                'close_time': datetime.now() + timedelta(days=5),
                'week_start': current_week
            },
            # Politics Markets (3)
            {
                'market_id': 'POLITICS-001',
                'kalshi_ticker': 'APPROVAL-45',
                'title': 'Will approval rating exceed 45% this week?',
                'category': 'Politics', 
                'description': 'Presidential approval rating threshold',
                'close_time': datetime.now() + timedelta(days=6),
                'week_start': current_week
            },
            {
                'market_id': 'POLITICS-002',
                'kalshi_ticker': 'SENATE-VOTE',
                'title': 'Will Senate vote pass with 60+ votes?',
                'category': 'Politics',
                'description': 'Upcoming Senate legislation',
                'close_time': datetime.now() + timedelta(days=3),
                'week_start': current_week
            },
            {
                'market_id': 'POLITICS-003',
                'kalshi_ticker': 'POLLS-LEAD',
                'title': 'Will polling lead exceed 5 points?',
                'category': 'Politics',
                'description': 'Generic ballot polling margin',
                'close_time': datetime.now() + timedelta(days=7),
                'week_start': current_week
            },
            # Finance Markets (2)
            {
                'market_id': 'FINANCE-001',
                'kalshi_ticker': 'SPY-ATH',
                'title': 'Will S&P 500 hit all-time high this week?',
                'category': 'Finance',
                'description': 'Stock market milestone prediction',
                'close_time': datetime.now() + timedelta(days=5),
                'week_start': current_week
            },
            {
                'market_id': 'FINANCE-002',
                'kalshi_ticker': 'FED-RATE',
                'title': 'Will Fed announce rate cut this month?',
                'category': 'Finance',
                'description': 'Federal Reserve monetary policy',
                'close_time': datetime.now() + timedelta(days=14),
                'week_start': current_week
            }
        ]

class FantasyLeagueBot:
    def __init__(self):
        # Environment variables
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.database_url = os.getenv('DATABASE_URL')
        
        if not all([self.bot_token, self.database_url]):
            missing = []
            if not self.bot_token:
                missing.append('TELEGRAM_BOT_TOKEN')
            if not self.database_url:
                missing.append('DATABASE_URL')
            raise ValueError(f"Missing required environment variables: {missing}")
        
        # Initialize components
        self.db = DatabaseManager(self.database_url)
        self.kalshi = KalshiClient()
        self.application = None

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        logger.info(f"Start command from user {user.id} ({user.username})")
        
        # Create user in database
        await self.db.create_user(
            user.id, 
            user.username or "unknown",
            user.first_name or "Unknown"
        )
        
        welcome_message = (
            f"🎯 **Welcome to Prediction League!** 🎯\n\n"
            f"Hey {user.first_name}! Ready to compete in the ultimate prediction market fantasy game?\n\n"
            f"**How it works:**\n"
            f"📊 Pick YES/NO on weekly markets\n"
            f"🏆 Earn points for correct predictions\n"
            f"🔥 Build streaks for bonus points\n"
            f"👥 Compete in leagues with friends\n\n"
            f"**Get started:**\n"
            f"• `/markets` - View this week's markets\n"
            f"• `/createleague [name]` - Start your own league\n"
            f"• `/joinleague [id]` - Join a friend's league\n"
            f"• `/leaderboard` - See rankings\n\n"
            f"Let's make some predictions! 🚀"
        )
        
        keyboard = [
            [InlineKeyboardButton("📊 This Week's Markets", callback_data="markets")],
            [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
            [InlineKeyboardButton("📈 My Stats", callback_data="mystats")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_message, parse_mode='Markdown', reply_markup=reply_markup)

    async def markets_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /markets command - show all 12 markets for selection"""
        user_id = update.effective_user.id
        
        # Get user's leagues first
        user_leagues = await self.db.get_user_leagues(user_id)
        if not user_leagues:
            await update.message.reply_text(
                "⚠️ You need to join a league first!\n\n"
                "Use `/createleague [name]` to start your own league\n"
                "or `/joinleague [id]` to join an existing one."
            )
            return
        
        # Get current week's markets
        markets = await self.kalshi.get_markets()
        
        if len(markets) < 12:
            await update.message.reply_text("📊 Not enough markets available this week. Check back soon!")
            return
        
        # Check if user already made selections this week
        current_week = datetime.now().strftime("%Y-%m-%d")
        league_id = user_leagues[0]['league_id']  # Use first league
        
        existing_predictions = await self.get_user_weekly_predictions(user_id, league_id, current_week)
        
        if len(existing_predictions) >= 7:
            await update.message.reply_text(
                f"✅ **Your Predictions Are Set!**\n\n"
                f"You've already made your 7 predictions for this week.\n"
                f"Check back next week for new markets!\n\n"
                f"Use `/leaderboard` to see how you're doing."
            )
            return
        
        message = f"📊 **This Week's 12 Prediction Markets** 📊\n\n"
        message += f"🎯 **Select exactly 7 markets to compete**\n"
        message += f"League: {user_leagues[0]['name']}\n\n"
        
        # Show markets by category with numbering
        categories = {'Sports': [], 'Crypto': [], 'Politics': [], 'Finance': []}
        for market in markets:
            cat = market.get('category', 'Other')
            if cat in categories:
                categories[cat].append(market)
        
        market_num = 1
        for category, cat_markets in categories.items():
            if cat_markets:
                message += f"**{category}:**\n"
                for market in cat_markets:
                    close_time = market['close_time'].strftime('%m/%d %H:%M')
                    message += f"`{market_num}.` {market['title']}\n"
                    message += f"    ⏰ Closes: {close_time}\n\n"
                    market_num += 1
        
        # Create selection keyboard - show first 6 markets
        keyboard = []
        for i, market in enumerate(markets[:6]):
            title = market['title'][:30] + "..." if len(market['title']) > 30 else market['title']
            keyboard.append([
                InlineKeyboardButton(f"✅ Select #{i+1}", callback_data=f"select_{market['market_id']}")
            ])
        
        # Add "Show More" button for markets 7-12
        keyboard.append([InlineKeyboardButton("➡️ Show Markets 7-12", callback_data="show_more_markets")])
        keyboard.append([InlineKeyboardButton("📋 Review Selections", callback_data="review_selections")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    async def get_user_weekly_predictions(self, user_id: int, league_id: str, week_start: str):
        """Get user's predictions for the current week"""
        async with self.db.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT market_id, prediction FROM predictions 
                WHERE user_id = $1 AND league_id = $2 
                AND created_at >= $3::date
                AND created_at < $3::date + INTERVAL '7 days'
            """, user_id, league_id, week_start)

    async def save_prediction_selection(self, user_id: int, market_id: str, league_id: str):
        """Save a market selection (not prediction yet)"""
        # Store in a temporary selections table or session data
        # For now, we'll use a simple in-memory storage
        if not hasattr(self, 'temp_selections'):
            self.temp_selections = {}
        
        user_key = f"{user_id}_{league_id}"
        if user_key not in self.temp_selections:
            self.temp_selections[user_key] = []
        
        if market_id not in self.temp_selections[user_key]:
            self.temp_selections[user_key].append(market_id)
        
        return len(self.temp_selections[user_key])

    async def leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /leaderboard command"""
        user_id = update.effective_user.id
        
        # Get user's leagues
        user_leagues = await self.db.get_user_leagues(user_id)
        
        if not user_leagues:
            await update.message.reply_text(
                "🏆 You haven't joined any leagues yet!\n\n"
                "Use `/createleague [name]` to start your own league\n"
                "or `/joinleague [id]` to join an existing one."
            )
            return
        
        # Show leaderboard for first league (or let user choose)
        league = user_leagues[0]
        leaderboard = await self.db.get_leaderboard(league['league_id'])
        
        message = f"🏆 **{league['name']} Leaderboard** 🏆\n\n"
        
        if not leaderboard:
            message += "No predictions made yet. Be the first!"
        else:
            for i, player in enumerate(leaderboard, 1):
                emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                streak_emoji = "🔥" if player['streak'] > 3 else ""
                message += f"{emoji} **{player['display_name']}** {streak_emoji}\n"
                message += f"   📊 {player['total_points']} pts | 🆕 {player['weekly_points']} this week\n\n"
        
        # Create navigation buttons
        keyboard = []
        if len(user_leagues) > 1:
            keyboard.append([InlineKeyboardButton("📋 Switch League", callback_data="switch_league")])
        keyboard.append([InlineKeyboardButton("📈 My Stats", callback_data="mystats")])
        keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="leaderboard")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    async def mystats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /mystats command"""
        user_id = update.effective_user.id
        
        # Get user stats
        stats = await self.db.get_user_stats(user_id)
        
        if not stats:
            await update.message.reply_text("📈 No stats yet! Make some predictions to get started.")
            return
        
        # Calculate accuracy
        total_points = stats.get('total_points', 0)
        weekly_points = stats.get('weekly_points', 0) 
        streak = stats.get('streak', 0)
        achievements = stats.get('achievements', [])
        
        message = f"📈 **Your Stats** 📈\n\n"
        message += f"🎯 **Total Points:** {total_points}\n"
        message += f"📅 **This Week:** {weekly_points} points\n"
        message += f"🔥 **Current Streak:** {streak}\n\n"
        
        if achievements:
            message += f"🏅 **Achievements:** {len(achievements)}\n"
            for achievement in achievements[:3]:  # Show first 3
                message += f"   • {achievement}\n"
            if len(achievements) > 3:
                message += f"   ... and {len(achievements) - 3} more!\n"
        else:
            message += f"🏅 **Achievements:** None yet - keep predicting!\n"
        
        keyboard = [
            [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
            [InlineKeyboardButton("📊 Markets", callback_data="markets")],
            [InlineKeyboardButton("🏅 All Achievements", callback_data="achievements")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    async def createleague_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /createleague command"""
        user_id = update.effective_user.id
        
        if not context.args:
            await update.message.reply_text(
                "🏆 **Create a League**\n\n"
                "Usage: `/createleague [name]`\n\n"
                "Example: `/createleague Friends Fantasy`"
            )
            return
        
        league_name = " ".join(context.args)
        league_id = f"league_{hashlib.md5(f'{user_id}_{league_name}_{time.time()}'.encode()).hexdigest()[:8]}"
        
        try:
            await self.db.create_league(league_id, league_name, user_id, is_private=True)
            
            message = (
                f"🎉 **League Created!** 🎉\n\n"
                f"**League:** {league_name}\n"
                f"**ID:** `{league_id}`\n\n"
                f"Share this ID with friends so they can join:\n"
                f"`/joinleague {league_id}`\n\n"
                f"Ready to start making predictions! 🚀"
            )
            
            keyboard = [
                [InlineKeyboardButton("📊 View Markets", callback_data="markets")],
                [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error creating league: {e}")
            await update.message.reply_text("❌ Error creating league. Please try again.")

    async def joinleague_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /joinleague command"""
        user_id = update.effective_user.id
        
        if not context.args:
            await update.message.reply_text(
                "👥 **Join a League**\n\n"
                "Usage: `/joinleague [league_id]`\n\n"
                "Get the league ID from a friend who created the league."
            )
            return
        
        league_id = context.args[0]
        
        try:
            await self.db.join_league(user_id, league_id)
            
            message = (
                f"🎉 **Joined League!** 🎉\n\n"
                f"Welcome to the league! Start making predictions to climb the leaderboard.\n\n"
                f"Ready to compete! 🚀"
            )
            
            keyboard = [
                [InlineKeyboardButton("📊 View Markets", callback_data="markets")],
                [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error joining league: {e}")
            await update.message.reply_text("❌ League not found or error joining. Check the league ID.")

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button presses"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = update.effective_user.id
        
        if data == "markets":
            await self.markets_command(update, context)
        elif data == "leaderboard":
            await self.leaderboard_command(update, context)
        elif data == "mystats":
            await self.mystats_command(update, context)
        elif data.startswith("select_"):
            # Handle market selection for weekly picks
            market_id = data.split("select_")[1]
            
            # Get user's league
            user_leagues = await self.db.get_user_leagues(user_id)
            if not user_leagues:
                await query.edit_message_text("❌ You need to join a league first!")
                return
            
            league_id = user_leagues[0]['league_id']
            selections_count = await self.save_prediction_selection(user_id, market_id, league_id)
            
            if selections_count >= 7:
                # User has selected 7 markets, now show prediction interface
                await self.show_prediction_interface(query, user_id, league_id)
            else:
                # Update the message to show selection progress
                await query.edit_message_text(
                    f"✅ **Market Selected!** ({selections_count}/7)\n\n"
                    f"Keep selecting markets until you have 7 total.\n"
                    f"Use `/markets` to continue selecting.",
                    parse_mode='Markdown'
                )
        
        elif data == "show_more_markets":
            # Show markets 7-12
            await self.show_more_markets(query, user_id)
        
        elif data == "review_selections":
            # Show current selections and allow prediction setting
            await self.review_selections(query, user_id)
        
        elif data.startswith("predict_"):
            # Handle final YES/NO predictions
            parts = data.split("_")
            prediction = parts[1] == "yes"
            market_id = "_".join(parts[2:])
            
            user_leagues = await self.db.get_user_leagues(user_id)
            if not user_leagues:
                await query.edit_message_text("❌ You need to join a league first!")
                return
            
            league_id = user_leagues[0]['league_id']
            
            try:
                await self.db.make_prediction(user_id, market_id, league_id, prediction)
                
                pred_text = "👍 YES" if prediction else "👎 NO"
                await query.edit_message_text(
                    f"✅ **Prediction Recorded!**\n\n"
                    f"Your pick: {pred_text}\n"
                    f"Market: {market_id}\n\n"
                    f"Good luck! 🍀",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Error making prediction: {e}")
                await query.edit_message_text("❌ Error recording prediction. Please try again.")

    async def show_more_markets(self, query, user_id: int):
        """Show markets 7-12"""
        markets = await self.kalshi.get_markets()
        
        message = "📊 **Markets 7-12** 📊\n\n"
        
        for i, market in enumerate(markets[6:12], 7):
            close_time = market['close_time'].strftime('%m/%d %H:%M')
            message += f"`{i}.` {market['title']}\n"
            message += f"    ⏰ Closes: {close_time}\n\n"
        
        # Create selection keyboard for markets 7-12
        keyboard = []
        for i, market in enumerate(markets[6:12], 7):
            title = market['title'][:30] + "..." if len(market['title']) > 30 else market['title']
            keyboard.append([
                InlineKeyboardButton(f"✅ Select #{i}", callback_data=f"select_{market['market_id']}")
            ])
        
        keyboard.append([InlineKeyboardButton("⬅️ Back to Markets 1-6", callback_data="markets")])
        keyboard.append([InlineKeyboardButton("📋 Review Selections", callback_data="review_selections")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    async def review_selections(self, query, user_id: int):
        """Show current selections and allow setting predictions"""
        user_leagues = await self.db.get_user_leagues(user_id)
        if not user_leagues:
            await query.edit_message_text("❌ You need to join a league first!")
            return
        
        league_id = user_leagues[0]['league_id']
        user_key = f"{user_id}_{league_id}"
        
        if not hasattr(self, 'temp_selections') or user_key not in self.temp_selections:
            await query.edit_message_text(
                "📋 **No Markets Selected Yet**\n\n"
                "Use `/markets` to select your 7 markets for this week.",
                parse_mode='Markdown'
            )
            return
        
        selections = self.temp_selections[user_key]
        markets = await self.kalshi.get_markets()
        market_dict = {m['market_id']: m for m in markets}
        
        message = f"📋 **Your Selected Markets** ({len(selections)}/7)\n\n"
        
        for i, market_id in enumerate(selections, 1):
            if market_id in market_dict:
                market = market_dict[market_id]
                message += f"`{i}.` {market['title']}\n"
        
        if len(selections) < 7:
            message += f"\n⚠️ You need {7 - len(selections)} more selections."
            keyboard = [[InlineKeyboardButton("📊 Continue Selecting", callback_data="markets")]]
        else:
            message += "\n✅ **Ready to make predictions!**\n"
            message += "Click below to set your YES/NO predictions:"
            keyboard = [[InlineKeyboardButton("🎯 Set Predictions", callback_data="set_predictions")]]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    async def show_prediction_interface(self, query, user_id: int, league_id: str):
        """Show interface to set YES/NO predictions on selected markets"""
        user_key = f"{user_id}_{league_id}"
        selections = self.temp_selections.get(user_key, [])
        
        if len(selections) != 7:
            await query.edit_message_text("❌ You must select exactly 7 markets first!")
            return
        
        markets = await self.kalshi.get_markets()
        market_dict = {m['market_id']: m for m in markets}
        
        # Show first market for prediction
        market_id = selections[0]
        market = market_dict.get(market_id)
        
        if not market:
            await query.edit_message_text("❌ Error loading market. Please try again.")
            return
        
        message = (
            f"🎯 **Set Your Predictions** (1/7)\n\n"
            f"**{market['title']}**\n\n"
            f"📂 {market['category']}\n"
            f"⏰ Closes: {market['close_time'].strftime('%m/%d %H:%M')}\n\n"
            f"What's your prediction?"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("👍 YES", callback_data=f"predict_yes_{market_id}"),
                InlineKeyboardButton("👎 NO", callback_data=f"predict_no_{market_id}")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, parse_mode='Markdown', reply_markup=reply_markup)

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Exception while handling an update: {context.error}")

    async def run(self):
        """Main bot runner"""        
        try:
            # Connect to database
            await self.db.connect()
            logger.info("Database connected successfully")
            
            # Create Telegram application
            self.application = Application.builder().token(self.bot_token).build()
            
            # Add handlers
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("markets", self.markets_command))
            self.application.add_handler(CommandHandler("leaderboard", self.leaderboard_command))
            self.application.add_handler(CommandHandler("mystats", self.mystats_command))
            self.application.add_handler(CommandHandler("createleague", self.createleague_command))
            self.application.add_handler(CommandHandler("joinleague", self.joinleague_command))
            self.application.add_handler(CallbackQueryHandler(self.button_handler))
            self.application.add_error_handler(self.error_handler)
            
            # Initialize and start the application
            await self.application.initialize()
            await self.application.start()
            
            bot_status["telegram"] = "connected"
            
            # Start polling
            logger.info("Starting fantasy league bot polling...")
            await self.application.updater.start_polling(
                poll_interval=1.0,
                timeout=20,
                bootstrap_retries=3,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30
            )
            
            # Keep running
            logger.info("Fantasy league bot started successfully and is running...")
            
            # Keep the main thread alive
            while True:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Bot startup error: {e}")
            bot_status["telegram"] = f"failed: {str(e)[:50]}"
            raise
        finally:
            await self.cleanup()

    async def cleanup(self):
        """Clean shutdown"""
        logger.info("Shutting down fantasy league bot...")
        
        try:
            if self.application:
                if hasattr(self.application, 'updater') and self.application.updater.running:
                    await self.application.updater.stop()
                if self.application.running:
                    await self.application.stop()
                await self.application.shutdown()
                    
            await self.db.disconnect()
            logger.info("Fantasy league bot shutdown complete")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

def run_health_server():
    """Run health server for Railway"""
    try:
        port = int(os.getenv('PORT', 8080))
        logger.info(f"Starting health server on port {port}")
        bot_status["health_server"] = "running"
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
    except Exception as e:
        logger.error(f"Health server failed: {e}")
        bot_status["health_server"] = f"failed: {e}"

async def main():
    """Main entry point"""
    logger.info("Starting Fantasy League Bot on Railway...")
    
    # Start health server immediately in background
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Give health server time to start
    await asyncio.sleep(2)
    logger.info("Health server started, now starting fantasy league bot...")
    
    try:
        bot = FantasyLeagueBot()
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Fantasy league bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        bot_status["telegram"] = f"fatal_error: {str(e)[:50]}"
        
        # Keep health server running even if bot fails
        logger.info("Bot failed, keeping health server alive")
        while True:
            await asyncio.sleep(60)
            logger.error("Bot failed but health server still running")

if __name__ == "__main__":
    asyncio.run(main())
