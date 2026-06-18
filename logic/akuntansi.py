from collections import defaultdict

# ── Kode akun default ──────────────────────────────────────────────────────
ACCOUNT_CODES = {
    "Kas": "111", "Piutang Usaha": "112", "Perlengkapan": "113",
    "Iklan Dibayar Dimuka": "114", "Asuransi Dibayar Dimuka": "115",
    "Sewa Dibayar Dimuka": "116", "Peralatan": "121", "Kendaraan": "122",
    "Bangunan": "123", "Akumulasi Penyusutan Peralatan": "121.1",
    "Akumulasi Penyusutan Kendaraan": "122.1",
    "Akumulasi Penyusutan Bangunan": "123.1",
    "Utang Usaha": "211", "Utang Bank": "212", "Utang Gaji": "213",
    "Utang Asuransi": "214", "Pendapatan Diterima Dimuka": "215",
    "Asuransi Diterima Dimuka": "216", "Sewa Diterima Dimuka": "217",
    "Modal": "311", "Prive": "312", "Ikhtisar Laba Rugi": "313",
    "Pendapatan Jasa": "411", "Pendapatan Sewa": "412",
    "Beban Gaji": "511", "Beban Sewa": "512", "Beban Iklan": "513",
    "Beban Asuransi": "514", "Beban Perlengkapan": "515",
    "Beban Penyusutan Peralatan": "516", "Beban Penyusutan Kendaraan": "517",
    "Beban Penyusutan Bangunan": "518", "Beban Serba-serbi": "519",
    "Beban Air, Listrik dan Telepon": "520",
}

ACCOUNT_TYPE_LABELS = {
    "asset": "Aset",
    "contra_asset": "Kontra Aset",
    "liability": "Utang",
    "equity": "Modal",
    "drawing": "Prive/Ikhtisar",
    "revenue": "Pendapatan",
    "expense": "Beban",
}

def default_accounts():
    return [
        {
            "kode": kode,
            "nama": nama,
            "tipe": get_account_type(nama),
            "saldo_normal": normal_balance(nama),
            "aktif": True,
        }
        for nama, kode in ACCOUNT_CODES.items()
    ]

def account_map(accounts=None):
    result = {}
    for acc in accounts or []:
        nama = acc.get("nama", "")
        if nama:
            result[nama] = acc
    return result

def get_account_type(account_name, accounts=None):
    """Tentukan tipe akun: asset/liability/equity/revenue/expense"""
    configured = account_map(accounts).get(account_name)
    if configured and configured.get("tipe"):
        return configured["tipe"]

    n = account_name.lower()
    if "beban" in n or "expense" in n:
        return "expense"
    if any(x in n for x in ["pendapatan diterima dimuka", "asuransi diterima dimuka", "sewa diterima dimuka"]):
        return "liability"
    if "pendapatan" in n or "revenue" in n:
        return "revenue"
    if "akumulasi" in n:
        return "contra_asset"
    if n.startswith("utang ") or " utang " in f" {n} ":
        return "liability"
    if any(x in n for x in ["kas", "piutang", "perlengkapan", "peralatan", "kendaraan",
                              "bangunan", "dibayar dimuka", "asuransi dibayar", "iklan dibayar",
                              "sewa dibayar"]):
        return "asset"
    if "modal" in n:
        return "equity"
    if "prive" in n or "ikhtisar" in n:
        return "drawing"
    return "asset"

def normal_balance(account_name, accounts=None):
    configured = account_map(accounts).get(account_name)
    if configured and configured.get("saldo_normal"):
        return configured["saldo_normal"]

    t = get_account_type(account_name, accounts)
    if t in ("asset", "expense", "drawing"):
        return "debit"
    return "credit"

