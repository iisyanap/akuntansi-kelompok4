"""
AccSys — Supabase Data Layer
Wraps all CRUD operations against Supabase (PostgreSQL + Auth).

Tables expected:
  - companies        (user_id UUID, nama, pemilik, periode, jenis, currency, opening_balances JSONB)
  - journal_entries  (user_id UUID, company_id UUID, type, tanggal, keterangan, debit_entries JSONB, kredit_entries JSONB, sort_order INT)
  - chat_history     (user_id UUID, company_id UUID, messages JSONB)
"""

import os
from typing import Optional
from supabase import create_client, Client

class SessionExpiredError(Exception):
    pass

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
    - With access_token:    authenticates as the user via direct header injection.
    """
    url = get_url()
    key = get_anon_key()
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_ANON_KEY must be set in .env"
        )
    client = create_client(url, key)
    if access_token:
        # Set the user's JWT on BOTH header dicts — session.headers AND self.headers
        bearer = f"Bearer {access_token}"
        client.postgrest.session.headers["Authorization"] = bearer
        client.postgrest.headers["Authorization"] = bearer
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

def _row_to_company(row: dict) -> dict:
    """Convert a Supabase row to a company dict."""
    return {
        "id":               row["id"],
        "nama":             row.get("nama", ""),
        "pemilik":          row.get("pemilik", ""),
        "periode":          row.get("periode", ""),
        "jenis":            row.get("jenis", ""),
        "currency":         row.get("currency", "IDR"),
        "opening_balances": row.get("opening_balances") or {},
    }

def refresh_session(refresh_token: str):
    client = make_client()

    try:
        response = client.auth.refresh_session(
            refresh_token
        )
        return response, None

    except Exception as e:
        return None, str(e)

def execute_with_refresh(
    operation,
    access_token: str,
    refresh_token: str = ""
):
    """
    Jalankan query Supabase.
    Jika JWT expired → refresh token → ulangi query.
    """

    try:
        return operation(access_token)

    except Exception as e:

        if "JWT expired" not in str(e):
            raise

        if not refresh_token:
            raise SessionExpiredError(
                "Session expired. Please login again."
            )

        tokens = refresh_access_token(refresh_token)

        return operation(tokens["access_token"])

def get_companies(access_token: str, user_id: str,
                  refresh_token: str = "") -> list[dict]:
    """Return all companies for a user, ordered by created_at."""
    client = make_client(access_token, refresh_token)
    resp = (
        client.table("companies")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
    )
    return [_row_to_company(r) for r in (resp.data or [])]


def get_company(access_token: str, user_id: str,
                refresh_token: str = "") -> Optional[dict]:
    """Return the first company for a user, or None. (backward compat)"""
    companies = get_companies(access_token, user_id, refresh_token)
    return companies[0] if companies else None


def get_company_by_id(access_token: str, user_id: str, company_id: str,
                      refresh_token: str = "") -> Optional[dict]:
    """Return a specific company by ID, or None."""
    client = make_client(access_token, refresh_token)
    resp = (
        client.table("companies")
        .select("*")
        .eq("id", company_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return _row_to_company(rows[0]) if rows else None


def save_company(access_token: str, user_id: str, data: dict,
                 company_id: str = "",
                 refresh_token: str = "") -> dict:
    """Insert or update a company record.
    - If company_id is provided: update that specific company.
    - If company_id is empty: insert a new company.
    """

    payload = {
        "user_id": user_id,
        "nama": data.get("nama", ""),
        "pemilik": data.get("pemilik", ""),
        "periode": data.get("periode", ""),
        "jenis": data.get("jenis", ""),
        "currency": data.get("currency", "IDR"),
        "opening_balances": data.get("opening_balances", {}),
    }

    def _operation(token):

        client = make_client(token)

        if company_id:
            return (
                client.table("companies")
                .update(payload)
                .eq("id", company_id)
                .eq("user_id", user_id)
                .execute()
            )

        return (
            client.table("companies")
            .insert(payload)
            .execute()
        )

    resp = execute_with_refresh(
        _operation,
        access_token,
        refresh_token
    )

    rows = resp.data or []

    return _row_to_company(rows[0]) if rows else {}


def delete_company(access_token: str, user_id: str, company_id: str,
                   refresh_token: str = "") -> bool:
    """Delete a company and all its related data (journals, chat)."""
    client = make_client(access_token, refresh_token)
    # Delete journals and chat for this company first
    client.table("journal_entries").delete().eq(
        "user_id", user_id).eq("company_id", company_id).execute()
    client.table("chat_history").delete().eq(
        "user_id", user_id).eq("company_id", company_id).execute()
    # Delete the company itself
    resp = (
        client.table("companies")
        .delete()
        .eq("id", company_id)
        .eq("user_id", user_id)
        .execute()
    )
    return bool(resp.data)


# ──────────────────────────────────────────────────────────────────────────
#  JOURNAL ENTRIES  (jurnal umum + jurnal penyesuaian)
# ──────────────────────────────────────────────────────────────────────────

def get_journals(access_token: str, user_id: str,
                 entry_type: str = "jurnal",
                 refresh_token: str = "",
                 company_id: str = "") -> list[dict]:
    """Return all journal entries for a user+company, ordered by sort_order."""
    client = make_client(access_token, refresh_token)
    query = (
        client.table("journal_entries")
        .select("*")
        .eq("user_id", user_id)
        .eq("type", entry_type)
    )
    if company_id:
        query = query.eq("company_id", company_id)
    resp = (
        query
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
                entry: dict, entry_type: str = "jurnal",
                refresh_token: str = "",
                company_id: str = "") -> dict:
    """Insert a new journal entry. Returns the inserted row."""
    client = make_client(access_token, refresh_token)
    existing = get_journals(access_token, user_id, entry_type,
                            refresh_token, company_id)
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
    if company_id:
        payload["company_id"] = company_id
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
                            idx: int, entry_type: str = "jurnal",
                            refresh_token: str = "",
                            company_id: str = "") -> bool:
    """Delete a journal entry by its positional index."""
    journals = get_journals(access_token, user_id, entry_type,
                            refresh_token, company_id)
    if 0 <= idx < len(journals):
        client = make_client(access_token, refresh_token)
        entry_id = journals[idx]["id"]
        query = (
            client.table("journal_entries")
            .delete()
            .eq("id", entry_id)
            .eq("user_id", user_id)
        )
        if company_id:
            query = query.eq("company_id", company_id)
        resp = query.execute()
        return bool(resp.data)
    return False


def clear_journals(access_token: str, user_id: str,
                   entry_type: str = "jurnal",
                   refresh_token: str = "",
                   company_id: str = "") -> None:
    """Delete all journal entries of a given type for a user+company."""
    client = make_client(access_token, refresh_token)
    query = (
        client.table("journal_entries")
        .delete()
        .eq("user_id", user_id)
        .eq("type", entry_type)
    )
    if company_id:
        query = query.eq("company_id", company_id)
    query.execute()


# ──────────────────────────────────────────────────────────────────────────
#  CHAT HISTORY
# ──────────────────────────────────────────────────────────────────────────

def get_chat_history(access_token: str, user_id: str,
                     refresh_token: str = "",
                     company_id: str = "") -> list[dict]:
    """Return stored AI chat messages, or empty list."""
    client = make_client(access_token, refresh_token)
    query = (
        client.table("chat_history")
        .select("messages")
        .eq("user_id", user_id)
    )
    if company_id:
        query = query.eq("company_id", company_id)
    resp = query.limit(1).execute()
    rows = resp.data or []
    if not rows:
        return []
    return rows[0].get("messages") or []


def save_chat_history(access_token: str, user_id: str,
                      messages: list[dict],
                      refresh_token: str = "",
                      company_id: str = "") -> None:
    """Upsert the chat history for a user+company."""
    client = make_client(access_token, refresh_token)
    query = (
        client.table("chat_history")
        .select("id")
        .eq("user_id", user_id)
    )
    if company_id:
        query = query.eq("company_id", company_id)
    existing = query.limit(1).execute()

    if existing.data:
        upd = client.table("chat_history").update(
            {"messages": messages}
        ).eq("user_id", user_id)
        if company_id:
            upd = upd.eq("company_id", company_id)
        upd.execute()
    else:
        payload = {"user_id": user_id, "messages": messages}
        if company_id:
            payload["company_id"] = company_id
        client.table("chat_history").insert(payload).execute()


def clear_chat_history(access_token: str, user_id: str,
                       refresh_token: str = "",
                       company_id: str = "") -> None:
    """Remove all chat messages for a user+company."""
    client = make_client(access_token, refresh_token)
    query = client.table("chat_history").delete().eq("user_id", user_id)
    if company_id:
        query = query.eq("company_id", company_id)
    query.execute()


# ──────────────────────────────────────────────────────────────────────────
#  RESET (delete everything for a user)
# ──────────────────────────────────────────────────────────────────────────

def delete_all_user_data(access_token: str, user_id: str,
                         refresh_token: str = "") -> None:
    """Hard-delete all accounting data for a user (used by /reset)."""
    client = make_client(access_token, refresh_token)
    client.table("journal_entries").delete().eq("user_id", user_id).execute()
    client.table("chat_history").delete().eq("user_id", user_id).execute()
    client.table("companies").delete().eq("user_id", user_id).execute()
