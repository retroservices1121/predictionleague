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
        import socket
        from urllib.parse import urlparse
        
        try:
            db_url = os.getenv('DATABASE_URL')
            logger.info(f"Database URL format: {db_url[:20]}...{db_url[-20:]}")
            
            # Parse the URL to test individual components
            parsed = urlparse(db_url)
            host = parsed.hostname
            port = parsed.port or 5432
            
            logger.info(f"Attempting to connect to {host}:{port}")
            
            # Test DNS resolution with multiple methods
            try:
                ip = socket.gethostbyname(host)
                logger.info(f"DNS resolution successful: {host} -> {ip}")
            except Exception as dns_error:
                logger.error(f"DNS resolution failed: {dns_error}")
                
                # Try alternative DNS methods
                logger.info("Trying alternative DNS resolution methods...")
                
                # Try with different DNS servers
                import subprocess
                try:
                    result = subprocess.run(['nslookup', host], capture_output=True, text=True, timeout=10)
                    logger.info(f"nslookup result: {result.stdout}")
                except Exception as nslookup_error:
                    logger.error(f"nslookup failed: {nslookup_error}")
                
                # Try direct IP connection (common Supabase IPs)
                # These are examples - we'll need to find the actual IP
                test_ips = [
                    "34.120.54.55",  # Common Google Cloud IP for Supabase
                    "35.236.11.79",  # Another common Supabase IP
                ]
                
                for test_ip in test_ips:
                    try:
                        logger.info(f"Testing direct IP connection: {test_ip}")
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(5)
                        result = sock.connect_ex((test_ip, port))
                        sock.close()
                        
                        if result == 0:
                            logger.info(f"Direct IP connection successful: {test_ip}")
                            # Try to create a modified connection string with IP
                            ip_db_url = db_url.replace(host, test_ip)
                            logger.info(f"Trying connection with IP: {ip_db_url[:30]}...")
                            
                            conn = await asyncpg.connect(
                                ip_db_url,
                                ssl='require',
                                command_timeout=15,
                                server_settings={'application_name': 'railway_test'}
                            )
                            result = await conn.fetchval('SELECT 1')
                            await conn.close()
                            
                            logger.info(f"Database connection successful using IP: {test_ip}")
                            app_status["database"] = f"connected_via_ip_{test_ip}"
                            return
                            
                    except Exception as ip_error:
                        logger.error(f"IP connection {test_ip} failed: {ip_error}")
                        continue
                
                app_status["database"] = f"dns_failed: {dns_error}"
                return
            
            # Test socket connection
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)
                result = sock.connect_ex((host, port))
                sock.close()
                
                if result == 0:
                    logger.info("Socket connection successful")
                else:
                    logger.error(f"Socket connection failed with code: {result}")
                    app_status["database"] = f"socket_failed: {result}"
                    return
            except Exception as socket_error:
                logger.error(f"Socket test failed: {socket_error}")
                app_status["database"] = f"socket_error: {socket_error}"
                return
            
            # Test PostgreSQL connection with different SSL modes
            ssl_modes = ['require', 'prefer', 'disable']
            
            for ssl_mode in ssl_modes:
                try:
                    logger.info(f"Trying PostgreSQL connection with SSL mode: {ssl_mode}")
                    
                    if ssl_mode == 'disable':
                        # Try without SSL
                        conn = await asyncpg.connect(
                            db_url.replace('?sslmode=require', ''),
                            command_timeout=15
                        )
                    else:
                        conn = await asyncpg.connect(
                            db_url,
                            ssl=ssl_mode,
                            command_timeout=15
                        )
                    
                    result = await conn.fetchval('SELECT 1')
                    await conn.close()
                    
                    logger.info(f"Database connection successful with SSL mode: {ssl_mode}")
                    app_status["database"] = f"connected_ssl_{ssl_mode}"
                    return
                    
                except Exception as pg_error:
                    logger.error(f"PostgreSQL connection failed with {ssl_mode}: {pg_error}")
                    continue
            
            # If all SSL modes failed
            app_status["database"] = "all_ssl_modes_failed"
            
        except Exception as db_error:
            logger.error(f"Database connection test failed: {db_error}")
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
