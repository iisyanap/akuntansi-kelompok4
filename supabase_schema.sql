-- ══════════════════════════════════════════════════════════════════════════
--  AccSys — Supabase Schema
--  Run this in: Supabase Dashboard → SQL Editor → New Query
-- ══════════════════════════════════════════════════════════════════════════

-- ── 1. COMPANIES ──────────────────────────────────────────────────────────
-- One record per user. Stores company info + opening balances as JSONB.
CREATE TABLE companies (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  nama TEXT NOT NULL,
  pemilik TEXT DEFAULT '',
  periode TEXT DEFAULT '',
  jenis TEXT DEFAULT '',
  currency TEXT DEFAULT 'IDR',
  opening_balances JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (user_id)
);

-- ── 2. JOURNAL ENTRIES ────────────────────────────────────────────────────
-- Stores both "jurnal umum" and "jurnal penyesuaian" (distinguished by `type`).
-- debit_entries and kredit_entries are JSONB arrays:
--   [{"akun": "Kas", "jumlah": 1000000}, ...]
CREATE TABLE journal_entries (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  type TEXT NOT NULL DEFAULT 'jurnal',  -- 'jurnal' or 'penyesuaian'
  tanggal TEXT NOT NULL,
  keterangan TEXT DEFAULT '',
  debit_entries JSONB NOT NULL DEFAULT '[]',
  kredit_entries JSONB NOT NULL DEFAULT '[]',
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- ── 3. CHAT HISTORY ───────────────────────────────────────────────────────
-- Stores the AI assistant conversation as a JSONB array of messages.
CREATE TABLE chat_history (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  messages JSONB DEFAULT '[]',
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (user_id)
);

-- ── 4. ROW LEVEL SECURITY ─────────────────────────────────────────────────
-- Ensures each user can only access their own data.

ALTER TABLE companies        ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_entries  ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_history     ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users manage own companies"
  ON companies FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users manage own journals"
  ON journal_entries FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users manage own chat"
  ON chat_history FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- ── 5. INDEXES (performance) ─────────────────────────────────────────────
CREATE INDEX idx_journal_user_type   ON journal_entries (user_id, type, sort_order);
CREATE INDEX idx_companies_user      ON companies (user_id);
CREATE INDEX idx_chat_user           ON chat_history (user_id);


-- ══════════════════════════════════════════════════════════════════════════
--  MIGRATION: Multi-Company Support
--  Run this AFTER the initial schema above to enable multiple companies
--  per user and period continuation.
-- ══════════════════════════════════════════════════════════════════════════

-- ── M1. Allow multiple companies per user ─────────────────────────────────
ALTER TABLE companies DROP CONSTRAINT IF EXISTS companies_user_id_key;

-- ── M2. Add company_id to journal_entries ─────────────────────────────────
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_journal_company ON journal_entries (company_id, type, sort_order);

-- ── M3. Add company_id to chat_history ───────────────────────────────────
ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id) ON DELETE CASCADE;
ALTER TABLE chat_history DROP CONSTRAINT IF EXISTS chat_history_user_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_user_company ON chat_history (user_id, company_id);

-- ── M4. Drop and recreate RLS policies with company_id scope ─────────────
DROP POLICY IF EXISTS "Users manage own companies" ON companies;
DROP POLICY IF EXISTS "Users manage own journals" ON journal_entries;
DROP POLICY IF EXISTS "Users manage own chat" ON chat_history;

CREATE POLICY "Users manage own companies"
  ON companies FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users manage own journals"
  ON journal_entries FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users manage own chat"
  ON chat_history FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);
