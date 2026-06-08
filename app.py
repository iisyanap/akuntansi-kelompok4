# ══════════════════════════════════════════════════════════════════════════
#  AccSys — Sistem Akuntansi  |  Flask + Groq + Supabase
# ══════════════════════════════════════════════════════════════════════════
import os
import io
import json
import html as _html
import re as _re
from functools import wraps
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
from logic import database as db

# ── App config ─────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "akuntansi_secret_2024")

# Groq API key: baca dari .env, bisa di-override via UI
GROQ_API_KEY_ENV = os.getenv("GROQ_API_KEY", "")

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
    perusahaan = _get_company()
    currency = perusahaan.get("currency", "IDR") if perusahaan else "IDR"
    symbol = "$" if currency == "USD" else "Rp"
    has_key = bool(GROQ_API_KEY_ENV or session.get("groq_api_key"))
    return dict(
        currency=currency, currency_symbol=symbol, groq_key_set=has_key,
        user_email=session.get("user_email", "")
    )

# ── Auth decorator ─────────────────────────────────────────────────────────


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth_page"))
        return f(*args, **kwargs)
    return decorated

# ── Helpers: Supabase-backed data ──────────────────────────────────────────


def _uid():
    return session.get("user_id", "")


def _token():
    return session.get("access_token", "")


def _get_company():
    """Return company dict for current user, or empty dict."""
    uid = _uid()
    token = _token()
    if not uid or not token:
        return {}
    try:
        return db.get_company(token, uid) or {}
    except Exception:
        return {}


def _get_journals(entry_type="jurnal"):
    uid = _uid()
    token = _token()
    if not uid or not token:
        return []
    try:
        return db.get_journals(token, uid, entry_type)
    except Exception:
        return []


def _get_opening_balances():
    co = _get_company()
    return co.get("opening_balances", {}) if co else {}


def get_active_api_key():
    """Prioritas: .env > session (diisi via UI)"""
    return GROQ_API_KEY_ENV or session.get("groq_api_key", "")

# ══════════════════════════════════════════════════════════════════════════
#  AUTH ROUTES
# ══════════════════════════════════════════════════════════════════════════


@app.route("/auth")
def auth_page():
    if session.get("user_id"):
        return redirect(url_for("index"))
    return render_template("auth.html")


@app.route("/auth/login", methods=["POST"])
def auth_login():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    if not email or not password:
        return render_template("auth.html", error="Email dan password wajib diisi.", email=email)
    resp, err = db.auth_sign_in(email, password)
    if err:
        msg = "Login gagal."
        if "invalid" in err.lower() or "email" in err.lower():
            msg = "Email atau password salah."
        elif "not confirmed" in err.lower():
            msg = "Email belum dikonfirmasi. Cek inbox Anda."
        return render_template("auth.html", error=msg, email=email)
    sess = resp.session
    user = resp.user
    session["user_id"] = user.id
    session["access_token"] = sess.access_token
    session["user_email"] = user.email
    session.permanent = True
    return redirect(url_for("index"))


@app.route("/auth/register", methods=["POST"])
def auth_register():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("password_confirm", "")
    if not email or not password:
        return render_template("auth.html", error="Email dan password wajib diisi.", email=email)
    if password != confirm:
        return render_template("auth.html", error="Password tidak cocok.", email=email)
    if len(password) < 6:
        return render_template("auth.html", error="Password minimal 6 karakter.", email=email)
    resp, err = db.auth_sign_up(email, password)
    if err:
        msg = "Pendaftaran gagal."
        if "already" in err.lower():
            msg = "Email sudah terdaftar. Silakan masuk."
        return render_template("auth.html", error=msg, email=email)
    if resp and resp.session:
        sess = resp.session
        user = resp.user
        session["user_id"] = user.id
        session["access_token"] = sess.access_token
        session["user_email"] = user.email
        session.permanent = True
        return redirect(url_for("index"))
    return render_template("auth.html",
                           success="Akun berhasil dibuat! Cek email untuk konfirmasi, lalu masuk.",
                           email=email)


