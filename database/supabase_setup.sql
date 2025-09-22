-- Supabase Database Schema for Prediction League Bot
-- Run this SQL in your Supabase SQL Editor

-- Enable necessary extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users table
CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    total_points INTEGER DEFAULT 0,
    weekly_points INTEGER DEFAULT 0,
    current_streak INTEGER DEFAULT 0,
    longest_streak INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_active TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Leagues table
CREATE TABLE IF NOT EXISTS leagues (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    admin_id BIGINT REFERENCES users(telegram_id),
    is_private BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- League memberships
CREATE TABLE IF NOT EXISTS league_members (
    league_id INTEGER REFERENCES leagues(id) ON DELETE CASCADE,
    user_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
    role TEXT DEFAULT 'member',
    joined_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    points_in_league INTEGER DEFAULT 0,
    PRIMARY KEY (league_id, user_id)
);

-- Weekly markets
CREATE TABLE IF NOT EXISTS weekly_markets (
    id SERIAL PRIMARY KEY,
    week_start DATE NOT NULL,
    market_ticker TEXT,
    title TEXT NOT NULL,
    description TEXT,
    category TEXT DEFAULT 'other',
    close_time TIMESTAMP WITH TIME ZONE,
    resolved BOOLEAN DEFAULT FALSE,
    resolution_value BOOLEAN,
    resolved_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- User predictions
CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
    market_id INTEGER REFERENCES weekly_markets(id) ON DELETE CASCADE,
    prediction BOOLEAN NOT NULL,
    confidence INTEGER DEFAULT 50,
    points_earned INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, market_id)
);

-- User achievements
CREATE TABLE IF NOT EXISTS user_achievements (
    user_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
    achievement_key TEXT NOT NULL,
    earned_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (user_id, achievement_key)
);

-- Market performance tracking
CREATE TABLE IF NOT EXISTS market_performance (
    market_id INTEGER REFERENCES weekly_markets(id) ON DELETE CASCADE PRIMARY KEY,
    total_predictions INTEGER DEFAULT 0,
    yes_predictions INTEGER DEFAULT 0,
    no_predictions INTEGER DEFAULT 0,
    avg_confidence DECIMAL(5,2)
);

-- Bot analytics
CREATE TABLE IF NOT EXISTS bot_metrics (
    id SERIAL PRIMARY KEY,
    metric_type TEXT NOT NULL,
    metric_value JSONB,
    user_id BIGINT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_users_total_points ON users(total_points DESC);
CREATE INDEX IF NOT EXISTS idx_users_weekly_points ON users(weekly_points DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_user_id ON predictions(user_id);
CREATE INDEX IF NOT EXISTS idx_predictions_market_id ON predictions(market_id);
CREATE INDEX IF NOT EXISTS idx_weekly_markets_week_start ON weekly_markets(week_start);
CREATE INDEX IF NOT EXISTS idx_weekly_markets_resolved ON weekly_markets(resolved);
CREATE INDEX IF NOT EXISTS idx_league_members_league_id ON league_members(league_id);

-- Create trigger for updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_leagues_updated_at BEFORE UPDATE ON leagues
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_weekly_markets_updated_at BEFORE UPDATE ON weekly_markets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Function to get leaderboard
CREATE OR REPLACE FUNCTION get_leaderboard(
    p_league_id INTEGER DEFAULT NULL,
    p_weekly BOOLEAN DEFAULT FALSE,
    p_limit INTEGER DEFAULT 10
)
RETURNS TABLE (
    telegram_id BIGINT,
    username TEXT,
    first_name TEXT,
    points INTEGER,
    rank INTEGER
) AS $$
BEGIN
    IF p_league_id IS NOT NULL THEN
        RETURN QUERY
        SELECT 
            u.telegram_id,
            u.username,
            u.first_name,
            CASE WHEN p_weekly THEN u.weekly_points ELSE u.total_points END as points,
            ROW_NUMBER() OVER (ORDER BY 
                CASE WHEN p_weekly THEN u.weekly_points ELSE u.total_points END DESC
            )::INTEGER as rank
        FROM users u
        JOIN league_members lm ON u.telegram_id = lm.user_id
        WHERE lm.league_id = p_league_id
        ORDER BY points DESC
        LIMIT p_limit;
    ELSE
        RETURN QUERY
        SELECT 
            u.telegram_id,
            u.username,
            u.first_name,
            CASE WHEN p_weekly THEN u.weekly_points ELSE u.total_points END as points,
            ROW_NUMBER() OVER (ORDER BY 
                CASE WHEN p_weekly THEN u.weekly_points ELSE u.total_points END DESC
            )::INTEGER as rank
        FROM users u
        ORDER BY points DESC
        LIMIT p_limit;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Insert achievement definitions
INSERT INTO bot_metrics (metric_type, metric_value) VALUES 
('achievement_definitions', '{
    "first_prediction": {
        "name": "First Steps 👶",
        "description": "Made your first prediction"
    },
    "hot_streak_5": {
        "name": "Hot Streak 🔥",
        "description": "5 correct predictions in a row"
    },
    "contrarian_genius": {
        "name": "Contrarian Genius 🧠",
        "description": "Won 3 predictions with <30% odds"
    },
    "sports_prophet": {
        "name": "Sports Prophet 🏈",
        "description": "10 correct sports predictions"
    },
    "perfect_week": {
        "name": "Perfect Week 💯",
        "description": "All predictions correct in a week"
    },
    "century_club": {
        "name": "Century Club 💰",
        "description": "Reached 100 total points"
    }
}'::jsonb)
ON CONFLICT DO NOTHING;
