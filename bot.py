#!/usr/bin/env python3

import os
import logging
import asyncio
import asyncpg
import aiohttp
import json
import base64
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, MessageHandler, filters
)
from telegram.constants import ParseMode

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool = None

    async def connect(self):
        """Connect to PostgreSQL database"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=1,
                max_size=10,
                command_timeout=60
            )
            await self.create_tables()
            logger.info("Database connected successfully")
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise

    async def create_tables(self):
        """Create necessary database tables"""
        async with self.pool.acquire() as conn:
            # Users table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id BIGINT PRIMARY KEY,
                    username VARCHAR(255),
                    first_name VARCHAR(255),
                    total_score INTEGER DEFAULT 0,
                    weekly_score INTEGER DEFAULT 0,
                    predictions_made INTEGER DEFAULT 0,
                    predictions_correct INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            ''')
            
            # Leagues table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS leagues (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) UNIQUE NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            ''')
            
            # League members table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS league_members (
                    league_id INTEGER REFERENCES leagues(id),
                    user_id BIGINT REFERENCES users(id),
                    joined_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (league_id, user_id)
                );
            ''')
            
            # Markets table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS markets (
                    id VARCHAR(255) PRIMARY KEY,
                    title TEXT NOT NULL,
                    category VARCHAR(255) DEFAULT 'General',
                    close_time TIMESTAMP NOT NULL,
                    week_start DATE NOT NULL,
                    is_resolved BOOLEAN DEFAULT FALSE,
                    resolution BOOLEAN,
                    volume DECIMAL DEFAULT 0,
                    yes_price DECIMAL DEFAULT 0.5,
                    no_price DECIMAL DEFAULT 0.5,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            ''')
            
            # Predictions table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS predictions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id),
                    market_id VARCHAR(255) REFERENCES markets(id),
                    league_id INTEGER REFERENCES leagues(id) DEFAULT 1,
                    prediction BOOLEAN NOT NULL,
                    confidence INTEGER DEFAULT 1,
                    points_earned INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, market_id, league_id)
                );
            ''')

            # Weekly scores table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS weekly_scores (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id),
                    league_id INTEGER REFERENCES leagues(id),
                    week_start DATE NOT NULL,
                    score INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, league_id, week_start)
                );
            ''')

            # Create default league if it doesn't exist
            await conn.execute('''
                INSERT INTO leagues (id, name) VALUES (1, 'Global League')
                ON CONFLICT (id) DO NOTHING;
            ''')

            logger.info("Database tables created successfully")

    async def get_or_create_user(self, user_id: int, username: str, first_name: str):
        """Get or create user in database"""
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow('SELECT * FROM users WHERE id = $1', user_id)
            if not user:
                await conn.execute('''
                    INSERT INTO users (id, username, first_name) 
                    VALUES ($1, $2, $3)
                ''', user_id, username or '', first_name or '')
                
                # Add to default league
                await conn.execute('''
                    INSERT INTO league_members (league_id, user_id) 
                    VALUES (1, $1) ON CONFLICT DO NOTHING
                ''', user_id)
                
                user = await conn.fetchrow('SELECT * FROM users WHERE id = $1', user_id)
            return dict(user)

    async def get_weekly_markets(self, week_start: date) -> List[Dict]:
        """Get markets for a specific week"""
        async with self.pool.acquire() as conn:
            markets = await conn.fetch('''
                SELECT * FROM markets 
                WHERE week_start = $1 AND close_time > NOW()
                ORDER BY close_time ASC
            ''', week_start)
            return [dict(market) for market in markets]

    async def store_weekly_markets(self, markets_data: List[Dict], week_start: date):
        """Store weekly markets in database"""
        async with self.pool.acquire() as conn:
            for market in markets_data:
                close_time = market.get('close_time')
                if isinstance(close_time, str):
                    try:
                        close_time = datetime.fromisoformat(close_time.replace('Z', '+00:00'))
                    except:
                        close_time = datetime.now() + timedelta(days=7)
                elif not isinstance(close_time, datetime):
                    close_time = datetime.now() + timedelta(days=7)

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
                    market.get('ticker', market.get('id', f'DEMO_{hash(market["title"])}')),
                    market['title'],
                    market.get('category', 'General'),
                    close_time,
                    week_start,
                    float(market.get('volume', 0)),
                    float(market.get('yes_bid', market.get('yes_price', 0.5))),
                    float(market.get('no_bid', market.get('no_price', 0.5)))
                )

    async def make_prediction(self, user_id: int, market_id: str, league_id: int, prediction: bool):
        """Record a user's prediction"""
        async with self.pool.acquire() as conn:
            # Insert or update prediction
            await conn.execute('''
                INSERT INTO predictions (user_id, market_id, league_id, prediction)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, market_id, league_id) 
                DO UPDATE SET prediction = EXCLUDED.prediction, created_at = NOW()
            ''', user_id, market_id, league_id, prediction)
            
            # Update user prediction count
            await conn.execute('''
                UPDATE users SET predictions_made = predictions_made + 1 
                WHERE id = $1
            ''', user_id)

    async def get_user_predictions(self, user_id: int, market_ids: List[str]) -> Dict[str, bool]:
        """Get user's predictions for given markets"""
        if not market_ids:
            return {}
        
        async with self.pool.acquire() as conn:
            predictions = await conn.fetch('''
                SELECT market_id, prediction FROM predictions 
                WHERE user_id = $1 AND market_id = ANY($2)
            ''', user_id, market_ids)
            return {pred['market_id']: pred['prediction'] for pred in predictions}

    async def get_leaderboard(self, league_id: int = 1, limit: int = 10) -> List[Dict]:
        """Get leaderboard for league"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch('''
                SELECT u.id, u.username, u.first_name, u.total_score, 
                       u.predictions_made, u.predictions_correct,
                       CASE WHEN u.predictions_made > 0 THEN 
                           ROUND((u.predictions_correct::float / u.predictions_made * 100), 1) 
                       ELSE 0 END as accuracy
                FROM users u
                JOIN league_members lm ON u.id = lm.user_id
                WHERE lm.league_id = $1
                ORDER BY u.total_score DESC, u.predictions_correct DESC
                LIMIT $2
            ''', league_id, limit)
            
            return [dict(row) for row in results]

    async def get_user_stats(self, user_id: int) -> Dict:
        """Get comprehensive user statistics"""
        async with self.pool.acquire() as conn:
            # Basic user stats
            user_data = await conn.fetchrow('''
                SELECT *, 
                       CASE WHEN predictions_made > 0 THEN 
                           ROUND((predictions_correct::float / predictions_made * 100), 1) 
                       ELSE 0 END as accuracy
                FROM users WHERE id = $1
            ''', user_id)
            
            if not user_data:
                return {}
            
            # Recent predictions
            recent_predictions = await conn.fetch('''
                SELECT m.title, p.prediction, m.is_resolved, m.resolution, 
                       p.created_at, p.points_earned
                FROM predictions p
                JOIN markets m ON p.market_id = m.id
                WHERE p.user_id = $1
                ORDER BY p.created_at DESC
                LIMIT 5
            ''', user_id)
            
            # Weekly performance
            current_week = date.today() - timedelta(days=date.today().weekday())
            weekly_stats = await conn.fetchrow('''
                SELECT COUNT(*) as weekly_predictions,
                       SUM(CASE WHEN m.is_resolved AND p.prediction = m.resolution THEN 1 ELSE 0 END) as weekly_correct
                FROM predictions p
                JOIN markets m ON p.market_id = m.id
                WHERE p.user_id = $1 AND m.week_start = $2
            ''', user_id, current_week)
            
            return {
                'user_data': dict(user_data),
                'recent_predictions': [dict(p) for p in recent_predictions],
                'weekly_stats': dict(weekly_stats) if weekly_stats else {'weekly_predictions': 0, 'weekly_correct': 0}
            }

class KalshiAPI:
    def __init__(self, api_key: str = None, private_key: str = None):
        self.api_key = api_key
        self.private_key = private_key
        self.base_url = "https://trading-api.kalshi.com/trade-api/v2"
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
        if not self.api_key or not self.private_key:
            return False
            
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
                    logger.error(f"Kalshi login failed: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Kalshi login error: {e}")
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
                    logger.error(f"Failed to get Kalshi markets: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error getting Kalshi markets: {e}")
            return []
class FantasyLeagueBot:
    def __init__(self, token: str, database_url: str, kalshi_api_key: str = None, kalshi_private_key: str = None):
        self.token = token
        self.db = DatabaseManager(database_url)
        self.kalshi_api_key = kalshi_api_key
        self.kalshi_private_key = kalshi_private_key
        self.kalshi_available = bool(kalshi_api_key and kalshi_private_key)
        
        # Rate limiting
        self.rate_limits = {}
        self.rate_limit_window = 60
        self.rate_limit_max = 15

        # Build application
        self.application = Application.builder().token(token).build()
        self.setup_handlers()

    def setup_handlers(self):
        """Setup command and callback handlers"""
        handlers = [
            CommandHandler("start", self.start_command),
            CommandHandler("markets", self.markets_command),
            CommandHandler("leaderboard", self.leaderboard_command),
            CommandHandler("mystats", self.mystats_command),
            CommandHandler("help", self.help_command),
            CommandHandler("status", self.status_command),
            CallbackQueryHandler(self.button_handler)
        ]
        
        for handler in handlers:
            self.application.add_handler(handler)

    async def rate_limit_check(self, user_id: int) -> bool:
        """Check if user is rate limited"""
        now = datetime.now().timestamp()
        if user_id not in self.rate_limits:
            self.rate_limits[user_id] = []
        
        self.rate_limits[user_id] = [
            req_time for req_time in self.rate_limits[user_id] 
            if now - req_time < self.rate_limit_window
        ]
        
        if len(self.rate_limits[user_id]) >= self.rate_limit_max:
            return False
        
        self.rate_limits[user_id].append(now)
        return True

    def get_demo_markets(self) -> List[Dict]:
        """Get demo markets when Kalshi API is not available"""
        base_time = datetime.now()
        return [
            {
                'title': 'Will Bitcoin reach $100,000 by end of 2024?',
                'category': 'Crypto',
                'close_time': base_time + timedelta(days=30),
                'volume': 15420,
                'yes_price': 0.65,
                'no_price': 0.35
            },
            {
                'title': 'Will US GDP growth exceed 3% in Q4 2024?',
                'category': 'Economics',
                'close_time': base_time + timedelta(days=45),
                'volume': 8930,
                'yes_price': 0.42,
                'no_price': 0.58
            },
            {
                'title': 'Will any team score 50+ points in next NFL game?',
                'category': 'Sports',
                'close_time': base_time + timedelta(days=3),
                'volume': 5670,
                'yes_price': 0.28,
                'no_price': 0.72
            },
            {
                'title': 'Will Apple announce new product line in 2024?',
                'category': 'Technology',
                'close_time': base_time + timedelta(days=60),
                'volume': 12100,
                'yes_price': 0.73,
                'no_price': 0.27
            },
            {
                'title': 'Will temperature exceed 100°F in NYC this week?',
                'category': 'Weather',
                'close_time': base_time + timedelta(days=7),
                'volume': 3450,
                'yes_price': 0.15,
                'no_price': 0.85
            }
        ]

    async def fetch_and_store_weekly_markets(self) -> bool:
        """Fetch markets and store for the week"""
        try:
            today = date.today()
            week_start = today - timedelta(days=today.weekday())
            
            if self.kalshi_available:
                async with KalshiAPI(self.kalshi_api_key, self.kalshi_private_key) as kalshi:
                    markets = await kalshi.get_markets(limit=10)
                    if markets:
                        await self.db.store_weekly_markets(markets, week_start)
                        logger.info(f"Stored {len(markets)} Kalshi markets")
                        return True
            
            # Fallback to demo markets
            demo_markets = self.get_demo_markets()
            await self.db.store_weekly_markets(demo_markets, week_start)
            logger.info(f"Stored {len(demo_markets)} demo markets")
            return True
            
        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return False

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
• Earn 10 points for correct predictions
• Compete on the global leaderboard
• Track your performance over time

🚀 **Get Started:**
• View markets: /markets
• Check leaderboard: /leaderboard  
• Your stats: /mystats

Good luck predicting! 🍀"""

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

    async def markets_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show weekly markets with prediction buttons"""
        user = update.effective_user
        
        if not await self.rate_limit_check(user.id):
            return

        try:
            await self.db.get_or_create_user(user.id, user.username, user.first_name)
            
            # Get current week's markets
            today = date.today()
            week_start = today - timedelta(days=today.weekday())
            
            markets = await self.db.get_weekly_markets(week_start)
            
            if not markets:
                await self.fetch_and_store_weekly_markets()
                markets = await self.db.get_weekly_markets(week_start)
            
            if not markets:
                error_msg = "🔄 **Loading Markets...**\n\nFetching fresh prediction markets. Try again in 30 seconds!"
                if hasattr(update, 'callback_query') and update.callback_query:
                    await update.callback_query.edit_message_text(error_msg, parse_mode=ParseMode.MARKDOWN)
                else:
                    await update.message.reply_text(error_msg, parse_mode=ParseMode.MARKDOWN)
                return
            
            # Get user's existing predictions
            market_ids = [m['id'] for m in markets]
            user_predictions = await self.db.get_user_predictions(user.id, market_ids)
            
            # Build message and keyboard
            message = f"📊 **Week of {week_start.strftime('%B %d')} - Prediction Markets**\n\n"
            keyboard = []
            
            for i, market in enumerate(markets[:6], 1): # Show up to 6 markets
                title = market['title']
                if len(title) > 60:
                    title = title[:57] + "..."
                
                # Status indicator
                status_icon = ""
                if market['id'] in user_predictions:
                    pred = user_predictions[market['id']]
                    status_icon = " ✅" if pred else " ❌"
                
                # Format close time
                close_time = market['close_time']
                if isinstance(close_time, datetime):
                    time_str = close_time.strftime('%m/%d %I:%M%p')
                else:
                    time_str = "TBD"
                
                # Add market info
                message += f"**{i}. {title}**{status_icon}\n"
                message += f"📅 Closes: {time_str} | 🏷️ {market['category']}\n"
                
                # Add price info if available
                yes_price = float(market.get('yes_price', 0.5))
                message += f"💰 YES: {yes_price:.0%} | NO: {1-yes_price:.0%}\n\n"
                
                # Add prediction buttons if not predicted and not closed
                if market['id'] not in user_predictions and market['close_time'] > datetime.now():
                    keyboard.append([
                        InlineKeyboardButton(f"✅ YES #{i}", callback_data=f"predict_yes_{market['id']}"),
                        InlineKeyboardButton(f"❌ NO #{i}", callback_data=f"predict_no_{market['id']}")
                    ])
            
            # Add navigation buttons
            nav_buttons = [
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_markets")],
                [
                    InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard"),
                    InlineKeyboardButton("📈 My Stats", callback_data="mystats")
                ]
            ]
            keyboard.extend(nav_buttons)
            
            if not any(m['id'] not in user_predictions and m['close_time'] > datetime.now() for m in markets):
                message += "ℹ️ _All markets predicted or closed for this week_\n"
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Send or edit message
            if hasattr(update, 'callback_query') and update.callback_query:
                try:
                    await update.callback_query.edit_message_text(
                        message, 
                        reply_markup=reply_markup, 
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    await update.callback_query.message.reply_text(
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
            error_msg = "❌ Error loading markets. Please try again."
            
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(error_msg)
            else:
                await update.message.reply_text(error_msg)

    async def leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show global leaderboard"""
        user = update.effective_user
        
        if not await self.rate_limit_check(user.id):
            return

        try:
            leaderboard = await self.db.get_leaderboard(league_id=1, limit=10)
            
            message = "🏆 **Global Leaderboard - Top Predictors**\n\n"
            
            if not leaderboard:
                message += "No predictions made yet! Be the first to start predicting! 🎯"
            else:
                for i, player in enumerate(leaderboard, 1):
                    if i <= 3:
                        emoji = ["🥇", "🥈", "🥉"][i-1]
                    else:
                        emoji = f"{i}."
                    
                    name = player['first_name'] or player['username'] or f"User {player['id']}"
                    score = player['total_score']
                    accuracy = player['accuracy']
                    predictions = player['predictions_made']
                    
                    message += f"{emoji} **{name}**\n"
                    message += f" 🎯 {score} pts • {predictions} predictions • {accuracy}% accuracy\n\n"
                
                # Show user's rank if not in top 10
                user_in_top = any(p['id'] == user.id for p in leaderboard)
                if not user_in_top:
                    message += "📍 _Your ranking: Use /mystats to see your position_"
            
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
            stats = await self.db.get_user_stats(user.id)
            
            if not stats or not stats.get('user_data'):
                await update.message.reply_text("❌ Could not load your statistics.")
                return
            
            user_data = stats['user_data']
            recent_preds = stats.get('recent_predictions', [])
            weekly_stats = stats.get('weekly_stats', {})
            
            message = f"📈 **Your Prediction Stats**\n\n"
            message += f"👤 **Player:** {user.first_name}\n"
            message += f"🎯 **Total Score:** {user_data['total_score']} points\n"
            message += f"📊 **All-Time:** {user_data['predictions_made']} predictions, {user_data['predictions_correct']} correct\n"
            message += f"🎪 **Accuracy:** {user_data['accuracy']}%\n"
            message += f"📅 **This Week:** {weekly_stats['weekly_predictions']} predictions, {weekly_stats['weekly_correct']} correct\n\n"
            
            if recent_preds:
                message += "**🕐 Recent Predictions:**\n"
                for pred in recent_preds[:5]:
                    title = pred['title'][:35] + "..." if len(pred['title']) > 35 else pred['title']
                    pred_text = "YES" if pred['prediction'] else "NO"
                    
                    if pred['is_resolved']:
                        if pred['prediction'] == pred['resolution']:
                            status = "✅ +10pts"
                        else:
                            status = "❌ 0pts"
                    else:
                        status = "⏳ Pending"
                    
                    message += f"• {pred_text} on '{title}' {status}\n"
            else:
                message += "No predictions made yet. Start with /markets! 🎯"
            
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
            error_msg = "❌ Error loading your stats. Please try again."
            
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(error_msg)
            else:
                await update.message.reply_text(error_msg)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help message"""
        message = """🎯 **Fantasy League Bot Help**