@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect(url_for("auth_page"))

# ══════════════════════════════════════════════════════════════════════════
#  ROUTES — SIKLUS AKUNTANSI
# ══════════════════════════════════════════════════════════════════════════

# ── Home / Setup ───────────────────────────────────────────────────────────


@app.route("/")
@login_required
def index():
    perusahaan = _get_company()
    return render_template("index.html",
                           perusahaan=perusahaan,
                           opening_balances=perusahaan.get("opening_balances", {}))


@app.route("/setup", methods=["POST"])
@login_required
def setup():
    ob = {}
    for acc, d, k in zip(request.form.getlist("ob_account"),
                         request.form.getlist("ob_debit"),
                         request.form.getlist("ob_kredit")):
        if acc.strip():
            ob[acc.strip()] = {"debit": float(d) if d else 0,
                               "kredit": float(k) if k else 0}
    company_data = {
        "nama":             request.form.get("nama_perusahaan", ""),
        "pemilik":          request.form.get("nama_pemilik", ""),
        "periode":          request.form.get("periode", ""),
        "jenis":            request.form.get("jenis_usaha", ""),
        "currency":         request.form.get("currency", "IDR"),
        "opening_balances": ob,
    }
    db.save_company(_token(), _uid(), company_data)
    return redirect(url_for("jurnal"))

# ── Jurnal Umum ────────────────────────────────────────────────────────────


@app.route("/jurnal")
@login_required
def jurnal():
    perusahaan = _get_company()
    if not perusahaan.get("nama"):
        return redirect(url_for("index"))
    jurnal_list = _get_journals("jurnal")
    return render_template("jurnal.html",
                           perusahaan=perusahaan,
                           jurnal=jurnal_list,
                           account_codes=ACCOUNT_CODES)


@app.route("/jurnal/add", methods=["POST"])
@login_required
def jurnal_add():
    debit_entries = [{"akun": a.strip(), "jumlah": float(v)}
                     for a, v in zip(request.form.getlist("debit_account[]"),
                                     request.form.getlist("debit_amount[]"))
                     if a.strip() and v]
    kredit_entries = [{"akun": a.strip(), "jumlah": float(v)}
                      for a, v in zip(request.form.getlist("kredit_account[]"),
                                      request.form.getlist("kredit_amount[]"))
                      if a.strip() and v]
    total_d = sum(e["jumlah"] for e in debit_entries)
    total_k = sum(e["jumlah"] for e in kredit_entries)
    if abs(total_d - total_k) > 0.01:
        return jsonify({"error": f"Debit (Rp {total_d:,.0f}) ≠ Kredit (Rp {total_k:,.0f})"}), 400
    entry = {
        "tanggal":        request.form.get("tanggal", ""),
        "keterangan":     request.form.get("keterangan", ""),
        "debit_entries":  debit_entries,
        "kredit_entries": kredit_entries,
    }
    saved = db.add_journal(_token(), _uid(), entry, "jurnal")
    return jsonify({"success": True, "entry": saved})


@app.route("/jurnal/delete/<int:idx>", methods=["POST"])
@login_required
def jurnal_delete(idx):
    db.delete_journal_by_index(_token(), _uid(), idx, "jurnal")
    return redirect(url_for("jurnal"))


@app.route("/jurnal/clear", methods=["POST"])
@login_required
def jurnal_clear():
    db.clear_journals(_token(), _uid(), "jurnal")
    return redirect(url_for("jurnal"))

# ── Buku Besar ─────────────────────────────────────────────────────────────


@app.route("/buku_besar")
@login_required
def buku_besar():
    jurnal_list = _get_journals("jurnal")
    if not jurnal_list:
        return redirect(url_for("jurnal"))
    penyesuaian = _get_journals("penyesuaian")
    ob = _get_opening_balances()
    bb = build_buku_besar(jurnal_list, penyesuaian or None, ob or None)
    perusahaan = _get_company()
    return render_template("buku_besar.html",
                           perusahaan=perusahaan, buku_besar=bb)

