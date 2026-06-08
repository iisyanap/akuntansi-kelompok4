"""
AccSys — Supabase Data Layer
Wraps all CRUD operations against Supabase (PostgreSQL + Auth).

Tables expected:
  - companies        (user_id UUID, nama, pemilik, periode, jenis, currency, opening_balances JSONB)
  - journal_entries  (user_id UUID, type, tanggal, keterangan, debit_entries JSONB, kredit_entries JSONB, sort_order INT)
  - chat_history     (user_id UUID, messages JSONB)
"""

import os
from typing import Optional
from supabase import create_client, Client


# ──────────────────────────────────────────────────────────────────────────
#  Client factory
# ──────────────────────────────────────────────────────────────────────────

def get_url() -> str:
    return os.getenv("SUPABASE_URL", "")


def get_anon_key() -> str:
    return os.getenv("SUPABASE_ANON_KEY", "")


def make_client(access_token: Optional[str] = None,
                refresh_token: Optional[str] = None) -> Client:
    """
    Return a Supabase client.
    - Without access_token: uses the anon key (for auth operations only).
    - With access_token:    authenticates as the user, enabling RLS-protected queries.
    """
    url = get_url()
    key = get_anon_key()
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_ANON_KEY must be set in .env"
        )
    client = create_client(url, key)
    if access_token:
        client.auth.set_session(
            access_token=access_token,
            refresh_token=refresh_token or ""
        )
    return client


# ──────────────────────────────────────────────────────────────────────────
#  Auth helpers (operate on anon client)
# ──────────────────────────────────────────────────────────────────────────

def auth_sign_up(email: str, password: str):
    """Register a new user. Returns (response, None) or (None, error_string)."""
    client = make_client()
    try:
        resp = client.auth.sign_up({"email": email, "password": password})
        return resp, None
    except Exception as e:
        return None, str(e)


def auth_sign_in(email: str, password: str):
    """Log in. Returns (response, None) or (None, error_string)."""
    client = make_client()
    try:
        resp = client.auth.sign_in_with_password(
            {"email": email, "password": password})
        return resp, None
    except Exception as e:
        return None, str(e)


# ──────────────────────────────────────────────────────────────────────────
#  COMPANIES
# ──────────────────────────────────────────────────────────────────────────

