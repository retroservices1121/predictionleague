import asyncio
import logging
import os
import threading
from datetime import datetime
from fastapi import FastAPI
import uvicorn

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global status
app_status = {
    "health_server": "starting",
    "bot": "not_started",
    "database": "not_connected",
    "started_at": datetime.now().isoformat()
}

# FastAPI app - this starts immediately
app = FastAPI()

@app.get("/")
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "components": app_status
    }

def run_health_server():
    """Run health server - this must work"""
    try:
        port = int(os.getenv('PORT', 8080))
        logger.info(f"Starting health server on port {port}")
        app_status["health_server"] = "running"
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
    except Exception as e:
        logger.error(f"Health server failed: {e}")
        app_status["health_server"] = f"failed: {e}"

async def try_bot_components():
    """Try to start bot components - failures here won't kill health server"""
    try:
        logger.info("Testing environment variables...")
        
        # Check environment variables
        required_vars = ['TELEGRAM_BOT_TOKEN', 'KALSHI_EMAIL', 'KALSHI_PASSWORD', 'DATABASE_URL']
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        
        if missing_vars:
            error_msg = f"Missing environment variables: {missing_vars}"
            logger.error(error_msg)
            app_status["bot"] = f"env_error: {error_msg}"
            return
        
        logger.info("Environment variables OK")
        app_status["bot"] = "env_ok"
        
        # Test database connection
        logger.info("Testing database connection...")
        import asyncpg
        
        try:
            conn = await asyncpg.connect(
                os.getenv('DATABASE_URL'),
                ssl='require',
                command_timeout=30
            )
            await conn.fetchval('SELECT 1')
            await conn.close()
            logger.info("Database connection successful")
            app_status["database"] = "connected"
        except Exception as db_error:
            logger.error(f"Database connection failed: {db_error}")
            app_status["database"] = f"failed: {str(db_error)[:100]}"
        
        # Test Kalshi client
        logger.info("Testing Kalshi client...")
        try:
            # Try different import patterns for kalshi-python
            try:
                from kalshi_python.kalshi_client import KalshiClient
            except ImportError:
                try:
                    from kalshi_python import kalshi_client
                    KalshiClient = kalshi_client.KalshiClient
                except ImportError:
                    import kalshi_python
                    KalshiClient = kalshi_python.KalshiClient
            
            client = KalshiClient(
                email=os.getenv('KALSHI_EMAIL'),
                password=os.getenv('KALSHI_PASSWORD'),
                prod_url="https://trading-api.kalshi.com/trade-api/v2"
            )
            logger.info("Kalshi client initialized")
            app_status["bot"] = "kalshi_ok"
        except Exception as kalshi_error:
            logger.error(f"Kalshi client failed: {kalshi_error}")
            app_status["bot"] = f"kalshi_failed: {str(kalshi_error)[:100]}"
        
        # Test Telegram bot
        logger.info("Testing Telegram bot...")
        try:
            from telegram.ext import Application
            application = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()
            await application.initialize()
            logger.info("Telegram bot initialized")
            app_status["bot"] = "telegram_ok"
            await application.shutdown()
        except Exception as telegram_error:
            logger.error(f"Telegram bot failed: {telegram_error}")
            app_status["bot"] = f"telegram_failed: {str(telegram_error)[:100]}"
        
        logger.info("All component tests completed")
        app_status["bot"] = "all_tests_completed"
        
    except Exception as e:
        logger.error(f"Component testing failed: {e}")
        app_status["bot"] = f"test_failed: {str(e)[:100]}"

async def main():
    """Main function - health server starts first"""
    logger.info("Starting Railway deployment...")
    
    # Start health server immediately in background
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Give health server time to start
    await asyncio.sleep(2)
    logger.info("Health server started, now testing bot components...")
    
    # Test bot components (this can fail without affecting health server)
    await try_bot_components()
    
    # Keep the main process alive
    logger.info("Entering main loop...")
    while True:
        await asyncio.sleep(60)
        logger.info(f"Health server running, status: {app_status}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application stopped")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        # Even if main fails, try to keep something running
        import time
        while True:
            time.sleep(60)
            print("Keeping process alive despite error")
