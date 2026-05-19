from flask import Flask, render_template, request, session, redirect, url_for, jsonify, send_file
import json, io
from logic.akuntansi import (
    build_buku_besar, build_neraca_saldo, build_kertas_kerja,
    build_laporan, build_jurnal_penutup, ACCOUNT_CODES, get_account_type
)
from logic.export_excel import (
    export_jurnal, export_buku_besar, export_neraca_saldo,
    export_penyesuaian, export_kertas_kerja, export_laporan, export_jurnal_penutup
)

app = Flask(__name__)
app.secret_key = "akuntansi_secret_2024"

# ── Custom Jinja2 filters ──────────────────────────────────────────────────
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
    if currency == "USD":
        return f"$ {n:,.2f}"
    return f"Rp {n:,.0f}"

@app.context_processor
def inject_currency():
    """Inject currency ke semua template secara otomatis."""
    from flask import session
    data = session.get("data", {})
    currency = data.get("perusahaan", {}).get("currency", "IDR")
    symbol = "$" if currency == "USD" else "Rp"
    return dict(currency=currency, currency_symbol=symbol)

def get_data():
    return session.get("data", {
        "perusahaan": {},
        "opening_balances": {},
        "jurnal": [],
        "jurnal_penyesuaian": [],
        "penyesuaian_info": []
    })

def save_data(data):
    session["data"] = data
    session.modified = True

# ── Home ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    data = get_data()
    return render_template("index.html", perusahaan=data.get("perusahaan", {}))

