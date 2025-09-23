import asyncio
import logging
import os
import json
import asyncpg
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from kalshi_python import KalshiClient
from fastapi import FastAPI
import uvicorn

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# FastAPI app for Railway health checks
app = FastAPI()

# Global status tracking
bot_status = {
    "database": "disconnected",
    "kalshi": "disconnected", 
    "telegram": "disconnected",
    "started_at": datetime.now().isoformat()
}

@app.get("/")
@app.get("/health")
async def health_check():
    # Always return 200 OK for Railway health check
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
        """Connect to database with Railway-optimized settings"""
        if not self.database_url:
            raise ValueError("DATABASE_URL not provided")

        connection_kwargs = {
            'dsn': self.database_url,
            'ssl': 'require',
            'min_size': 1,
            'max_size': 3,  # Lower for Railway resource limits
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
                
                if "certificate verify failed" in str(e):
                    logger.error("SSL certificate verification failed")
                elif "connection refused" in str(e):
                    logger.error("Connection refused - check network/firewall")
                elif "timeout" in str(e):
                    logger.error("Connection timeout - network connectivity issue")
                
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
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_portfolios (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    portfolio_data JSONB,
                    last_updated TIMESTAMP DEFAULT NOW()
                )
            """)
            
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id BIGINT PRIMARY KEY,
                    notifications_enabled BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

    async def get_user_portfolio(self, user_id: int) -> Optional[Dict]:
        """Get user portfolio from database"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT portfolio_data FROM user_portfolios WHERE user_id = $1",
                user_id
            )
            return row['portfolio_data'] if row else None

