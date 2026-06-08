-- ══════════════════════════════════════════════════════════════════════════
--  AccSys — Initial Schema Migration
--  Auto-applied by Supabase GitHub integration on push.
-- ══════════════════════════════════════════════════════════════════════════

-- ── 1. COMPANIES ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS companies (
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
CREATE TABLE IF NOT EXISTS journal_entries (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  type TEXT NOT NULL DEFAULT 'jurnal',
  tanggal TEXT NOT NULL,
  keterangan TEXT DEFAULT '',
  debit_entries JSONB NOT NULL DEFAULT '[]',
  kredit_entries JSONB NOT NULL DEFAULT '[]',
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- ── 3. CHAT HISTORY ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat_history (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  messages JSONB DEFAULT '[]',
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (user_id)
);

-- ── 4. ROW LEVEL SECURITY ─────────────────────────────────────────────────
ALTER TABLE companies        ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_entries  ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_history     ENABLE ROW LEVEL SECURITY;

-- Drop existing policies first (safe for re-runs)
DROP POLICY IF EXISTS "Users manage own companies" ON companies;
DROP POLICY IF EXISTS "Users manage own journals" ON journal_entries;
DROP POLICY IF EXISTS "Users manage own chat" ON chat_history;

CREATE POLICY "Users manage own companies"
  ON companies FOR ALL
  USING (auth.uid() = user_id);

CREATE POLICY "Users manage own journals"
  ON journal_entries FOR ALL
  USING (auth.uid() = user_id);

CREATE POLICY "Users manage own chat"
  ON chat_history FOR ALL
  USING (auth.uid() = user_id);

-- ── 5. INDEXES ────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_journal_user_type ON journal_entries (user_id, type, sort_order);
CREATE INDEX IF NOT EXISTS idx_companies_user    ON companies (user_id);
CREATE INDEX IF NOT EXISTS idx_chat_user         ON chat_history (user_id);
