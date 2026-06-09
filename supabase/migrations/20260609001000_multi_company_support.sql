-- ══════════════════════════════════════════════════════════════════════════
--  AccSys — Multi-Company Support Migration
--  Adds company-scoped storage for journals and chat history.
--  Keeps existing accounting flow unchanged.
-- ══════════════════════════════════════════════════════════════════════════

-- ── 1. Allow multiple companies per user ─────────────────────────────────
ALTER TABLE companies DROP CONSTRAINT IF EXISTS companies_user_id_key;

-- ── 2. Add company_id to journal_entries ─────────────────────────────────
ALTER TABLE journal_entries
  ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_journal_company
  ON journal_entries (company_id, type, sort_order);

-- Backfill existing journal rows to their original company.
UPDATE journal_entries AS j
SET company_id = c.id
FROM companies AS c
WHERE j.user_id = c.user_id
  AND j.company_id IS NULL;

-- ── 3. Add company_id to chat_history ────────────────────────────────────
ALTER TABLE chat_history
  ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id) ON DELETE CASCADE;

ALTER TABLE chat_history DROP CONSTRAINT IF EXISTS chat_history_user_id_key;

CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_user_company
  ON chat_history (user_id, company_id);

-- Backfill existing chat rows to their original company.
UPDATE chat_history AS h
SET company_id = c.id
FROM companies AS c
WHERE h.user_id = c.user_id
  AND h.company_id IS NULL;

-- ── 4. Keep policies user-scoped, company filtering is handled in app code ─
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