# ── Buku Besar ─────────────────────────────────────────────────────────────
def build_buku_besar(jurnal_entries, jurnal_penyesuaian=None, opening_balances=None, accounts=None):
    """
    Bangun buku besar dari jurnal umum + jurnal penyesuaian.
    Returns dict: { account_name: [ {tanggal, keterangan, ref, debit, kredit, saldo_d, saldo_k} ] }
    """
    ledger = defaultdict(list)  # account -> list of transactions

    # Saldo awal (dari neraca saldo sebelumnya)
    running = defaultdict(lambda: {"debit": 0, "kredit": 0})
    if opening_balances:
        for acc, bal in opening_balances.items():
            d = bal.get("debit", 0)
            k = bal.get("kredit", 0)
            running[acc]["debit"] = d
            running[acc]["kredit"] = k
            nb = normal_balance(acc, accounts)
            saldo_d = d - k if nb == "debit" else 0
            saldo_k = k - d if nb == "credit" else 0
            ledger[acc].append({
                "tanggal": "Saldo Awal", "keterangan": "Saldo Awal",
                "ref": "-", "debit": d if d else "", "kredit": k if k else "",
                "saldo_d": saldo_d if saldo_d > 0 else "",
                "saldo_k": saldo_k if saldo_k > 0 else "",
                "source": "opening"
            })

    def post_entry(entries, ref_prefix):
        for e in entries:
            tgl = e["tanggal"]
            ket = e.get("keterangan", "")
            for side in ("debit", "kredit"):
                for item in e.get(side + "_entries", []):
                    acc = item["akun"]
                    amt = item["jumlah"]
                    if side == "debit":
                        running[acc]["debit"] += amt
                    else:
                        running[acc]["kredit"] += amt
                    nb = normal_balance(acc, accounts)
                    saldo = running[acc]["debit"] - running[acc]["kredit"]
                    saldo_d = saldo if saldo >= 0 and nb == "debit" else (saldo if saldo > 0 else 0)
                    saldo_k = abs(saldo) if saldo < 0 or nb == "kredit" else 0
                    if nb == "debit":
                        saldo_d = max(saldo, 0)
                        saldo_k = max(-saldo, 0)
                    else:
                        saldo_k = running[acc]["kredit"] - running[acc]["debit"]
                        saldo_d = 0
                        if saldo_k < 0:
                            saldo_d = abs(saldo_k)
                            saldo_k = 0
                    ledger[acc].append({
                        "tanggal": tgl, "keterangan": ket,
                        "ref": ref_prefix,
                        "debit": amt if side == "debit" else "",
                        "kredit": amt if side == "kredit" else "",
                        "saldo_d": saldo_d if saldo_d > 0 else "",
                        "saldo_k": saldo_k if saldo_k > 0 else "",
                        "source": ref_prefix
                    })

    post_entry(jurnal_entries, "JU")
    if jurnal_penyesuaian:
        post_entry(jurnal_penyesuaian, "AJP")

    return dict(ledger)

# ── Neraca Saldo ───────────────────────────────────────────────────────────
def build_neraca_saldo(buku_besar, accounts=None):
    """Ambil saldo akhir tiap akun dari buku besar"""
    def kode_sort_key(kode):
        return [int(part) if part.isdigit() else 999 for part in str(kode).split(".")]

    result = []
    for acc, rows in buku_besar.items():
        last = rows[-1]
        d = last["saldo_d"] if last["saldo_d"] != "" else 0
        k = last["saldo_k"] if last["saldo_k"] != "" else 0
        if d > 0 or k > 0:
            result.append({
                "kode": account_map(accounts).get(acc, {}).get("kode", ACCOUNT_CODES.get(acc, "---")),
                "akun": acc,
                "debit": d,
                "kredit": k
            })
    result.sort(key=lambda x: (kode_sort_key(x["kode"]), x["akun"].lower()))
    return result

# ── Kertas Kerja ───────────────────────────────────────────────────────────
def build_kertas_kerja(neraca_saldo, jurnal_penyesuaian, accounts=None):
    """Buat kertas kerja 10 kolom"""
    # Kumpulkan semua AJP per akun
    ajp_debit = defaultdict(float)
    ajp_kredit = defaultdict(float)
    for e in jurnal_penyesuaian:
        for item in e.get("debit_entries", []):
            ajp_debit[item["akun"]] += item["jumlah"]
        for item in e.get("kredit_entries", []):
            ajp_kredit[item["akun"]] += item["jumlah"]

    # Gabungkan semua akun
    all_accounts = {row["akun"]: row for row in neraca_saldo}
    extra_accounts = set(ajp_debit.keys()) | set(ajp_kredit.keys())
    for acc in extra_accounts:
        if acc not in all_accounts:
            all_accounts[acc] = {
                "kode": account_map(accounts).get(acc, {}).get("kode", ACCOUNT_CODES.get(acc, "---")),
                "akun": acc, "debit": 0, "kredit": 0
            }

    rows = []
    for acc, ns in all_accounts.items():
        ns_d = ns["debit"]
        ns_k = ns["kredit"]
        ajp_d = ajp_debit.get(acc, 0)
        ajp_k = ajp_kredit.get(acc, 0)

        # NSD = NS ± AJP
        nb = normal_balance(acc, accounts)
        if nb == "debit":
            nsd_d = ns_d + ajp_d - ajp_k
            nsd_k = 0
            if nsd_d < 0:
                nsd_k = abs(nsd_d); nsd_d = 0
        else:
            nsd_k = ns_k + ajp_k - ajp_d
            nsd_d = 0
            if nsd_k < 0:
                nsd_d = abs(nsd_k); nsd_k = 0

        t = get_account_type(acc, accounts)
        # Laba Rugi: pendapatan & beban
        if t == "revenue":
            lr_d, lr_k, ner_d, ner_k = 0, nsd_k, 0, 0
        elif t == "expense":
            lr_d, lr_k, ner_d, ner_k = nsd_d, 0, 0, 0
        else:
            lr_d, lr_k, ner_d, ner_k = 0, 0, nsd_d, nsd_k

        rows.append({
            "kode": ns["kode"],
            "akun": acc,
            "ns_d": ns_d, "ns_k": ns_k,
            "ajp_d": ajp_d if ajp_d else 0,
            "ajp_k": ajp_k if ajp_k else 0,
            "nsd_d": nsd_d, "nsd_k": nsd_k,
            "lr_d": lr_d, "lr_k": lr_k,
            "ner_d": ner_d, "ner_k": ner_k,
        })

    rows.sort(key=lambda x: x["kode"])
    return rows