**📚 Available Commands:**
/start - Welcome message and main menu
/markets - View this week's prediction markets
/leaderboard - See top players globally
/mystats - Your personal statistics
/help - Show this help message
/status - Check bot system status

**🎮 How to Play:**
1. Use /markets to see this week's prediction markets
2. Click YES or NO buttons to make predictions
3. Earn 10 points for each correct prediction
4. Compete on the global leaderboard
5. Track your progress with /mystats

**🏆 Scoring System:**
• Correct prediction = +10 points
• Incorrect prediction = 0 points
• Points added when markets resolve
• Weekly and all-time rankings

**💡 Pro Tips:**
• Markets close at scheduled times - predict early!
• You can only predict once per market
• New markets added weekly
• Study the odds before making predictions
• Accuracy matters as much as volume

**🛟 Need Help?**
Contact support if you encounter any issues!

Good luck with your predictions! 🍀"""

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
        """Show bot system status"""
        try:
            # Check database connection
            async with self.db.pool.acquire() as conn:
                await conn.fetchval('SELECT 1')
            db_status = "✅ Connected"
        except Exception as e:
            db_status = f"❌ Error: {str(e)[:50]}"
        
        # Check Kalshi API status
        if self.kalshi_available:
            try:
                async with KalshiAPI(self.kalshi_api_key, self.kalshi_private_key) as kalshi:
                    if await kalshi.login():
                        kalshi_status = "✅ Connected"
                    else:
                        kalshi_status = "⚠️ Login Failed"
            except:
                kalshi_status = "⚠️ Connection Error"
        else:
            kalshi_status = "⚠️ Demo Mode (No API Keys)"
        
        # Get statistics
        try:
            async with self.db.pool.acquire() as conn:
                total_users = await conn.fetchval('SELECT COUNT(*) FROM users')
                total_predictions = await conn.fetchval('SELECT COUNT(*) FROM predictions')
                active_markets = await conn.fetchval('SELECT COUNT(*) FROM markets WHERE close_time > NOW()')
                resolved_markets = await conn.fetchval('SELECT COUNT(*) FROM markets WHERE is_resolved = TRUE')
        except:
            total_users = total_predictions = active_markets = resolved_markets = 0

        message = f"""🔍 **Bot System Status**

