#!/usr/bin/env python3
"""
Test database and API connections
"""

import os
import asyncio
import asyncpg
from dotenv import load_dotenv

load_dotenv()

async def test_database():
    """Test Supabase database connection"""
    try:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            print("❌ DATABASE_URL not found in environment")
            return False
        
        print("🔗 Testing database connection...")
        conn = await asyncpg.connect(database_url)
        
        # Test query
        version = await conn.fetchval("SELECT version()")
        print(f"✅ Database connected: {version[:50]}...")
        
        # Test tables exist
        tables = await conn.fetch("""
            SELECT tablename FROM pg_tables 
            WHERE schemaname = 'public' 
            AND tablename IN ('users', 'weekly_markets', 'predictions')
        """)
        
        table_names = [t['tablename'] for t in tables]
        if len(table_names) >= 3:
            print(f"✅ Core tables found: {table_names}")
        else:
            print(f"⚠️ Missing tables. Found: {table_names}")
        
        await conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False

async def test_telegram():
    """Test Telegram bot token"""
    try:
        import aiohttp
        
        token = os.getenv("TELEGRAM_TOKEN")
        if not token:
            print("❌ TELEGRAM_TOKEN not found")
            return False
        
        print("🤖 Testing Telegram bot token...")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.telegram.org/bot{token}/getMe") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    bot_name = data['result']['first_name']
                    print(f"✅ Telegram bot connected: {bot_name}")
                    return True
                else:
                    print(f"❌ Telegram API returned status {resp.status}")
                    return False
                    
    except Exception as e:
        print(f"❌ Telegram test failed: {e}")
        return False

async def test_kalshi():
    """Test Kalshi API credentials"""
    try:
        email = os.getenv("KALSHI_EMAIL")
        password = os.getenv("KALSHI_PASSWORD")
        private_key = os.getenv("KALSHI_PRIVATE_KEY")
        
        if not all([email, password, private_key]):
            print("❌ Kalshi credentials incomplete")
            return False
        
        print("📊 Testing Kalshi API...")
        
        # Basic validation
        if "@" not in email:
            print("❌ Invalid Kalshi email format")
            return False
        
        if len(password) < 8:
            print("❌ Kalshi password too short")
            return False
        
        try:
            import base64
            base64.b64decode(private_key)
            print("✅ Kalshi private key format valid")
        except:
            print("❌ Kalshi private key not valid base64")
            return False
        
        print("✅ Kalshi credentials format valid")
        return True
        
    except