# ── Laporan Keuangan ───────────────────────────────────────────────────────
def build_laporan(kertas_kerja, nama_perusahaan, nama_pemilik, periode, accounts=None):
    # Laba Rugi
    pendapatan = [(r["akun"], r["lr_k"]) for r in kertas_kerja if r["lr_k"] > 0]
    beban = [(r["akun"], r["lr_d"]) for r in kertas_kerja if r["lr_d"] > 0]
    total_pendapatan = sum(v for _, v in pendapatan)
    total_beban = sum(v for _, v in beban)
    laba_bersih = total_pendapatan - total_beban

    # Perubahan Modal
    modal_awal = sum(r["ner_k"] for r in kertas_kerja
                     if get_account_type(r["akun"], accounts) == "equity")
    prive = sum(r["ner_d"] for r in kertas_kerja
                if get_account_type(r["akun"], accounts) == "drawing")
    modal_akhir = modal_awal + laba_bersih - prive

    # Neraca
    assets = [(r["akun"], r["ner_d"]) for r in kertas_kerja
              if r["ner_d"] > 0 and get_account_type(r["akun"], accounts) in ("asset",)]
    contra = [(r["akun"], r["ner_k"]) for r in kertas_kerja
              if r["ner_k"] > 0 and get_account_type(r["akun"], accounts) == "contra_asset"]
    liabilities = [(r["akun"], r["ner_k"]) for r in kertas_kerja
                   if r["ner_k"] > 0 and get_account_type(r["akun"], accounts) in ("liability",)]

    return {
        "nama_perusahaan": nama_perusahaan,
        "nama_pemilik": nama_pemilik,
        "periode": periode,
        "laba_rugi": {
            "pendapatan": pendapatan,
            "total_pendapatan": total_pendapatan,
            "beban": beban,
            "total_beban": total_beban,
            "laba_bersih": laba_bersih,
        },
        "perubahan_modal": {
            "modal_awal": modal_awal,
            "laba_bersih": laba_bersih,
            "prive": prive,
            "modal_akhir": modal_akhir,
        },
        "neraca": {
            "assets": assets,
            "contra_assets": contra,
            "liabilities": liabilities,
            "modal_akhir": modal_akhir,
        }
    }

# ── Jurnal Penutup ─────────────────────────────────────────────────────────
def build_jurnal_penutup(laporan, periode):
    entries = []
    lr = laporan["laba_rugi"]
    pm = laporan["perubahan_modal"]
    pemilik = laporan["nama_pemilik"]

    # 1. Tutup pendapatan -> Ikhtisar L/R
    if lr["pendapatan"]:
        entries.append({
            "tanggal": periode,
            "keterangan": "Menutup akun pendapatan",
            "debit_entries": [{"akun": a, "jumlah": v} for a, v in lr["pendapatan"]],
            "kredit_entries": [{"akun": "Ikhtisar Laba Rugi", "jumlah": lr["total_pendapatan"]}]
        })

    # 2. Tutup beban -> Ikhtisar L/R
    if lr["beban"]:
        entries.append({
            "tanggal": periode,
            "keterangan": "Menutup akun beban",
            "debit_entries": [{"akun": "Ikhtisar Laba Rugi", "jumlah": lr["total_beban"]}],
            "kredit_entries": [{"akun": a, "jumlah": v} for a, v in lr["beban"]]
        })

    # 3. Tutup Ikhtisar L/R -> Modal
    if lr["laba_bersih"] > 0:
        entries.append({
            "tanggal": periode,
            "keterangan": "Menutup Ikhtisar Laba Rugi (Laba)",
            "debit_entries": [{"akun": "Ikhtisar Laba Rugi", "jumlah": lr["laba_bersih"]}],
            "kredit_entries": [{"akun": f"Modal {pemilik}", "jumlah": lr["laba_bersih"]}]
        })
    else:
        entries.append({
            "tanggal": periode,
            "keterangan": "Menutup Ikhtisar Laba Rugi (Rugi)",
            "debit_entries": [{"akun": f"Modal {pemilik}", "jumlah": abs(lr["laba_bersih"])}],
            "kredit_entries": [{"akun": "Ikhtisar Laba Rugi", "jumlah": abs(lr["laba_bersih"])}]
        })

    # 4. Tutup Prive -> Modal
    if pm["prive"] > 0:
        entries.append({
            "tanggal": periode,
            "keterangan": f"Menutup akun Prive {pemilik}",
            "debit_entries": [{"akun": f"Modal {pemilik}", "jumlah": pm["prive"]}],
            "kredit_entries": [{"akun": f"Prive {pemilik}", "jumlah": pm["prive"]}]
        })

    return entries
