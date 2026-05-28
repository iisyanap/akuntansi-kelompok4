# ══════════════════════════════════════════════════════════════════════════
#  AccSys — Sistem Akuntansi  |  Flask + Groq + LangChain
# ══════════════════════════════════════════════════════════════════════════
import os, io, json, html as _html, re as _re
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
    data = session.get("data", {})
    currency = data.get("perusahaan", {}).get("currency", "IDR")
    symbol   = "$" if currency == "USD" else "Rp"
    # Cek apakah key tersedia (env atau session)
    has_key  = bool(GROQ_API_KEY_ENV or session.get("groq_api_key"))
    return dict(currency=currency, currency_symbol=symbol, groq_key_set=has_key)

# ── Helpers ────────────────────────────────────────────────────────────────
def get_data():
    return session.get("data", {
        "perusahaan": {}, "opening_balances": {},
        "jurnal": [], "jurnal_penyesuaian": [], "penyesuaian_info": []
    })

def save_data(data):
    session["data"] = data
    session.modified = True

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

# ══════════════════════════════════════════════════════════════════════════
#  ROUTES — SIKLUS AKUNTANSI
# ══════════════════════════════════════════════════════════════════════════

# ── Home / Setup ───────────────────────────────────────────────────────────
@app.route("/")
def index():
    data = get_data()
    return render_template("index.html",
                           perusahaan=data.get("perusahaan", {}),
                           opening_balances=data.get("opening_balances", {}))

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
        if acc.strip():
            ob[acc.strip()] = {"debit": float(d) if d else 0,
                               "kredit": float(k) if k else 0}
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
    debit_entries  = [{"akun": a.strip(), "jumlah": float(v)}
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
        "id":             len(data.get("jurnal", [])),
        "tanggal":        request.form.get("tanggal", ""),
        "keterangan":     request.form.get("keterangan", ""),
        "debit_entries":  debit_entries,
        "kredit_entries": kredit_entries
    }
    data.setdefault("jurnal", []).append(entry)
    save_data(data)
    return jsonify({"success": True, "entry": entry})

@app.route("/jurnal/delete/<int:idx>", methods=["POST"])
def jurnal_delete(idx):
    data = get_data()
    if 0 <= idx < len(data.get("jurnal", [])):
        data["jurnal"].pop(idx)
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
    debit_entries  = [{"akun": a.strip(), "jumlah": float(v)}
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