**🔧 System Components:**
🗄️ **Database:** {db_status}
📡 **Kalshi API:** {kalshi_status}
⚡ **Bot Service:** ✅ Running
🤖 **Telegram API:** ✅ Connected

**📊 Current Statistics:**
👥 **Total Users:** {total_users}
🎯 **Active Markets:** {active_markets}
📋 **Total Predictions:** {total_predictions}
✅ **Resolved Markets:** {resolved_markets}

**🕐 Last Updated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC

**ℹ️ Version:** Fantasy League Bot v1.0"""

        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all inline button presses"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user = update.effective_user
        
        if not await self.rate_limit_check(user.id):
            await query.edit_message_text("⏰ Rate limited. Please wait a moment.")
            return
        
        try:
            if data in ["markets", "refresh_markets"]:
                # Create fake update for markets command
                fake_update = type('obj', (object,), {
                    'callback_query': query,
                    'effective_user': user,
                    'message': query.message
                })
                await self.markets_command(fake_update, context)
                
            elif data == "leaderboard":
                fake_update = type('obj', (object,), {
                    'callback_query': query,
                    'effective_user': user,
                    'message': query.message
                })
                await self.leaderboard_command(fake_update, context)
                
            elif data == "mystats":
                fake_update = type('obj', (object,), {
                    'callback_query': query,
                    'effective_user': user,
                    'message': query.message
                })
                await self.mystats_command(fake_update, context)
                
            elif data.startswith("predict_"):
                await self.handle_prediction(query, data, user)
                
            else:
                await query.edit_message_text("❌ Unknown command. Please try again.")
                
        except Exception as e:
            logger.error(f"Error in button_handler: {e}")
            try:
                await query.edit_message_text("❌ Something went wrong. Please try /start to reset.")
            except:
                await query.message.reply_text("❌ Error occurred. Please try /start to reset.")

    async def handle_prediction(self, query, data, user):
        """Handle prediction button clicks"""
        try:
            # Parse prediction data: predict_yes_MARKET_ID or predict_no_MARKET_ID
            parts = data.split('_', 2)  # Split into max 3 parts
            if len(parts) < 3:
                await query.edit_message_text("❌ Invalid prediction format.")
                return
                
            prediction_type = parts[1]  # 'yes' or 'no'
            market_id = parts[2]  # Everything after second underscore
            
            prediction = prediction_type == 'yes'
            
            # Record prediction in database
            await self.db.make_prediction(user.id, market_id, 1, prediction)  # League ID = 1 (Global)
            
            # Get market details for confirmation
            async with self.db.pool.acquire() as conn:
                market = await conn.fetchrow('SELECT * FROM markets WHERE id = $1', market_id)
            
            if not market:
                await query.edit_message_text("❌ Market not found.")
                return
            
            # Create confirmation message
            pred_text = "YES ✅" if prediction else "NO ❌"
            close_time_str = market['close_time'].strftime('%B %d, %Y at %I:%M %p')
            
            message = f"🎯 **Prediction Recorded!**\n\n"
            message += f"**Market:** {market['title'][:70]}{'...' if len(market['title']) > 70 else ''}\n\n"
            message += f"**Your Prediction:** {pred_text}\n"
            message += f"**Market Closes:** {close_time_str}\n"
            message += f"**Category:** {market['category']}\n\n"
            message += "🎉 **Good luck!** You'll earn 10 points if you're correct when this market resolves.\n\n"
            message += "💡 _Track your predictions with /mystats_"
            
            keyboard = [
                [InlineKeyboardButton("📊 View More Markets", callback_data="markets")],
                [InlineKeyboardButton("📈 My Stats", callback_data="mystats")],
                [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                message, 
                reply_markup=reply_markup, 
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Error handling prediction: {e}")
            await query.edit_message_text(
                "❌ Error recording prediction. Please try again or contact support."
            )

    async def run(self):
        """Run the bot with proper initialization"""
        try:
            logger.info("Starting Fantasy League Bot initialization...")
            
            # Connect to database first
            await self.db.connect()
            logger.info("✅ Database connected and tables created")
            
            # Set bot commands for Telegram UI
            commands = [
                BotCommand("start", "🎯 Welcome & main menu"),
                BotCommand("markets", "📊 View prediction markets"),
                BotCommand("leaderboard", "🏆 See top players"),
                BotCommand("mystats", "📈 Your statistics"),
                BotCommand("help", "❓ Help & instructions"),
                BotCommand("status", "🔍 System status")
            ]
            await self.application.bot.set_my_commands(commands)
            logger.info("✅ Bot commands set")
            
            # Initialize weekly markets if none exist
            today = date.today()
            week_start = today - timedelta(days=today.weekday())
            existing_markets = await self.db.get_weekly_markets(week_start)
            
            if not existing_markets:
                logger.info("No markets found, initializing with fresh markets...")
                success = await self.fetch_and_store_weekly_markets()
                if success:
                    logger.info("✅ Weekly markets initialized")
                else:
                    logger.warning("⚠️ Could not initialize markets, but bot will continue")
            else:
                logger.info(f"✅ Found {len(existing_markets)} existing markets for this week")
            
            # Test Kalshi connection if credentials provided
            if self.kalshi_available:
                try:
                    async with KalshiAPI(self.kalshi_api_key, self.kalshi_private_key) as kalshi:
                        if await kalshi.login():
                            logger.info("✅ Kalshi API connection successful")
                        else:
                            logger.warning("⚠️ Kalshi API login failed, using demo mode")
                            self.kalshi_available = False
                except Exception as e:
                    logger.warning(f"⚠️ Kalshi API error: {e}, using demo mode")
                    self.kalshi_available = False
            else:
                logger.info("⚠️ No Kalshi credentials provided, running in demo mode")
            
            # Start the bot
            logger.info("🚀 Starting Fantasy League Bot polling...")
            await self.application.run_polling(
                drop_pending_updates=True,
                allowed_updates=['message', 'callback_query']
            )
            
        except Exception as e:
            logger.error(f"❌ Critical error starting bot: {e}")
            raise

async def health_server():
    """Simple health check server for Railway"""
    from aiohttp import web
    
    async def health_check(request):
        return web.Response(text="Fantasy League Bot is running!", status=200)
    
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.getenv('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"✅ Health server started on port {port}")

def main():
    """Main entry point"""
    logger.info("🎯 Fantasy League Bot starting up...")
    
    # Get environment variables
    BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    DATABASE_URL = os.getenv('DATABASE_URL')
    KALSHI_API_KEY = os.getenv('KALSHI_API_KEY_ID')
    KALSHI_PRIVATE_KEY = os.getenv('KALSHI_PRIVATE_KEY_PEM')
    
    # Validate required environment variables
    if not BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN environment variable is required")
        return
    
    if not DATABASE_URL:
        logger.error("❌ DATABASE_URL environment variable is required")
        return
    
    logger.info("✅ Environment variables loaded")
    
    if KALSHI_API_KEY and KALSHI_PRIVATE_KEY:
        logger.info("✅ Kalshi API credentials found")
    else:
        logger.info("⚠️ No Kalshi credentials - will run in demo mode")
    
    # Create bot instance
    bot = FantasyLeagueBot(BOT_TOKEN, DATABASE_URL, KALSHI_API_KEY, KALSHI_PRIVATE_KEY)
    
    async def run_both():
        """Run both health server and bot"""
        # Start health server for Railway
        await health_server()
        logger.info("✅ Health server running")
        
        # Start the main bot
        await bot.run()
    
    try:
        asyncio.run(run_both())
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"💥 Bot crashed with error: {e}")
        raise

if __name__ == "__main__":
    main()