# ── Neraca Saldo ───────────────────────────────────────────────────────────


@app.route("/neraca_saldo")
@login_required
def neraca_saldo():
    perusahaan = _get_company()
    jurnal_list = _get_journals("jurnal")
    if not perusahaan.get("nama"):
        return redirect(url_for("index"))
    if not jurnal_list:
        return redirect(url_for("jurnal"))
    ob = _get_opening_balances()
    bb = build_buku_besar(jurnal_list, None, ob or None)
    ns = build_neraca_saldo(bb)
    return render_template("neraca_saldo.html",
                           perusahaan=perusahaan, neraca_saldo=ns,
                           total_d=sum(r["debit"] for r in ns),
                           total_k=sum(r["kredit"] for r in ns))

# ── Jurnal Penyesuaian ─────────────────────────────────────────────────────


@app.route("/penyesuaian")
@login_required
def penyesuaian():
    perusahaan = _get_company()
    jurnal_list = _get_journals("jurnal")
    if not perusahaan.get("nama"):
        return redirect(url_for("index"))
    if not jurnal_list:
        return redirect(url_for("jurnal"))
    ob = _get_opening_balances()
    bb = build_buku_besar(jurnal_list, None, ob or None)
    ns = build_neraca_saldo(bb)
    jp = _get_journals("penyesuaian")
    return render_template("penyesuaian.html",
                           perusahaan=perusahaan, neraca_saldo=ns,
                           jurnal_penyesuaian=jp,
                           account_codes=ACCOUNT_CODES)


@app.route("/penyesuaian/add", methods=["POST"])
@login_required
def penyesuaian_add():
    debit_entries = [{"akun": a.strip(), "jumlah": float(v)}
                     for a, v in zip(request.form.getlist("debit_account[]"),
                                     request.form.getlist("debit_amount[]"))
                     if a.strip() and v]
    kredit_entries = [{"akun": a.strip(), "jumlah": float(v)}
                      for a, v in zip(request.form.getlist("kredit_account[]"),
                                      request.form.getlist("kredit_amount[]"))
                      if a.strip() and v]
    total_d = sum(e["jumlah"] for e in debit_entries)
    total_k = sum(e["jumlah"] for e in kredit_entries)
    if abs(total_d - total_k) > 0.01:
        return jsonify({"error": f"Debit (Rp {total_d:,.0f}) ≠ Kredit (Rp {total_k:,.0f})"}), 400
    entry = {
        "tanggal":        request.form.get("tanggal", ""),
        "keterangan":     request.form.get("keterangan", ""),
        "debit_entries":  debit_entries,
        "kredit_entries": kredit_entries,
    }
    saved = db.add_journal(_token(), _uid(), entry, "penyesuaian")
    return jsonify({"success": True, "entry": saved})


@app.route("/penyesuaian/delete/<int:idx>", methods=["POST"])
@login_required
def penyesuaian_delete(idx):
    db.delete_journal_by_index(_token(), _uid(), idx, "penyesuaian")
    return redirect(url_for("penyesuaian"))

# ── Kertas Kerja ───────────────────────────────────────────────────────────


@app.route("/kertas_kerja")
@login_required
def kertas_kerja():
    perusahaan = _get_company()
    jurnal_list = _get_journals("jurnal")
    if not perusahaan.get("nama"):
        return redirect(url_for("index"))
    if not jurnal_list:
        return redirect(url_for("jurnal"))
    ob = _get_opening_balances()
    bb = build_buku_besar(jurnal_list, None, ob or None)
    ns = build_neraca_saldo(bb)
    jp = _get_journals("penyesuaian")
    kk = build_kertas_kerja(ns, jp)
    total = {k: sum(r[k] for r in kk) for k in
             ["ns_d", "ns_k", "ajp_d", "ajp_k", "nsd_d", "nsd_k", "lr_d", "lr_k", "ner_d", "ner_k"]}
    laba = total["lr_k"] - total["lr_d"]
    return render_template("kertas_kerja.html",
                           perusahaan=perusahaan,
                           kertas_kerja=kk, total=total, laba_bersih=laba)

