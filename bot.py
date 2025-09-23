import asyncio
import logging
import os
import threading
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from fastapi import FastAPI
import uvicorn
import asyncpg
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
        "service": "kalshi_bot",
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
                'application_name': 'railway_kalshi_bot',
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
        """Create necessary tables"""
        logger.info("Creating database tables...")
        async with self.pool.acquire() as conn:
            # Create user_portfolios table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_portfolios (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    portfolio_data JSONB,
                    last_updated TIMESTAMP DEFAULT NOW()
                )
            """)
            logger.info("Created user_portfolios table")
            
            # Create user_settings table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id BIGINT PRIMARY KEY,
                    notifications_enabled BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            logger.info("Created user_settings table")
            
            # Create bot_logs table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_logs (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    command TEXT,
                    timestamp TIMESTAMP DEFAULT NOW(),
                    success BOOLEAN DEFAULT TRUE
                )
            """)
            logger.info("Created bot_logs table")
            
            # Verify tables exist
            tables = await conn.fetch("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)
            table_names = [row['table_name'] for row in tables]
            logger.info(f"Tables in database: {table_names}")
            
            logger.info("Database tables created successfully")

    async def get_user_portfolio(self, user_id: int) -> Optional[Dict]:
        """Get user portfolio from database"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT portfolio_data FROM user_portfolios WHERE user_id = $1",
                user_id
            )
            return row['portfolio_data'] if row else None

    async def save_user_portfolio(self, user_id: int, username: str, portfolio_data: Dict):
        """Save user portfolio to database"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO user_portfolios (user_id, username, portfolio_data, last_updated)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    username = $2,
                    portfolio_data = $3,
                    last_updated = NOW()
            """, user_id, username, json.dumps(portfolio_data))

    async def log_command(self, user_id: int, command: str, success: bool = True):
        """Log bot command usage"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO bot_logs (user_id, command, success)
                VALUES ($1, $2, $3)
            """, user_id, command, success)

class KalshiBot:
    def __init__(self):
        # Environment variables
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.database_url = os.getenv('DATABASE_URL')
        
        # New Kalshi API credentials (optional - bot can work without them)
        self.kalshi_api_key_id = os.getenv('KALSHI_API_KEY_ID')
        self.kalshi_private_key_pem = os.getenv('KALSHI_PRIVATE_KEY_PEM')
        
        # Check required variables (only Telegram and Database are mandatory)
        if not all([self.bot_token, self.database_url]):
            missing = []
            if not self.bot_token:
                missing.append('TELEGRAM_BOT_TOKEN')
            if not self.database_url:
                missing.append('DATABASE_URL')
            raise ValueError(f"Missing required environment variables: {missing}")
        
        # Initialize components
        self.db = DatabaseManager(self.database_url)
        self.kalshi_client = None
        
    async def initialize_kalshi(self):
        """Initialize Kalshi client with proper API key authentication"""
        try:
            logger.info("Initializing Kalshi client...")
            bot_status["kalshi"] = "connecting"
            
            # Use the new kalshi_python API structure from PyPI
            from kalshi_python import Configuration, KalshiClient
            
            # Check for required environment variables
            api_key_id = self.kalshi_api_key_id
            private_key_pem = self.kalshi_private_key_pem
            
            if not api_key_id or not private_key_pem:
                logger.error("Missing KALSHI_API_KEY_ID or KALSHI_PRIVATE_KEY_PEM environment variables")
                logger.info("Please set up API key authentication instead of email/password")
                logger.info("Visit https://kalshi.com/profile/api to generate API credentials")
                bot_status["kalshi"] = "missing_api_credentials"
                self.kalshi_client = None
                return
            
            # Configure the client with new API endpoint and authentication
            config = Configuration(
                host="https://api.elections.kalshi.com/trade-api/v2"
            )
            config.api_key_id = api_key_id
            config.private_key_pem = private_key_pem
            
            # Initialize the client
            self.kalshi_client = KalshiClient(config)
            
            # Test the connection by getting balance
            try:
                balance = self.kalshi_client.get_balance()
                logger.info(f"Kalshi client initialized successfully. Balance: ${balance.balance / 100:.2f}")
                bot_status["kalshi"] = "connected"
            except Exception as test_error:
                logger.error(f"Kalshi client created but test call failed: {test_error}")
                bot_status["kalshi"] = f"auth_failed: {str(test_error)[:50]}"
                self.kalshi_client = None
            
        except ImportError as e:
            logger.error(f"Failed to import kalshi_python: {e}")
            logger.info("Make sure you have the latest kalshi-python package installed")
            bot_status["kalshi"] = "import_failed"
            self.kalshi_client = None
        except Exception as e:
            logger.error(f"Failed to initialize Kalshi client: {e}")
            bot_status["kalshi"] = f"failed: {str(e)[:50]}"
            self.kalshi_client = None

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        logger.info(f"Start command from user {user.id} ({user.username})")
        
        await self.db.log_command(user.id, "start")
        
        welcome_message = (
            f"Hello {user.first_name}! 👋\n\n"
            "I'm your Kalshi trading bot. I can help you:\n"
            "• View your portfolio 📊\n"
            "• Check market data 📈\n"
            "• Get trading insights 💡\n"
            "• Monitor your positions 👀\n\n"
            "Use /help to see all available commands."
        )
        
        keyboard = [
            [InlineKeyboardButton("📊 Portfolio", callback_data="portfolio")],
            [InlineKeyboardButton("📈 Markets", callback_data="markets")],
            [InlineKeyboardButton("💰 Balance", callback_data="balance")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_message, reply_markup=reply_markup)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        await self.db.log_command(update.effective_user.id, "help")
        
        help_text = (
            "🤖 **Kalshi Bot Commands**\n\n"
            "📊 `/portfolio` - View your portfolio\n"
            "📈 `/markets` - Browse available markets\n"
            "💰 `/balance` - Check your account balance\n"
            "📊 `/positions` - View your current positions\n"
            "🔄 `/refresh` - Refresh your data\n"
            "⚙️ `/settings` - Bot settings\n"
            "📊 `/stats` - Bot usage statistics\n"
            "❓ `/help` - Show this help message\n\n"
            "You can also use the inline buttons for quick access!"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def portfolio_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /portfolio command"""
        user_id = update.effective_user.id
        
        try:
            await self.db.log_command(user_id, "portfolio")
            
            if not self.kalshi_client:
                await update.message.reply_text(
                    "⚠️ Kalshi connection not available. Please check /status for details."
                )
                return
            
            # Get portfolio from Kalshi using new API
            portfolio = self.kalshi_client.get_portfolio()
            
            if not portfolio or not hasattr(portfolio, 'positions'):
                await update.message.reply_text("📊 No portfolio data available.")
                return
            
            # Save to database
            portfolio_dict = {
                'positions': [pos.__dict__ if hasattr(pos, '__dict__') else pos for pos in portfolio.positions] if portfolio.positions else [],
                'balance': portfolio.balance if hasattr(portfolio, 'balance') else 0
            }
            
            await self.db.save_user_portfolio(
                user_id, 
                update.effective_user.username or "unknown",
                portfolio_dict
            )
            
            # Format portfolio information
            message = "📊 **Your Portfolio:**\n\n"
            
            if portfolio.positions and len(portfolio.positions) > 0:
                total_value = 0
                for i, position in enumerate(portfolio.positions[:10]):  # Show first 10
                    market_ticker = getattr(position, 'market_ticker', 'Unknown')
                    position_count = getattr(position, 'position', 0)
                    
                    message += f"📈 **{market_ticker}**\n"
                    message += f"   Position: {position_count}\n\n"
                
                if len(portfolio.positions) > 10:
                    message += f"... and {len(portfolio.positions) - 10} more positions\n"
            else:
                message += "No active positions found."
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error fetching portfolio: {e}")
            await self.db.log_command(user_id, "portfolio", False)
            await update.message.reply_text(
                "❌ Sorry, I couldn't fetch your portfolio right now. Please try again later."
            )

    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /balance command"""
        user_id = update.effective_user.id
        
        try:
            await self.db.log_command(user_id, "balance")
            
            if not self.kalshi_client:
                await update.message.reply_text(
                    "⚠️ Kalshi connection not available. Please check /status for details."
                )
                return
            
            # Get balance from Kalshi using new API
            balance_response = self.kalshi_client.get_balance()
            
            if balance_response and hasattr(balance_response, 'balance'):
                balance_cents = balance_response.balance
                balance_dollars = balance_cents / 100
                message = f"💰 **Your Balance:** ${balance_dollars:.2f}"
            else:
                message = "❌ Unable to fetch balance information."
            
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            await self.db.log_command(user_id, "balance", False)
            await update.message.reply_text(
                "❌ Sorry, I couldn't fetch your balance right now. Please try again later."
            )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        await self.db.log_command(update.effective_user.id, "status")
        
        # Check component statuses
        db_status = "✅ Connected" if self.db.pool else "❌ Disconnected"
        kalshi_status = "✅ Connected" if self.kalshi_client else "❌ Disconnected"
        telegram_status = "✅ Connected"  # If we're here, Telegram is working
        
        message = (
            "🤖 **Bot Status:**\n\n"
            f"📊 Database: {db_status}\n"
            f"📈 Kalshi API: {kalshi_status}\n"
            f"💬 Telegram: {telegram_status}\n"
            f"🕒 Uptime: Running on Railway\n"
            f"📅 Started: {bot_status['started_at'][:19]}"
        )
        
        await update.message.reply_text(message, parse_mode='Markdown')

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button presses"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "portfolio":
            await self.portfolio_command(update, context)
        elif query.data == "balance":
            await self.balance_command(update, context)
        elif query.data == "help":
            await self.help_command(update, context)
        elif query.data == "markets":
            await query.edit_message_text("📈 Markets feature coming soon!")

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Exception while handling an update: {context.error}")

    async def run(self):
        """Main bot runner"""        
        try:
            # Connect to database
            await self.db.connect()
            logger.info("Database connected successfully")
            
            # Initialize Kalshi client (don't fail if this doesn't work)
            await self.initialize_kalshi()
            
            # Create Telegram application
            self.application = Application.builder().token(self.bot_token).build()
            
            # Add handlers
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("portfolio", self.portfolio_command))
            self.application.add_handler(CommandHandler("balance", self.balance_command))
            self.application.add_handler(CommandHandler("status", self.status_command))
            self.application.add_handler(CallbackQueryHandler(self.button_handler))
            self.application.add_error_handler(self.error_handler)
            
            # Initialize and start the application
            await self.application.initialize()
            await self.application.start()
            
            bot_status["telegram"] = "connected"
            
            # Start polling
            logger.info("Starting bot polling...")
            await self.application.updater.start_polling(
                poll_interval=1.0,
                timeout=20,
                bootstrap_retries=3,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30
            )
            
            # Keep running
            logger.info("Bot started successfully and is running...")
            
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
        logger.info("Shutting down bot...")
        
        try:
            if self.application:
                if hasattr(self.application, 'updater') and self.application.updater.running:
                    await self.application.updater.stop()
                if self.application.running:
                    await self.application.stop()
                await self.application.shutdown()
                    
            await self.db.disconnect()
            logger.info("Bot shutdown complete")
            
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
    logger.info("Starting Kalshi Bot on Railway...")
    
    # Start health server immediately in background
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Give health server time to start
    await asyncio.sleep(2)
    logger.info("Health server started, now starting bot...")
    
    try:
        bot = KalshiBot()
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
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