def get_company(access_token: str, user_id: str) -> Optional[dict]:
    """Return the first company for a user, or None."""
    client = make_client(access_token)
    resp = (
        client.table("companies")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if not rows:
        return None
    row = rows[0]
    return {
        "id":               row["id"],
        "nama":             row.get("nama", ""),
        "pemilik":          row.get("pemilik", ""),
        "periode":          row.get("periode", ""),
        "jenis":            row.get("jenis", ""),
        "currency":         row.get("currency", "IDR"),
        "opening_balances": row.get("opening_balances") or {},
    }


def save_company(access_token: str, user_id: str, data: dict) -> dict:
    """Insert or update the user's company record."""
    client = make_client(access_token)
    existing = get_company(access_token, user_id)
    payload = {
        "user_id":          user_id,
        "nama":             data.get("nama", ""),
        "pemilik":          data.get("pemilik", ""),
        "periode":          data.get("periode", ""),
        "jenis":            data.get("jenis", ""),
        "currency":         data.get("currency", "IDR"),
        "opening_balances": data.get("opening_balances", {}),
    }
    if existing:
        resp = (
            client.table("companies")
            .update(payload)
            .eq("user_id", user_id)
            .execute()
        )
    else:
        resp = client.table("companies").insert(payload).execute()
    rows = resp.data or []
    return rows[0] if rows else {}


# ──────────────────────────────────────────────────────────────────────────
#  JOURNAL ENTRIES  (jurnal umum + jurnal penyesuaian)
# ──────────────────────────────────────────────────────────────────────────

def get_journals(access_token: str, user_id: str,
                 entry_type: str = "jurnal") -> list[dict]:
    """Return all journal entries for a user, ordered by sort_order."""
    client = make_client(access_token)
    resp = (
        client.table("journal_entries")
        .select("*")
        .eq("user_id", user_id)
        .eq("type", entry_type)
        .order("sort_order")
        .order("created_at")
        .execute()
    )
    rows = resp.data or []
    result = []
    for idx, row in enumerate(rows):
        result.append({
            "id":             row["id"],
            "idx":            idx,
            "tanggal":        row.get("tanggal", ""),
            "keterangan":     row.get("keterangan", ""),
            "debit_entries":  row.get("debit_entries") or [],
            "kredit_entries": row.get("kredit_entries") or [],
        })
    return result


def add_journal(access_token: str, user_id: str,
                entry: dict, entry_type: str = "jurnal") -> dict:
    """Insert a new journal entry. Returns the inserted row."""
    client = make_client(access_token)
    existing = get_journals(access_token, user_id, entry_type)
    next_order = len(existing)

    payload = {
        "user_id":        user_id,
        "type":           entry_type,
        "tanggal":        entry.get("tanggal", ""),
        "keterangan":     entry.get("keterangan", ""),
        "debit_entries":  entry.get("debit_entries", []),
        "kredit_entries": entry.get("kredit_entries", []),
        "sort_order":     next_order,
    }
    resp = client.table("journal_entries").insert(payload).execute()
    rows = resp.data or []
    inserted = rows[0] if rows else {}
    return {
        "id":             inserted.get("id", ""),
        "tanggal":        inserted.get("tanggal", ""),
        "keterangan":     inserted.get("keterangan", ""),
        "debit_entries":  inserted.get("debit_entries") or [],
        "kredit_entries": inserted.get("kredit_entries") or [],
    }


def delete_journal_by_index(access_token: str, user_id: str,
                            idx: int, entry_type: str = "jurnal") -> bool:
    """Delete a journal entry by its positional index."""
    journals = get_journals(access_token, user_id, entry_type)
    if 0 <= idx < len(journals):
        client = make_client(access_token)
        entry_id = journals[idx]["id"]
        resp = (
            client.table("journal_entries")
            .delete()
            .eq("id", entry_id)
            .eq("user_id", user_id)
            .execute()
        )
        return bool(resp.data)
    return False


def clear_journals(access_token: str, user_id: str,
                   entry_type: str = "jurnal") -> None:
    """Delete all journal entries of a given type for a user."""
    client = make_client(access_token)
    client.table("journal_entries").delete().eq(
        "user_id", user_id).eq("type", entry_type).execute()


# ──────────────────────────────────────────────────────────────────────────
#  CHAT HISTORY
# ──────────────────────────────────────────────────────────────────────────

def get_chat_history(access_token: str, user_id: str) -> list[dict]:
    """Return stored AI chat messages, or empty list."""
    client = make_client(access_token)
    resp = (
        client.table("chat_history")
        .select("messages")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if not rows:
        return []
    return rows[0].get("messages") or []


def save_chat_history(access_token: str, user_id: str,
                      messages: list[dict]) -> None:
    """Upsert the chat history for a user."""
    client = make_client(access_token)
    existing = (
        client.table("chat_history")
        .select("id")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        client.table("chat_history").update(
            {"messages": messages}
        ).eq("user_id", user_id).execute()
    else:
        client.table("chat_history").insert({
            "user_id":  user_id,
            "messages": messages,
        }).execute()


def clear_chat_history(access_token: str, user_id: str) -> None:
    """Remove all chat messages for a user."""
    client = make_client(access_token)
    client.table("chat_history").delete().eq("user_id", user_id).execute()


# ──────────────────────────────────────────────────────────────────────────
#  RESET (delete everything for a user)
# ──────────────────────────────────────────────────────────────────────────

def delete_all_user_data(access_token: str, user_id: str) -> None:
    """Hard-delete all accounting data for a user (used by /reset)."""
    client = make_client(access_token)
    client.table("journal_entries").delete().eq("user_id", user_id).execute()
    client.table("chat_history").delete().eq("user_id", user_id).execute()
    client.table("companies").delete().eq("user_id", user_id).execute()
