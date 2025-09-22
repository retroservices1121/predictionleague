"""
Advanced scoring system for Prediction League Bot
Optional module for enhanced scoring features
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ScoringConfig:
    """Configuration for advanced scoring"""
    base_correct_points: int = 10
    base_wrong_points: int = 0
    contrarian_bonus_threshold: float = 0.3
    contrarian_bonus_multiplier: float = 1.5
    streak_bonus_threshold: int = 3
    streak_bonus_per_correct: int = 2
    early_bird_bonus_hours: int = 24
    early_bird_bonus: int = 3

class AdvancedScoring:
    """Advanced scoring with bonuses"""
    
    def __init__(self, db_pool, config: ScoringConfig = None):
        self.pool = db_pool
        self.config = config or ScoringConfig()
    
    async def calculate_score(self, prediction_id: int, market_odds: float = None) -> int:
        """Calculate score with bonuses"""
        async with self.pool.acquire() as conn:
            prediction = await conn.fetchrow("""
                SELECT p.*, wm.resolution_value, wm.close_time
                FROM predictions p
                JOIN weekly_markets wm ON p.market_id = wm.id
                WHERE p.id = $1
            """, prediction_id)
            
            if not prediction or prediction['resolution_value'] is None:
                return 0
            
            is_correct = prediction['prediction'] == prediction['resolution_value']
            base_score = self.config.base_correct_points if is_correct else self.config.base_wrong_points
            
            if not is_correct:
                return base_score
            
            # Calculate bonuses
            bonus_score = 0
            
            # Contrarian bonus
            if market_odds and market_odds < self.config.contrarian_bonus_threshold:
                bonus_score += int(base_score * self.config.contrarian_bonus_multiplier)
            
            # Early bird bonus
            time_to_close = prediction['close_time'] - prediction['created_at']
            if time_to_close.total_seconds() > self.config.early_bird_bonus_hours * 3600:
                bonus_score += self.config.early_bird_bonus
            
            # Streak bonus
            streak = await self.get_user_streak(prediction['user_id'])
            if streak >= self.config.streak_bonus_threshold:
                bonus_score += (streak - self.config.streak_bonus_threshold + 1) * self.config.streak_bonus_per_correct
            
            return base_score + bonus_score
    
    async def get_user_streak(self, user_id: int) -> int:
        """Get current streak"""
        async with self.pool.acquire() as conn:
            recent_predictions = await conn.fetch("""
                SELECT p.prediction = wm.resolution_value as is_correct
                FROM predictions p
                JOIN weekly_markets wm ON p.market_id = wm.id
                WHERE p.user_id = $1 AND wm.resolved = TRUE
                ORDER BY p.created_at DESC
                LIMIT 10
            """, user_id)
            
            streak = 0
            for pred in recent_predictions:
                if pred['is_correct']:
                    streak += 1
                else:
                    break
            
            return streak

class AchievementManager:
    """Manage user achievements"""
    
    def __init__(self, db_pool):
        self.pool = db_pool
        self.achievements = {
            'first_prediction': 'First Steps 👶',
            'hot_streak_5': 'Hot Streak 🔥',
            'contrarian_genius': 'Contrarian Genius 🧠',
            'perfect_week': 'Perfect Week 💯',
            'century_club': 'Century Club 💰'
        }
    
    async def check_achievements(self, user_id: int) -> List[str]:
        """Check for new achievements"""
        new_achievements = []
        
        async with self.pool.acquire() as conn:
            # Check first prediction
            prediction_count = await conn.fetchval(
                "SELECT COUNT(*) FROM predictions WHERE user_id = $1", user_id
            )
            
            if prediction_count == 1:
                await self.award_achievement(user_id, 'first_prediction')
                new_achievements.append(self.achievements['first_prediction'])
            
            # Check century club
            user_points = await conn.fetchval(
                "SELECT total_points FROM users WHERE telegram_id = $1", user_id
            )
            
            if user_points >= 100:
                has_achievement = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM user_achievements WHERE user_id = $1 AND achievement_key = 'century_club')",
                    user_id
                )
                if not has_achievement:
                    await self.award_achievement(user_id, 'century_club')
                    new_achievements.append(self.achievements['century_club'])
        
        return new_achievements
    
    async def award_achievement(self, user_id: int, achievement_key: str):
        """Award achievement to user"""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_achievements (user_id, achievement_key) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                user_id, achievement_key
            )
