# ══════════════════════════════════════════════════════════════════════════
#  AccSys — Sistem Akuntansi  |  Flask + Groq + LangChain
# ══════════════════════════════════════════════════════════════════════════
import os, io, json, html as _html, re as _re
from datetime import date, datetime
from uuid import uuid4
from urllib import parse as _urlparse, request as _urlrequest
from urllib.error import HTTPError, URLError
from flask import (Flask, render_template, request,
                   session, redirect, url_for, jsonify, send_file)

# Load .env otomatis (pip install python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv opsional; bisa juga set env manual

from logic.akuntansi import (
    build_buku_besar, build_neraca_saldo, build_kertas_kerja,
    build_laporan, build_jurnal_penutup, ACCOUNT_CODES, get_account_type
)
from logic.export_excel import (
    export_jurnal, export_buku_besar, export_neraca_saldo,
    export_penyesuaian, export_kertas_kerja, export_laporan, export_jurnal_penutup
)

# ── App config ─────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "akuntansi_secret_2024")

# Groq API key: baca dari .env, bisa di-override via UI
GROQ_API_KEY_ENV = os.getenv("GROQ_API_KEY", "")

# Supabase storage: isi di .env agar data tidak lagi tersimpan di browser.
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = (os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
                or os.getenv("SUPABASE_ANON_KEY", ""))
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "accsys_companies")
SUPABASE_OWNER_ID = os.getenv("SUPABASE_OWNER_ID", "default")
SUPABASE_TIMEOUT = float(os.getenv("SUPABASE_TIMEOUT", "25"))

# ── Jinja2 filters ─────────────────────────────────────────────────────────
@app.template_filter('enumerate')
def jinja_enumerate(iterable, start=0):
    return list(enumerate(iterable, start=start))

@app.template_filter('sum_debit')
def sum_debit(jurnal):
    return sum(item["jumlah"] for e in jurnal for item in e.get("debit_entries", []))

@app.template_filter('sum_kredit')
def sum_kredit(jurnal):
    return sum(item["jumlah"] for e in jurnal for item in e.get("kredit_entries", []))

@app.template_filter('fmt_currency')
def fmt_currency(value, currency="IDR"):
    if not value:
        return ""
    try:
        n = float(value)
    except Exception:
        return value
    return f"$ {n:,.2f}" if currency == "USD" else f"Rp {n:,.0f}"

@app.context_processor
def inject_globals():
    data = get_data()
    currency = data.get("perusahaan", {}).get("currency", "IDR")
    symbol   = "$" if currency == "USD" else "Rp"
    # Cek apakah key tersedia (env atau session)
    has_key  = bool(GROQ_API_KEY_ENV or session.get("groq_api_key"))
    active_company = get_active_company()
    return dict(currency=currency, currency_symbol=symbol, groq_key_set=has_key,
                active_company=active_company,
                account_options=sort_account_options(ACCOUNT_CODES),
                storage_backend="Supabase" if supabase_enabled() else "Session")

# ── Helpers ────────────────────────────────────────────────────────────────
def blank_data():
    return {
        "perusahaan": {}, "opening_balances": {},
        "jurnal": [], "jurnal_penyesuaian": [], "penyesuaian_info": []
    }

def supabase_enabled():
    return bool(SUPABASE_URL and SUPABASE_KEY)