# ── Laporan Keuangan ───────────────────────────────────────────────────────


@app.route("/laporan")
@login_required
def laporan():
    perusahaan = _get_company()
    jurnal_list = _get_journals("jurnal")
    if not perusahaan.get("nama"):
        return redirect(url_for("index"))
    if not jurnal_list:
        return redirect(url_for("jurnal"))
    ob = _get_opening_balances()
    bb = build_buku_besar(jurnal_list, None, ob or None)
    ns = build_neraca_saldo(bb)
    jp = _get_journals("penyesuaian")
    kk = build_kertas_kerja(ns, jp)
    p = perusahaan
    lap = build_laporan(kk, p["nama"], p.get(
        "pemilik", ""), p.get("periode", ""))
    return render_template("laporan.html", laporan=lap, perusahaan=p)

# ── Jurnal Penutup ─────────────────────────────────────────────────────────


@app.route("/jurnal_penutup")
@login_required
def jurnal_penutup():
    perusahaan = _get_company()
    jurnal_list = _get_journals("jurnal")
    if not perusahaan.get("nama"):
        return redirect(url_for("index"))
    if not jurnal_list:
        return redirect(url_for("jurnal"))
    ob = _get_opening_balances()
    bb = build_buku_besar(jurnal_list, None, ob or None)
    ns = build_neraca_saldo(bb)
    jp = _get_journals("penyesuaian")
    kk = build_kertas_kerja(ns, jp)
    p = perusahaan
    lap = build_laporan(kk, p["nama"], p.get(
        "pemilik", ""), p.get("periode", ""))
    jpen = build_jurnal_penutup(lap, p.get("periode", ""))
    return render_template("jurnal_penutup.html", perusahaan=p, jurnal_penutup=jpen)

# ── Reset ──────────────────────────────────────────────────────────────────


@app.route("/reset", methods=["POST"])
@login_required
def reset():
    try:
        db.delete_all_user_data(_token(), _uid())
    except Exception:
        pass
    uid = session.get("user_id")
    token = session.get("access_token")
    email = session.get("user_email")
    session.clear()
    session["user_id"] = uid
    session["access_token"] = token
    session["user_email"] = email
    return redirect(url_for("index"))

# ── Export Excel ───────────────────────────────────────────────────────────


def _excel_response(data_bytes, filename):
    return send_file(io.BytesIO(data_bytes),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=filename)


@app.route("/export/jurnal")
@login_required
def export_jurnal_route():
    p = _get_company()
    jl = _get_journals("jurnal")
    return _excel_response(export_jurnal(jl, p, p.get("currency", "IDR")),
                           f"Jurnal_Umum_{p.get('periode','').replace(' ','_')}.xlsx")


@app.route("/export/buku_besar")
@login_required
def export_buku_besar_route():
    p = _get_company()
    jl = _get_journals("jurnal")
    ps = _get_journals("penyesuaian")
    ob = _get_opening_balances()
    bb = build_buku_besar(jl, ps or None, ob or None)
    return _excel_response(export_buku_besar(bb, p, p.get("currency", "IDR")),
                           f"Buku_Besar_{p.get('periode','').replace(' ','_')}.xlsx")


@app.route("/export/neraca_saldo")
@login_required
def export_neraca_saldo_route():
    p = _get_company()
    jl = _get_journals("jurnal")
    ob = _get_opening_balances()
    bb = build_buku_besar(jl, None, ob or None)
    ns = build_neraca_saldo(bb)
    return _excel_response(export_neraca_saldo(ns, p, p.get("currency", "IDR")),
                           f"Neraca_Saldo_{p.get('periode','').replace(' ','_')}.xlsx")