@app.route("/setup", methods=["POST"])
def setup():
    data = get_data()
    data["perusahaan"] = {
        "nama": request.form.get("nama_perusahaan", ""),
        "pemilik": request.form.get("nama_pemilik", ""),
        "periode": request.form.get("periode", ""),
        "jenis": request.form.get("jenis_usaha", ""),
        "currency": request.form.get("currency", "IDR")
    }
    # Opening balances
    ob_accounts = request.form.getlist("ob_account")
    ob_debits = request.form.getlist("ob_debit")
    ob_kredits = request.form.getlist("ob_kredit")
    ob = {}
    for i, acc in enumerate(ob_accounts):
        if acc.strip():
            d = float(ob_debits[i]) if ob_debits[i] else 0
            k = float(ob_kredits[i]) if ob_kredits[i] else 0
            ob[acc.strip()] = {"debit": d, "kredit": k}
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
    tanggal = request.form.get("tanggal", "")
    keterangan = request.form.get("keterangan", "")

    debit_accounts = request.form.getlist("debit_account[]")
    debit_amounts = request.form.getlist("debit_amount[]")
    kredit_accounts = request.form.getlist("kredit_account[]")
    kredit_amounts = request.form.getlist("kredit_amount[]")

    debit_entries = []
    for a, v in zip(debit_accounts, debit_amounts):
        if a.strip() and v:
            debit_entries.append({"akun": a.strip(), "jumlah": float(v)})

    kredit_entries = []
    for a, v in zip(kredit_accounts, kredit_amounts):
        if a.strip() and v:
            kredit_entries.append({"akun": a.strip(), "jumlah": float(v)})

    total_d = sum(e["jumlah"] for e in debit_entries)
    total_k = sum(e["jumlah"] for e in kredit_entries)

    if abs(total_d - total_k) > 0.01:
        return jsonify({"error": f"Jumlah Debit (Rp {total_d:,.0f}) ≠ Kredit (Rp {total_k:,.0f}). Selisih: Rp {abs(total_d-total_k):,.0f}"}), 400

    entry = {
        "id": len(data.get("jurnal", [])),
        "tanggal": tanggal,
        "keterangan": keterangan,
        "debit_entries": debit_entries,
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
    bb = build_buku_besar(
        data["jurnal"],
        data.get("jurnal_penyesuaian"),
        data.get("opening_balances")
    )
    return render_template("buku_besar.html",
                           perusahaan=data["perusahaan"],
                           buku_besar=bb)

# ── Neraca Saldo ───────────────────────────────────────────────────────────
@app.route("/neraca_saldo")
def neraca_saldo():
    data = get_data()
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    total_d = sum(r["debit"] for r in ns)
    total_k = sum(r["kredit"] for r in ns)
    return render_template("neraca_saldo.html",
                           perusahaan=data["perusahaan"],
                           neraca_saldo=ns,
                           total_d=total_d, total_k=total_k)

# ── Jurnal Penyesuaian ─────────────────────────────────────────────────────
@app.route("/penyesuaian")
def penyesuaian():
    data = get_data()
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    return render_template("penyesuaian.html",
                           perusahaan=data["perusahaan"],
                           neraca_saldo=ns,
                           jurnal_penyesuaian=data.get("jurnal_penyesuaian", []),
                           account_codes=ACCOUNT_CODES)

@app.route("/penyesuaian/add", methods=["POST"])
def penyesuaian_add():
    data = get_data()
    tanggal = request.form.get("tanggal", "")
    keterangan = request.form.get("keterangan", "")

    debit_accounts = request.form.getlist("debit_account[]")
    debit_amounts = request.form.getlist("debit_amount[]")
    kredit_accounts = request.form.getlist("kredit_account[]")
    kredit_amounts = request.form.getlist("kredit_amount[]")

    debit_entries = []
    for a, v in zip(debit_accounts, debit_amounts):
        if a.strip() and v:
            debit_entries.append({"akun": a.strip(), "jumlah": float(v)})

    kredit_entries = []
    for a, v in zip(kredit_accounts, kredit_amounts):
        if a.strip() and v:
            kredit_entries.append({"akun": a.strip(), "jumlah": float(v)})

    total_d = sum(e["jumlah"] for e in debit_entries)
    total_k = sum(e["jumlah"] for e in kredit_entries)

    if abs(total_d - total_k) > 0.01:
        return jsonify({"error": f"Debit (Rp {total_d:,.0f}) ≠ Kredit (Rp {total_k:,.0f})"}), 400

    entry = {
        "id": len(data.get("jurnal_penyesuaian", [])),
        "tanggal": tanggal,
        "keterangan": keterangan,
        "debit_entries": debit_entries,
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
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    kk = build_kertas_kerja(ns, data.get("jurnal_penyesuaian", []))

    total = {k: sum(r[k] for r in kk) for k in
             ["ns_d","ns_k","ajp_d","ajp_k","nsd_d","nsd_k","lr_d","lr_k","ner_d","ner_k"]}

    laba = total["lr_k"] - total["lr_d"]
    return render_template("kertas_kerja.html",
                           perusahaan=data["perusahaan"],
                           kertas_kerja=kk,
                           total=total,
                           laba_bersih=laba)

# ── Laporan Keuangan ───────────────────────────────────────────────────────
@app.route("/laporan")
def laporan():
    data = get_data()
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    kk = build_kertas_kerja(ns, data.get("jurnal_penyesuaian", []))
    p = data["perusahaan"]
    lap = build_laporan(kk, p["nama"], p["pemilik"], p["periode"])
    return render_template("laporan.html", laporan=lap, perusahaan=p)

# ── Jurnal Penutup ─────────────────────────────────────────────────────────
@app.route("/jurnal_penutup")
def jurnal_penutup():
    data = get_data()
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    kk = build_kertas_kerja(ns, data.get("jurnal_penyesuaian", []))
    p = data["perusahaan"]
    lap = build_laporan(kk, p["nama"], p["pemilik"], p["periode"])
    jp = build_jurnal_penutup(lap, p["periode"])
    return render_template("jurnal_penutup.html",
                           perusahaan=p,
                           jurnal_penutup=jp)


# ── Export Excel ───────────────────────────────────────────────────────────
def _excel_response(data_bytes, filename):
    return send_file(
        io.BytesIO(data_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )

@app.route("/export/jurnal")
def export_jurnal_route():
    data = get_data()
    p = data["perusahaan"]
    b = export_jurnal(data["jurnal"], p, p.get("currency","IDR"))
    return _excel_response(b, f"Jurnal_Umum_{p['periode'].replace(' ','_')}.xlsx")

@app.route("/export/buku_besar")
def export_buku_besar_route():
    data = get_data()
    p = data["perusahaan"]
    bb = build_buku_besar(data["jurnal"], data.get("jurnal_penyesuaian"), data.get("opening_balances"))
    b = export_buku_besar(bb, p, p.get("currency","IDR"))
    return _excel_response(b, f"Buku_Besar_{p['periode'].replace(' ','_')}.xlsx")

@app.route("/export/neraca_saldo")
def export_neraca_saldo_route():
    data = get_data()
    p = data["perusahaan"]
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    b = export_neraca_saldo(ns, p, p.get("currency","IDR"))
    return _excel_response(b, f"Neraca_Saldo_{p['periode'].replace(' ','_')}.xlsx")

@app.route("/export/penyesuaian")
def export_penyesuaian_route():
    data = get_data()
    p = data["perusahaan"]
    b = export_penyesuaian(data.get("jurnal_penyesuaian", []), p, p.get("currency","IDR"))
    return _excel_response(b, f"Jurnal_Penyesuaian_{p['periode'].replace(' ','_')}.xlsx")

@app.route("/export/kertas_kerja")
def export_kertas_kerja_route():
    data = get_data()
    p = data["perusahaan"]
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    kk = build_kertas_kerja(ns, data.get("jurnal_penyesuaian", []))
    total = {k: sum(r[k] for r in kk) for k in ["lr_d","lr_k"]}
    laba = total["lr_k"] - total["lr_d"]
    b = export_kertas_kerja(kk, p, laba, p.get("currency","IDR"))
    return _excel_response(b, f"Kertas_Kerja_{p['periode'].replace(' ','_')}.xlsx")

@app.route("/export/laporan")
def export_laporan_route():
    data = get_data()
    p = data["perusahaan"]
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    kk = build_kertas_kerja(ns, data.get("jurnal_penyesuaian", []))
    lap = build_laporan(kk, p["nama"], p["pemilik"], p["periode"])
    b = export_laporan(lap, p.get("currency","IDR"))
    return _excel_response(b, f"Laporan_Keuangan_{p['periode'].replace(' ','_')}.xlsx")

@app.route("/export/jurnal_penutup")
def export_jurnal_penutup_route():
    data = get_data()
    p = data["perusahaan"]
    bb = build_buku_besar(data["jurnal"], None, data.get("opening_balances"))
    ns = build_neraca_saldo(bb)
    kk = build_kertas_kerja(ns, data.get("jurnal_penyesuaian", []))
    lap = build_laporan(kk, p["nama"], p["pemilik"], p["periode"])
    jp = build_jurnal_penutup(lap, p["periode"])
    b = export_jurnal_penutup(jp, p, p.get("currency","IDR"))
    return _excel_response(b, f"Jurnal_Penutup_{p['periode'].replace(' ','_')}.xlsx")

# ── Reset ──────────────────────────────────────────────────────────────────
@app.route("/reset", methods=["POST"])
def reset():
    session.clear()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True, port=5000)