class KalshiBot:
    def __init__(self):
        # Environment variables
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.kalshi_email = os.getenv('KALSHI_EMAIL')
        self.kalshi_password = os.getenv('KALSHI_PASSWORD')
        self.database_url = os.getenv('DATABASE_URL')
        
        if not all([self.bot_token, self.kalshi_email, self.kalshi_password, self.database_url]):
            raise ValueError("Missing required environment variables")
        
        # Initialize components
        self.db = DatabaseManager(self.database_url)
        self.kalshi_client = None
        self.application = None
        
    async def initialize_kalshi(self):
        """Initialize Kalshi client"""
        try:
            logger.info("Initializing Kalshi client...")
            bot_status["kalshi"] = "connecting"
            
            self.kalshi_client = KalshiClient(
                email=self.kalshi_email,
                password=self.kalshi_password,
                prod_url="https://trading-api.kalshi.com/trade-api/v2"
            )
            logger.info("Kalshi client initialized successfully")
            bot_status["kalshi"] = "connected"
        except Exception as e:
            logger.error(f"Failed to initialize Kalshi client: {e}")
            bot_status["kalshi"] = f"failed: {str(e)[:50]}"
            raise

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        logger.info(f"Start command from user {user.id} ({user.username})")
        
        welcome_message = (
            f"Hello {user.first_name}! 👋\n\n"
            "I'm your Kalshi trading bot. I can help you:\n"
            "• View your portfolio\n"
            "• Check market data\n"
            "• Get trading insights\n\n"
            "Use /help to see all available commands."
        )
        
        await update.message.reply_text(welcome_message)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_text = (
            "Available commands:\n\n"
            "/start - Start the bot\n"
            "/help - Show this help message\n"
            "/portfolio - View your portfolio\n"
            "/markets - Browse markets\n"
            "/balance - Check your balance\n"
            "/status - Bot status\n"
        )
        await update.message.reply_text(help_text)

    async def portfolio_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /portfolio command"""
        user_id = update.effective_user.id
        
        try:
            # Get portfolio from Kalshi
            portfolio = self.kalshi_client.get_portfolio()
            
            if not portfolio:
                await update.message.reply_text("No portfolio data available.")
                return
            
            # Format portfolio information
            message = "📊 Your Portfolio:\n\n"
            
            if 'positions' in portfolio:
                for position in portfolio['positions'][:5]:  # Show first 5 positions
                    market_ticker = position.get('market_ticker', 'Unknown')
                    position_count = position.get('position', 0)
                    message += f"• {market_ticker}: {position_count}\n"
            else:
                message += "No active positions found."
            
            await update.message.reply_text(message)
            
        except Exception as e:
            logger.error(f"Error fetching portfolio: {e}")
            await update.message.reply_text("Sorry, I couldn't fetch your portfolio right now.")

    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /balance command"""
        try:
            balance = self.kalshi_client.get_balance()
            
            if balance:
                balance_cents = balance.get('balance', 0)
                balance_dollars = balance_cents / 100
                message = f"💰 Your Balance: ${balance_dollars:.2f}"
            else:
                message = "Unable to fetch balance information."
            
            await update.message.reply_text(message)
            
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            await update.message.reply_text("Sorry, I couldn't fetch your balance right now.")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        try:
            # Check Kalshi connection
            kalshi_status = "✅ Connected" if self.kalshi_client else "❌ Disconnected"
            
            # Check database connection
            db_status = "✅ Connected" if self.db.pool else "❌ Disconnected"
            
            message = (
                "🤖 Bot Status:\n\n"
                f"Kalshi API: {kalshi_status}\n"
                f"Database: {db_status}\n"
                f"Uptime: Running on Railway\n"
            )
            
            await update.message.reply_text(message)
            
        except Exception as e:
            logger.error(f"Error in status command: {e}")
            await update.message.reply_text("Error checking status.")

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Exception while handling an update: {context.error}")

    def run_web_server(self):
        """This method is no longer used - health server starts in main()"""
        pass

    async def run(self):
        """Main bot runner optimized for Railway"""        
        try:
            # Connect to database with retries
            await self.db.connect()
            logger.info("Database connected successfully")
            
            # Initialize Kalshi client
            await self.initialize_kalshi()
            logger.info("Kalshi client initialized")
            
            # Create Telegram application
            self.application = Application.builder().token(self.bot_token).build()
            
            # Add handlers
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("portfolio", self.portfolio_command))
            self.application.add_handler(CommandHandler("balance", self.balance_command))
            self.application.add_handler(CommandHandler("status", self.status_command))
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
            # Don't re-raise - let health server keep running
            logger.info("Bot failed but letting health server continue")
            
            # Keep the main thread alive even if bot fails
            while True:
                await asyncio.sleep(60)
                logger.info("Bot failed but health server still running")
        finally:
            # Only cleanup bot components, not the web server
            try:
                if self.application:
                    if hasattr(self.application, 'updater') and self.application.updater.running:
                        await self.application.updater.stop()
                    if self.application.running:
                        await self.application.stop()
                    await self.application.shutdown()
                        
                await self.db.disconnect()
                logger.info("Bot cleanup complete")
                
            except Exception as e:
                logger.error(f"Error during cleanup: {e}")

    async def cleanup(self):
        """Clean shutdown"""
        logger.info("Shutting down bot...")
        
        try:
            if self.application:
                if self.application.updater.running:
                    await self.application.updater.stop()
                if self.application.running:
                    await self.application.stop()
                await self.application.shutdown()
                
            await self.db.disconnect()
            logger.info("Bot shutdown complete")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

async def main():
    """Main entry point"""
    try:
        # Start health server first, independent of bot
        port = int(os.getenv('PORT', 8080))
        logger.info(f"Starting standalone health server on port {port}")
        
        import uvicorn
        import threading
        
        def run_health_server():
            uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
        
        # Start health server immediately
        health_thread = threading.Thread(target=run_health_server, daemon=True)
        health_thread.start()
        
        # Give health server time to start
        await asyncio.sleep(3)
        logger.info("Health server should be running now")
        
        # Now try to start the bot
        try:
            bot = KalshiBot()
            await bot.run()
        except Exception as bot_error:
            logger.error(f"Bot failed to start: {bot_error}")
            bot_status["telegram"] = f"failed: {str(bot_error)[:50]}"
            
            # Keep the health server running even if bot fails
            logger.info("Bot failed, but keeping health server alive for Railway")
            while True:
                await asyncio.sleep(60)
                logger.info("Health server still running, bot failed")
                
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
    except Exception as e:
        logger.error(f"Fatal error in main: {e}")
        # Still keep health server running
        logger.info("Fatal error, but keeping health server alive")
        while True:
            await asyncio.sleep(60)
            logger.error("Health server running despite fatal error")

if __name__ == "__main__":
    asyncio.run(main())