@app.route("/export/penyesuaian")
@login_required
def export_penyesuaian_route():
    p = _get_company()
    jp = _get_journals("penyesuaian")
    return _excel_response(export_penyesuaian(jp, p, p.get("currency", "IDR")),
                           f"Jurnal_Penyesuaian_{p.get('periode','').replace(' ','_')}.xlsx")


@app.route("/export/kertas_kerja")
@login_required
def export_kertas_kerja_route():
    p = _get_company()
    jl = _get_journals("jurnal")
    ob = _get_opening_balances()
    bb = build_buku_besar(jl, None, ob or None)
    ns = build_neraca_saldo(bb)
    jp = _get_journals("penyesuaian")
    kk = build_kertas_kerja(ns, jp)
    laba = sum(r["lr_k"] for r in kk) - sum(r["lr_d"] for r in kk)
    return _excel_response(export_kertas_kerja(kk, p, laba, p.get("currency", "IDR")),
                           f"Kertas_Kerja_{p.get('periode','').replace(' ','_')}.xlsx")


@app.route("/export/laporan")
@login_required
def export_laporan_route():
    p = _get_company()
    jl = _get_journals("jurnal")
    ob = _get_opening_balances()
    bb = build_buku_besar(jl, None, ob or None)
    ns = build_neraca_saldo(bb)
    jp = _get_journals("penyesuaian")
    kk = build_kertas_kerja(ns, jp)
    lap = build_laporan(kk, p["nama"], p.get(
        "pemilik", ""), p.get("periode", ""))
    return _excel_response(export_laporan(lap, p.get("currency", "IDR")),
                           f"Laporan_Keuangan_{p.get('periode','').replace(' ','_')}.xlsx")


@app.route("/export/jurnal_penutup")
@login_required
def export_jurnal_penutup_route():
    p = _get_company()
    jl = _get_journals("jurnal")
    ob = _get_opening_balances()
    bb = build_buku_besar(jl, None, ob or None)
    ns = build_neraca_saldo(bb)
    jp = _get_journals("penyesuaian")
    kk = build_kertas_kerja(ns, jp)
    lap = build_laporan(kk, p["nama"], p.get(
        "pemilik", ""), p.get("periode", ""))
    jpen = build_jurnal_penutup(lap, p.get("periode", ""))
    return _excel_response(export_jurnal_penutup(jpen, p, p.get("currency", "IDR")),
                           f"Jurnal_Penutup_{p.get('periode','').replace(' ','_')}.xlsx")

# ══════════════════════════════════════════════════════════════════════════
#  AI ASSISTANT — Groq + LangChain Memory
# ══════════════════════════════════════════════════════════════════════════


def _md_to_html(text: str) -> str:
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
@login_required
def ai_assistant():
    perusahaan = _get_company()
    ch = db.get_chat_history(_token(), _uid())
    return render_template(
        "ai_assistant.html",
        perusahaan=perusahaan,
        chat_history=_history_for_template(ch),
        session_has_apikey=bool(get_active_api_key()),
        saved_api_key=session.get("groq_api_key", ""),
        key_from_env=bool(GROQ_API_KEY_ENV)
    )


@app.route("/ai/set_key", methods=["POST"])
@login_required
def ai_set_key():
    body = request.get_json() or {}
    key = body.get("api_key", "").strip()
    if not key:
        return jsonify({"success": False, "error": "API key kosong"})
    if not key.startswith("gsk_"):
        return jsonify({"success": False, "error": "Harus diawali gsk_"})
    session["groq_api_key"] = key
    session.modified = True
    return jsonify({"success": True})


@app.route("/ai/chat", methods=["POST"])
@login_required
def ai_chat():
    try:
        return _ai_chat_handler()
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Server error: {str(e)[:300]}"}), 500


