-- Optional RLS Policies for Enhanced Security
-- Only run this if you want additional database security

-- Enable RLS on sensitive tables
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_achievements ENABLE ROW LEVEL SECURITY;

-- Bot service role policies
CREATE POLICY "Bot full access" ON users FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Bot predictions access" ON predictions FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Bot achievements access" ON user_achievements FOR ALL USING (auth.role() = 'service_role');

-- Public read policies for leaderboards
CREATE POLICY "Public user read" ON users FOR SELECT USING (true);
CREATE POLICY "Public market read" ON weekly_markets FOR SELECT USING (true);

-- User context function
CREATE OR REPLACE FUNCTION set_user_context(p_user_id BIGINT)
RETURNS void AS $$
BEGIN
    PERFORM set_config('app.user_id', p_user_id::text, true);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
