-- ═══════════════════════════════════════════
-- MadeByTK Toto Tracker — Supabase Schema
-- Run this in Supabase SQL Editor
-- ═══════════════════════════════════════════

-- 1. Draws table
CREATE TABLE draws (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    draw_date DATE NOT NULL,
    draw_number TEXT,
    predictions JSONB DEFAULT '[]'::jsonb,
    bets JSONB DEFAULT '[]'::jsonb,
    results JSONB DEFAULT '{"winning":[],"additional":null}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Each draw date must be unique (prevents duplicate rows)
ALTER TABLE draws ADD CONSTRAINT draws_draw_date_unique UNIQUE (draw_date);

-- Index for fast date sorting
CREATE INDEX idx_draws_date ON draws(draw_date DESC);

-- 2. Settings table (for API keys, URLs — admin only)
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 3. Enable Row Level Security
ALTER TABLE draws ENABLE ROW LEVEL SECURITY;
ALTER TABLE settings ENABLE ROW LEVEL SECURITY;

-- 4. RLS Policies

-- Draws: anyone can read, only authenticated users can insert/update/delete
CREATE POLICY "Public can read draws"
    ON draws FOR SELECT
    USING (true);

CREATE POLICY "Authenticated can insert draws"
    ON draws FOR INSERT
    TO authenticated
    WITH CHECK (true);

CREATE POLICY "Authenticated can update draws"
    ON draws FOR UPDATE
    TO authenticated
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Authenticated can delete draws"
    ON draws FOR DELETE
    TO authenticated
    USING (true);

-- Settings: only authenticated users can read and write
CREATE POLICY "Authenticated can read settings"
    ON settings FOR SELECT
    TO authenticated
    USING (true);

CREATE POLICY "Authenticated can insert settings"
    ON settings FOR INSERT
    TO authenticated
    WITH CHECK (true);

CREATE POLICY "Authenticated can update settings"
    ON settings FOR UPDATE
    TO authenticated
    USING (true)
    WITH CHECK (true);

-- 5. Auto-update updated_at trigger
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER draws_updated_at
    BEFORE UPDATE ON draws
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER settings_updated_at
    BEFORE UPDATE ON settings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- 6. Create your admin user
-- Go to Supabase Dashboard → Authentication → Users → Add User
-- Use your email and a strong password. That becomes your admin login.