def _ai_chat_handler():
    try:
        from llm.groq_agent import (
            chat_with_memory, parse_journal_from_response, get_used_accounts
        )
    except ImportError as e:
        return jsonify({"error": (
            f"groq belum terinstall! Jalankan: pip install groq python-dotenv ({e})"
        )}), 500

    body = request.get_json() or {}
    user_message = body.get("message", "").strip()
    model = body.get("model", "llama-3.3-70b-versatile")
    api_key = get_active_api_key() or body.get("api_key", "").strip()

    if not user_message:
        return jsonify({"error": "Pesan kosong"}), 400
    if not api_key:
        return jsonify({"error": "GROQ_API_KEY belum diset. "
                        "Isi di file .env atau masukkan via UI."}), 400

    perusahaan = _get_company()
    jurnal_list = _get_journals("jurnal")
    history = db.get_chat_history(_token(), _uid())

    try:
        response_text, updated_history = chat_with_memory(
            api_key=api_key,
            user_message=user_message,
            chat_history=history,
            perusahaan=perusahaan,
            existing_accounts=get_used_accounts(jurnal_list),
            model=model
        )
    except Exception as e:
        err = str(e)
        if "401" in err or "authentication" in err.lower() or "api_key" in err.lower():
            return jsonify({"error": "API key tidak valid."}), 400
        return jsonify({"error": f"Groq error: {err[:250]}"}), 500

    db.save_chat_history(_token(), _uid(), updated_history)

    return jsonify({
        "success":       True,
        "response_html": _md_to_html(response_text),
        "entries":       parse_journal_from_response(response_text) or [],
        "chat_history":  updated_history
    })


@app.route("/ai/setup_company", methods=["POST"])
@login_required
def ai_setup_company():
    body = request.get_json() or {}
    nama = body.get("nama", "").strip()
    pemilik = body.get("pemilik", "").strip()
    periode = body.get("periode", "").strip()
    jenis = body.get("jenis", "").strip()
    currency = body.get("currency", "IDR").strip()
    if not nama:
        return jsonify({"success": False, "error": "Nama perusahaan wajib diisi"})
    company_data = {
        "nama": nama, "pemilik": pemilik,
        "periode": periode, "jenis": jenis, "currency": currency,
        "opening_balances": _get_opening_balances(),
    }
    db.save_company(_token(), _uid(), company_data)
    return jsonify({"success": True, "perusahaan": company_data})


@app.route("/ai/clear_memory", methods=["POST"])
@login_required
def ai_clear_memory():
    db.clear_chat_history(_token(), _uid())
    return jsonify({"success": True})

# ── Debug ──────────────────────────────────────────────────────────────────


@app.route("/debug/env")
def debug_env():
    key = os.getenv("GROQ_API_KEY", "")
    return jsonify({
        "GROQ_API_KEY_set": bool(key),
        "GROQ_API_KEY_preview": key[:8] + "..." if key else "KOSONG",
        "dotenv_loaded": GROQ_API_KEY_ENV != "",
        "cwd": os.getcwd(),
        "env_file_exists": os.path.exists(".env"),
        "SUPABASE_URL_set": bool(os.getenv("SUPABASE_URL")),
    })


@app.route("/debug/ai_test")
def debug_ai_test():
    results = {}
    try:
        import groq
        results["groq"] = f"OK (v{groq.__version__})"
    except ImportError as e:
        results["groq"] = f"MISSING — pip install groq ({e})"
    try:
        from llm.groq_agent import chat_with_memory
        results["llm_module"] = "OK"
    except Exception as e:
        results["llm_module"] = f"ERROR: {e}"
    key = get_active_api_key()
    results["api_key"] = f"OK ({key[:8]}...)" if key else "KOSONG — isi di .env atau UI"
    results["env_file"] = str(__import__('os').path.exists(".env"))
    results["supabase_url"] = "OK" if os.getenv("SUPABASE_URL") else "MISSING"
    return jsonify(results)


# ══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(debug=True, port=5000)