def _supabase_request(method, path, payload=None, prefer=None):
    if not supabase_enabled():
        return None
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = _urlrequest.Request(url, data=body, headers=headers, method=method)
    try:
        with _urlrequest.urlopen(req, timeout=SUPABASE_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Supabase error {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Tidak bisa terhubung ke Supabase: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Tidak bisa terhubung ke Supabase: timed out") from exc

def _q(value):
    return _urlparse.quote(str(value), safe="")

def db_get_companies():
    path = (f"{SUPABASE_TABLE}?select=id,data,updated_at"
            f"&owner_id=eq.{_q(SUPABASE_OWNER_ID)}&order=updated_at.desc")
    rows = _supabase_request("GET", path) or []
    return [{
        "id": row.get("id"),
        "data": row.get("data") or blank_data(),
        "updated_at": (row.get("updated_at") or "").replace("T", " ")[:19]
    } for row in rows]

def supabase_status():
    status = {
        "configured": supabase_enabled(),
        "backend": "Supabase" if supabase_enabled() else "Session",
        "url": SUPABASE_URL,
        "table": SUPABASE_TABLE,
        "owner_id": SUPABASE_OWNER_ID,
        "active_company_id": session.get("active_company_id"),
        "last_storage_error": session.get("storage_error"),
    }
    if not supabase_enabled():
        status["message"] = "SUPABASE_URL dan SUPABASE_ANON_KEY belum terbaca. Data disimpan di session browser."
        return status
    try:
        rows = db_get_companies()
        status["ok"] = True
        status["company_count"] = len(rows)
        status["latest_company"] = rows[0].get("data", {}).get("perusahaan", {}).get("nama") if rows else None
    except Exception as exc:
        status["ok"] = False
        status["error"] = str(exc)
    return status

def remember_storage_error(exc):
    session["storage_error"] = str(exc)
    session.modified = True

def get_session_companies():
    companies = session.get("companies", [])
    legacy_data = session.get("data")
    if legacy_data and legacy_data.get("perusahaan", {}).get("nama") and not companies:
        company_id = session.get("active_company_id") or uuid4().hex
        companies = [{
            "id": company_id,
            "data": legacy_data,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }]
        session["companies"] = companies
        session["active_company_id"] = company_id
        session.modified = True
    return companies

def get_session_active_company():
    active_id = session.get("active_company_id")
    for company in get_session_companies():
        if company.get("id") == active_id:
            return company
    data = session.get("data")
    if data and data.get("perusahaan", {}).get("nama"):
        return {"id": active_id or "session", "data": data, "updated_at": ""}
    return None

def save_data_to_session(data):
    companies = get_session_companies()
    active_id = session.get("active_company_id")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if data.get("perusahaan", {}).get("nama"):
        for company in companies:
            if company.get("id") == active_id:
                company["data"] = data
                company["updated_at"] = now
                break
        else:
            active_id = active_id or uuid4().hex
            companies.append({"id": active_id, "data": data, "updated_at": now})
            session["active_company_id"] = active_id
        session["companies"] = companies
    session["data"] = data
    session.modified = True

def db_get_company(company_id):
    path = (f"{SUPABASE_TABLE}?select=id,data,updated_at"
            f"&owner_id=eq.{_q(SUPABASE_OWNER_ID)}&id=eq.{_q(company_id)}&limit=1")
    rows = _supabase_request("GET", path) or []
    if not rows:
        return None
    row = rows[0]
    return {
        "id": row.get("id"),
        "data": row.get("data") or blank_data(),
        "updated_at": (row.get("updated_at") or "").replace("T", " ")[:19]
    }

def db_save_company(company_id, data):
    company_id = company_id or uuid4().hex
    payload = {
        "id": company_id,
        "owner_id": SUPABASE_OWNER_ID,
        "data": data,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    path = f"{SUPABASE_TABLE}?on_conflict=id"
    rows = _supabase_request(
        "POST", path, payload,
        prefer="resolution=merge-duplicates,return=representation"
    ) or []
    row = rows[0] if rows else payload
    return {
        "id": row.get("id", company_id),
        "data": row.get("data", data),
        "updated_at": (row.get("updated_at") or payload["updated_at"]).replace("T", " ")[:19]
    }

def db_delete_company(company_id):
    path = (f"{SUPABASE_TABLE}?owner_id=eq.{_q(SUPABASE_OWNER_ID)}"
            f"&id=eq.{_q(company_id)}")
    _supabase_request("DELETE", path)

def ensure_company_store():
    if supabase_enabled():
        try:
            legacy_data = session.get("data")
            if legacy_data and legacy_data.get("perusahaan", {}).get("nama") and not session.get("active_company_id"):
                company = db_save_company(None, legacy_data)
                session["active_company_id"] = company["id"]
                session.pop("data", None)
                session.modified = True
            session.pop("storage_error", None)
            return db_get_companies()
        except Exception as exc:
            remember_storage_error(exc)
            return get_session_companies()

    return get_session_companies()

def get_companies():
    return ensure_company_store()

def get_active_company():
    active_id = session.get("active_company_id")
    if supabase_enabled():
        if not active_id:
            return get_session_active_company()
        try:
            session.pop("storage_error", None)
            return db_get_company(active_id)
        except Exception as exc:
            remember_storage_error(exc)
            return get_session_active_company()

    return get_session_active_company()

def get_data():
    active_company = get_active_company()
    if active_company:
        return active_company.get("data", blank_data())
    return blank_data()

def save_data(data):
    if supabase_enabled():
        try:
            active_id = session.get("active_company_id")
            company = db_save_company(active_id, data)
            session["active_company_id"] = company["id"]
            session.pop("storage_error", None)
            session.modified = True
            return
        except Exception as exc:
            remember_storage_error(exc)

    save_data_to_session(data)

def require_setup(data):
    """Return redirect jika perusahaan belum di-setup, else None."""
    p = data.get("perusahaan", {})
    if not p.get("nama"):
        return redirect(url_for("index"))
    return None

def require_jurnal(data):
    """Return redirect jika jurnal masih kosong, else None."""
    if not data.get("jurnal"):
        return redirect(url_for("jurnal"))
    return None

def get_active_api_key():
    """Prioritas: .env > session (diisi via UI)"""
    return GROQ_API_KEY_ENV or session.get("groq_api_key", "")

def clean_account_name(value):
    return _re.sub(r"^\[[^\]]+\]\s*", "", (value or "").strip())

def parse_amount(value):
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    s = _re.sub(r"[^\d,.\-]", "", s)
    if not s or s in ("-", ".", ","):
        return 0.0

    has_dot = "." in s
    has_comma = "," in s

    if has_dot and has_comma:
        decimal_sep = "." if s.rfind(".") > s.rfind(",") else ","
        thousand_sep = "," if decimal_sep == "." else "."
        s = s.replace(thousand_sep, "")
        s = s.replace(decimal_sep, ".")
    elif has_dot:
        parts = s.split(".")
        if len(parts) > 1 and all(len(part) == 3 for part in parts[1:]):
            s = "".join(parts)
    elif has_comma:
        parts = s.split(",")
        if len(parts) > 1 and all(len(part) == 3 for part in parts[1:]):
            s = "".join(parts)
        else:
            s = s.replace(",", ".")

    return float(s)

def _account_code_sort_key(account_name):
    code = ACCOUNT_CODES.get(account_name, "999")
    parts = []
    for part in code.split("."):
        parts.append(int(part) if part.isdigit() else 999)
    return (parts, account_name.lower())

def sort_buku_besar(buku_besar):
    return dict(sorted(buku_besar.items(), key=lambda item: _account_code_sort_key(item[0])))

def sort_account_options(account_codes):
    return [{"nama": name, "kode": code}
            for name, code in sorted(account_codes.items(),
                                     key=lambda item: _account_code_sort_key(item[0]))]

MONTHS_ID = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "agustus": 8,
    "september": 9, "oktober": 10, "november": 11, "desember": 12,
}

def parse_tanggal_indonesia(value):
    text = (value or "").strip()
    m = _re.match(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$", text, _re.I)
    if m:
        day = int(m.group(1))
        month = MONTHS_ID.get(m.group(2).lower())
        year = int(m.group(3))
        if month:
            return date(year, month, day)
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except Exception:
        return date.max

def sort_jurnal_entries(entries):
    return sorted(
        entries,
        key=lambda e: (parse_tanggal_indonesia(e.get("tanggal", "")), e.get("created_at", ""), e.get("id", 0))
    )

# ══════════════════════════════════════════════════════════════════════════
#  ROUTES — SIKLUS AKUNTANSI
# ══════════════════════════════════════════════════════════════════════════

# ── Home / Setup ───────────────────────────────────────────────────────────
@app.route("/")
def index():
    data = get_data()
    companies = get_companies()
    show_setup = (not companies) or request.args.get("mode") in ("setup", "edit")
    return render_template("index.html",
                           perusahaan=data.get("perusahaan", {}),
                           opening_balances=data.get("opening_balances", {}),
                           companies=companies,
                           show_setup=show_setup)

@app.route("/company/new")
def company_new():
    session.pop("active_company_id", None)
    if not supabase_enabled():
        session["data"] = blank_data()
    session.modified = True
    return redirect(url_for("index", mode="setup"))

@app.route("/company/select/<company_id>", methods=["POST"])
def company_select(company_id):
    for company in get_companies():
        if company.get("id") == company_id:
            session["active_company_id"] = company_id
            if not supabase_enabled():
                session["data"] = company.get("data", blank_data())
            session.modified = True
            return redirect(url_for("jurnal"))
    return redirect(url_for("index"))

@app.route("/company/edit/<company_id>")
def company_edit(company_id):
    for company in get_companies():
        if company.get("id") == company_id:
            session["active_company_id"] = company_id
            if not supabase_enabled():
                session["data"] = company.get("data", blank_data())
            session.modified = True
            return redirect(url_for("index", mode="edit"))
    return redirect(url_for("index"))

@app.route("/company/delete/<company_id>", methods=["POST"])
def company_delete(company_id):
    if supabase_enabled():
        try:
            db_delete_company(company_id)
            session.pop("storage_error", None)
        except Exception as exc:
            remember_storage_error(exc)
        if session.get("active_company_id") == company_id:
            session.pop("active_company_id", None)
        session.modified = True
        return redirect(url_for("index"))

    companies = [c for c in get_companies() if c.get("id") != company_id]
    session["companies"] = companies
    if session.get("active_company_id") == company_id:
        session.pop("active_company_id", None)
        session["data"] = blank_data()
    session.modified = True
    return redirect(url_for("index"))

@app.route("/setup", methods=["POST"])
def setup():
    data = get_data()
    data["perusahaan"] = {
        "nama":     request.form.get("nama_perusahaan", ""),
        "pemilik":  request.form.get("nama_pemilik", ""),
        "periode":  request.form.get("periode", ""),
        "jenis":    request.form.get("jenis_usaha", ""),
        "currency": request.form.get("currency", "IDR")
    }
    ob = {}
    for acc, d, k in zip(request.form.getlist("ob_account"),
                         request.form.getlist("ob_debit"),
                         request.form.getlist("ob_kredit")):
        acc = clean_account_name(acc)
        if acc:
            ob[acc] = {"debit": parse_amount(d),
                       "kredit": parse_amount(k)}
    data["opening_balances"] = ob
    save_data(data)
    return redirect(url_for("jurnal"))

# ── Jurnal Umum ────────────────────────────────────────────────────────────
@app.route("/jurnal")
def jurnal():
    data = get_data()
    if not data.get("perusahaan"):
        return redirect(url_for("index"))
    return render_template("jurnal.html",
                           perusahaan=data["perusahaan"],
                           jurnal=data.get("jurnal", []),
                           account_codes=ACCOUNT_CODES)

@app.route("/jurnal/add", methods=["POST"])
def jurnal_add():
    data = get_data()
    debit_entries  = [{"akun": clean_account_name(a), "jumlah": parse_amount(v)}
                      for a, v in zip(request.form.getlist("debit_account[]"),
                                      request.form.getlist("debit_amount[]"))
                      if clean_account_name(a) and v]
    kredit_entries = [{"akun": clean_account_name(a), "jumlah": parse_amount(v)}
                      for a, v in zip(request.form.getlist("kredit_account[]"),
                                      request.form.getlist("kredit_amount[]"))
                      if clean_account_name(a) and v]
    total_d = sum(e["jumlah"] for e in debit_entries)
    total_k = sum(e["jumlah"] for e in kredit_entries)
    if abs(total_d - total_k) > 0.01:
        return jsonify({"error": f"Debit (Rp {total_d:,.0f}) ≠ Kredit (Rp {total_k:,.0f})"}), 400
    jurnal_list = data.setdefault("jurnal", [])
    entry = {
        "id":             uuid4().hex,
        "created_at":     datetime.utcnow().isoformat(timespec="microseconds") + "Z",
        "tanggal":        request.form.get("tanggal", ""),
        "keterangan":     request.form.get("keterangan", ""),
        "debit_entries":  debit_entries,
        "kredit_entries": kredit_entries
    }
    jurnal_list.append(entry)
    data["jurnal"] = sort_jurnal_entries(jurnal_list)
    save_data(data)
    return jsonify({"success": True, "entry": entry})

@app.route("/jurnal/delete/<entry_id>", methods=["POST"])
def jurnal_delete(entry_id):
    data = get_data()
    jurnal_list = data.get("jurnal", [])
    try:
        idx = int(entry_id)
    except ValueError:
        idx = None

    if idx is not None and 0 <= idx < len(jurnal_list) and str(jurnal_list[idx].get("id", idx)) == entry_id:
        jurnal_list.pop(idx)
    else:
        jurnal_list = [e for e in jurnal_list if str(e.get("id")) != entry_id]

    data["jurnal"] = sort_jurnal_entries(jurnal_list)
    save_data(data)
    return redirect(url_for("jurnal"))

@app.route("/jurnal/clear", methods=["POST"])
def jurnal_clear():
    data = get_data()
    data["jurnal"] = []
    save_data(data)
    return redirect(url_for("jurnal"))

# ── Buku Besar ─────────────────────────────────────────────────────────────
@app.route("/buku_besar")
def buku_besar():
    data = get_data()
    if not data.get("jurnal"):
        return redirect(url_for("jurnal"))
    bb = build_buku_besar(data["jurnal"],
                          data.get("jurnal_penyesuaian"),
                          data.get("opening_balances"))
    bb = sort_buku_besar(bb)
    return render_template("buku_besar.html",
                           perusahaan=data["perusahaan"], buku_besar=bb)

# ── Neraca Saldo ───────────────────────────────────────────────────────────
@app.route("/neraca_saldo")
def neraca_saldo():
    data = get_data()
    redir = require_setup(data) or require_jurnal(data)
    if redir: return redir
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    return render_template("neraca_saldo.html",
                           perusahaan=data["perusahaan"], neraca_saldo=ns,
                           total_d=sum(r["debit"]  for r in ns),
                           total_k=sum(r["kredit"] for r in ns))

# ── Jurnal Penyesuaian ─────────────────────────────────────────────────────
@app.route("/penyesuaian")
def penyesuaian():
    data = get_data()
    redir = require_setup(data) or require_jurnal(data)
    if redir: return redir
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    return render_template("penyesuaian.html",
                           perusahaan=data["perusahaan"], neraca_saldo=ns,
                           jurnal_penyesuaian=data.get("jurnal_penyesuaian", []),
                           account_codes=ACCOUNT_CODES)

@app.route("/penyesuaian/add", methods=["POST"])
def penyesuaian_add():
    data = get_data()
    debit_entries  = [{"akun": clean_account_name(a), "jumlah": parse_amount(v)}
                      for a, v in zip(request.form.getlist("debit_account[]"),
                                      request.form.getlist("debit_amount[]"))
                      if clean_account_name(a) and v]
    kredit_entries = [{"akun": clean_account_name(a), "jumlah": parse_amount(v)}
                      for a, v in zip(request.form.getlist("kredit_account[]"),
                                      request.form.getlist("kredit_amount[]"))
                      if clean_account_name(a) and v]
    total_d = sum(e["jumlah"] for e in debit_entries)
    total_k = sum(e["jumlah"] for e in kredit_entries)
    if abs(total_d - total_k) > 0.01:
        return jsonify({"error": f"Debit (Rp {total_d:,.0f}) ≠ Kredit (Rp {total_k:,.0f})"}), 400
    entry = {
        "id":             len(data.get("jurnal_penyesuaian", [])),
        "tanggal":        request.form.get("tanggal", ""),
        "keterangan":     request.form.get("keterangan", ""),
        "debit_entries":  debit_entries,
        "kredit_entries": kredit_entries
    }
    data.setdefault("jurnal_penyesuaian", []).append(entry)
    save_data(data)
    return jsonify({"success": True, "entry": entry})

@app.route("/penyesuaian/delete/<int:idx>", methods=["POST"])
def penyesuaian_delete(idx):
    data = get_data()
    if 0 <= idx < len(data.get("jurnal_penyesuaian", [])):
        data["jurnal_penyesuaian"].pop(idx)
        save_data(data)
    return redirect(url_for("penyesuaian"))

# ── Kertas Kerja ───────────────────────────────────────────────────────────
@app.route("/kertas_kerja")
def kertas_kerja():
    data = get_data()
    redir = require_setup(data) or require_jurnal(data)
    if redir: return redir
    bb   = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns   = build_neraca_saldo(bb)
    kk   = build_kertas_kerja(ns, data.get("jurnal_penyesuaian", []))
    total = {k: sum(r[k] for r in kk) for k in
             ["ns_d","ns_k","ajp_d","ajp_k","nsd_d","nsd_k","lr_d","lr_k","ner_d","ner_k"]}
    laba = total["lr_k"] - total["lr_d"]
    return render_template("kertas_kerja.html",
                           perusahaan=data["perusahaan"],
                           kertas_kerja=kk, total=total, laba_bersih=laba)

# ── Laporan Keuangan ───────────────────────────────────────────────────────
@app.route("/laporan")
def laporan():
    data = get_data()
    redir = require_setup(data) or require_jurnal(data)
    if redir: return redir
    bb   = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns   = build_neraca_saldo(bb)
    kk   = build_kertas_kerja(ns, data.get("jurnal_penyesuaian", []))
    p    = data["perusahaan"]
    lap  = build_laporan(kk, p["nama"], p["pemilik"], p["periode"])
    return render_template("laporan.html", laporan=lap, perusahaan=p)

# ── Jurnal Penutup ─────────────────────────────────────────────────────────
@app.route("/jurnal_penutup")
def jurnal_penutup():
    data = get_data()
    redir = require_setup(data) or require_jurnal(data)
    if redir: return redir
    bb   = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns   = build_neraca_saldo(bb)
    kk   = build_kertas_kerja(ns, data.get("jurnal_penyesuaian", []))
    p    = data["perusahaan"]
    lap  = build_laporan(kk, p["nama"], p["pemilik"], p["periode"])
    jp   = build_jurnal_penutup(lap, p["periode"])
    return render_template("jurnal_penutup.html", perusahaan=p, jurnal_penutup=jp)

# ── Reset ──────────────────────────────────────────────────────────────────
@app.route("/reset", methods=["POST"])
def reset():
    if supabase_enabled() and session.get("active_company_id"):
        try:
            db_delete_company(session.get("active_company_id"))
            session.pop("storage_error", None)
        except Exception as exc:
            remember_storage_error(exc)
        session.pop("active_company_id", None)
        session.modified = True
    else:
        session.clear()
    return redirect(url_for("index"))

# ── Export Excel ───────────────────────────────────────────────────────────
def _excel_response(data_bytes, filename):
    return send_file(io.BytesIO(data_bytes),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=filename)

@app.route("/export/jurnal")
def export_jurnal_route():
    data = get_data(); p = data["perusahaan"]
    return _excel_response(export_jurnal(data["jurnal"], p, p.get("currency","IDR")),
                           f"Jurnal_Umum_{p['periode'].replace(' ','_')}.xlsx")

@app.route("/export/buku_besar")
def export_buku_besar_route():
    data = get_data(); p = data["perusahaan"]
    bb = build_buku_besar(data["jurnal"], data.get("jurnal_penyesuaian"), data.get("opening_balances"))
    bb = sort_buku_besar(bb)
    return _excel_response(export_buku_besar(bb, p, p.get("currency","IDR")),
                           f"Buku_Besar_{p['periode'].replace(' ','_')}.xlsx")

@app.route("/export/neraca_saldo")
def export_neraca_saldo_route():
    data = get_data(); p = data["perusahaan"]
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    return _excel_response(export_neraca_saldo(ns, p, p.get("currency","IDR")),
                           f"Neraca_Saldo_{p['periode'].replace(' ','_')}.xlsx")

@app.route("/export/penyesuaian")
def export_penyesuaian_route():
    data = get_data(); p = data["perusahaan"]
    return _excel_response(export_penyesuaian(data.get("jurnal_penyesuaian",[]), p, p.get("currency","IDR")),
                           f"Jurnal_Penyesuaian_{p['periode'].replace(' ','_')}.xlsx")

@app.route("/export/kertas_kerja")
def export_kertas_kerja_route():
    data = get_data(); p = data["perusahaan"]
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    kk = build_kertas_kerja(ns, data.get("jurnal_penyesuaian", []))
    laba = sum(r["lr_k"] for r in kk) - sum(r["lr_d"] for r in kk)
    return _excel_response(export_kertas_kerja(kk, p, laba, p.get("currency","IDR")),
                           f"Kertas_Kerja_{p['periode'].replace(' ','_')}.xlsx")

@app.route("/export/laporan")
def export_laporan_route():
    data = get_data(); p = data["perusahaan"]
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    kk = build_kertas_kerja(ns, data.get("jurnal_penyesuaian", []))
    lap = build_laporan(kk, p["nama"], p["pemilik"], p["periode"])
    return _excel_response(export_laporan(lap, p.get("currency","IDR")),
                           f"Laporan_Keuangan_{p['periode'].replace(' ','_')}.xlsx")

@app.route("/export/jurnal_penutup")
def export_jurnal_penutup_route():
    data = get_data(); p = data["perusahaan"]
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    kk = build_kertas_kerja(ns, data.get("jurnal_penyesuaian", []))
    lap = build_laporan(kk, p["nama"], p["pemilik"], p["periode"])
    jp  = build_jurnal_penutup(lap, p["periode"])
    return _excel_response(export_jurnal_penutup(jp, p, p.get("currency","IDR")),
                           f"Jurnal_Penutup_{p['periode'].replace(' ','_')}.xlsx")

# ══════════════════════════════════════════════════════════════════════════
#  AI ASSISTANT — Groq + LangChain Memory
#  Pola wrapper seperti contoh Node.js (OpenAI/Gemini/Claude)
# ══════════════════════════════════════════════════════════════════════════

def _md_to_html(text: str) -> str:
    """Markdown ringan → HTML aman untuk chat bubble."""
    safe = _html.escape(text)
    safe = _re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', safe)
    safe = _re.sub(r'\*(.*?)\*',     r'<em>\1</em>',         safe)
    safe = _re.sub(r'`([^`]+)`',
                   r'<code style="background:rgba(0,0,0,0.08);padding:1px 5px;'
                   r'border-radius:3px;font-family:monospace;">\1</code>', safe)
    def _block(m):
        inner = m.group(1).strip()
        return (f'<div style="background:rgba(0,0,0,0.04);border:1px solid rgba(0,0,0,0.1);'
                f'border-radius:6px;padding:8px 10px;margin:6px 0;font-family:monospace;'
                f'font-size:11px;overflow:auto;max-height:200px;">'
                f'<pre style="white-space:pre-wrap;margin:0;">{inner}</pre></div>')
    safe = _re.sub(r'```(?:json)?([\s\S]*?)```', _block, safe)
    safe = safe.replace('\n', '<br>')
    return safe

def _history_for_template(raw: list) -> list:
    return [{"role": m["role"], "content": m["content"],
             "content_html": _md_to_html(m["content"]) if m["role"] == "ai" else m["content"]}
            for m in raw]

# ── Halaman AI ─────────────────────────────────────────────────────────────
@app.route("/ai")
def ai_assistant():
    data = get_data()
    ch   = session.get("ai_chat_history", [])
    return render_template(
        "ai_assistant.html",
        perusahaan         = data.get("perusahaan", {}),
        chat_history       = _history_for_template(ch),
        session_has_apikey = bool(get_active_api_key()),
        saved_api_key      = session.get("groq_api_key", ""),  # tidak expose .env key ke UI
        key_from_env       = bool(GROQ_API_KEY_ENV)
    )

# ── Simpan API key via UI (jika tidak pakai .env) ──────────────────────────
@app.route("/ai/set_key", methods=["POST"])
def ai_set_key():
    body = request.get_json() or {}
    key  = body.get("api_key", "").strip()
    if not key:
        return jsonify({"success": False, "error": "API key kosong"})
    if not key.startswith("gsk_"):
        return jsonify({"success": False, "error": "Harus diawali gsk_"})
    session["groq_api_key"] = key
    session.modified = True
    return jsonify({"success": True})

# ── Chat endpoint ──────────────────────────────────────────────────────────
@app.route("/ai/chat", methods=["POST"])
def ai_chat():
    try:
        return _ai_chat_handler()
    except Exception as e:
        import traceback
        traceback.print_exc()  # print ke terminal Flask
        return jsonify({"error": f"Server error: {str(e)[:300]}"}), 500

def _ai_chat_handler():
    # Import Groq agent
    try:
        from llm.groq_agent import (
            chat_with_memory, parse_journal_from_response, get_used_accounts
        )
    except ImportError as e:
        return jsonify({"error": (
            f"groq belum terinstall! Jalankan: pip install groq python-dotenv ({e})"
        )}), 500

    body         = request.get_json() or {}
    user_message = body.get("message", "").strip()
    model        = body.get("model", "llama-3.3-70b-versatile")

    # Prioritas key: .env > body (dari UI) > session
    api_key = get_active_api_key() or body.get("api_key", "").strip()

    if not user_message:
        return jsonify({"error": "Pesan kosong"}), 400
    if not api_key:
        return jsonify({"error": "GROQ_API_KEY belum diset. "
                                  "Isi di file .env atau masukkan via UI."}), 400

    data    = get_data()
    history = session.get("ai_chat_history", [])

    try:
        response_text, updated_history = chat_with_memory(
            api_key           = api_key,
            user_message      = user_message,
            chat_history      = history,
            perusahaan        = data.get("perusahaan", {}),
            existing_accounts = get_used_accounts(data.get("jurnal", [])),
            model             = model
        )
    except Exception as e:
        err = str(e)
        if "401" in err or "authentication" in err.lower() or "api_key" in err.lower():
            return jsonify({"error": "API key tidak valid."}), 400
        return jsonify({"error": f"Groq error: {err[:250]}"}), 500

    session["ai_chat_history"] = updated_history
    session.modified = True

    return jsonify({
        "success":       True,
        "response_html": _md_to_html(response_text),
        "entries":       parse_journal_from_response(response_text) or [],
        "chat_history":  updated_history
    })

# ── Debug: cek apakah .env terbaca (hapus di production) ───────────────────
@app.route("/debug/env")
def debug_env():
    import os
    key = os.getenv("GROQ_API_KEY", "")
    return jsonify({
        "GROQ_API_KEY_set": bool(key),
        "GROQ_API_KEY_preview": key[:8] + "..." if key else "KOSONG",
        "dotenv_loaded": GROQ_API_KEY_ENV != "",
        "cwd": os.getcwd(),
        "env_file_exists": os.path.exists(".env")
    })

@app.route("/debug/storage")
def debug_storage():
    status = supabase_status()
    if status.get("url"):
        status["url"] = status["url"].replace("https://", "").split(".")[0] + ".supabase.co"
    return jsonify(status)

# ── Test endpoint: cek semua dependency ────────────────────────────────────
@app.route("/debug/ai_test")
def debug_ai_test():
    results = {}
    # Test groq
    try:
        import groq
        results["groq"] = f"OK (v{groq.__version__})"
    except ImportError as e:
        results["groq"] = f"MISSING — pip install groq ({e})"
    # Test llm module
    try:
        from llm.groq_agent import chat_with_memory
        results["llm_module"] = "OK"
    except Exception as e:
        results["llm_module"] = f"ERROR: {e}"
    # Test API key
    key = get_active_api_key()
    results["api_key"] = f"OK ({key[:8]}...)" if key else "KOSONG — isi di .env atau UI"
    results["env_file"] = str(__import__('os').path.exists(".env"))
    return jsonify(results)

# ── Setup perusahaan via AI ────────────────────────────────────────────────
@app.route("/ai/setup_company", methods=["POST"])
def ai_setup_company():
    body = request.get_json() or {}
    nama    = body.get("nama", "").strip()
    pemilik = body.get("pemilik", "").strip()
    periode = body.get("periode", "").strip()
    jenis   = body.get("jenis", "").strip()
    currency= body.get("currency", "IDR").strip()
    if not nama:
        return jsonify({"success": False, "error": "Nama perusahaan wajib diisi"})
    data = get_data()
    data["perusahaan"] = {
        "nama": nama, "pemilik": pemilik,
        "periode": periode, "jenis": jenis, "currency": currency
    }
    save_data(data)
    return jsonify({"success": True, "perusahaan": data["perusahaan"]})

# ── Clear memory ───────────────────────────────────────────────────────────
@app.route("/ai/clear_memory", methods=["POST"])
def ai_clear_memory():
    session.pop("ai_chat_history", None)
    session.modified = True
    return jsonify({"success": True})

# ══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT  ← selalu di paling bawah!
# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(debug=True, port=5000)
