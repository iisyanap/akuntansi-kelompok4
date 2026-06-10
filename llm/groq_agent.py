"""
AccSys — Groq Agent
Pola persis dari contoh official Groq:
  client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
  chat_history = [system_prompt]
  chat_history.append({"role": "user", "content": ...})
  response = client.chat.completions.create(model=..., messages=chat_history)
  chat_history.append({"role": "assistant", "content": ...})
"""

import os, json, re
from groq import Groq

# ── Model default ──────────────────────────────────────────────────────────
DEFAULT_MODEL = "llama-3.1-8b-instant"

# ── System prompt akuntansi ────────────────────────────────────────────────
def build_system_prompt(perusahaan: dict, existing_accounts: list = None) -> str:
    ctx = ""
    if perusahaan:
        ctx = f"""
Nama Perusahaan : {perusahaan.get('nama', '-')}
Pemilik         : {perusahaan.get('pemilik', '-')}
Periode         : {perusahaan.get('periode', '-')}
Jenis Usaha     : {perusahaan.get('jenis', '-')}
Mata Uang       : {perusahaan.get('currency', 'IDR')}"""
    if existing_accounts:
        ctx += f"\nAkun sudah dipakai: {', '.join(existing_accounts[:20])}"

    return f"""Kamu adalah asisten akuntansi profesional untuk sistem AccSys.
Bantu pengguna membuat jurnal akuntansi dari deskripsi transaksi Bahasa Indonesia dan Bahasa Inggris.

KONTEKS PERUSAHAAN:{ctx if ctx else ' Belum ada data.'}

DAFTAR AKUN:
Aset    : Kas, Piutang Usaha, Perlengkapan, Iklan Dibayar Dimuka,
          Asuransi Dibayar Dimuka, Sewa Dibayar Dimuka,
          Peralatan, Kendaraan, Bangunan,
          Akumulasi Penyusutan Peralatan, Akumulasi Penyusutan Kendaraan
Utang   : Utang Usaha, Utang Gaji, Utang Asuransi,
          Pendapatan Diterima Dimuka, Asuransi Diterima Dimuka,
          Sewa Diterima Dimuka
Ekuitas : Modal, Prive, Ikhtisar Laba Rugi
Pendptn : Pendapatan Jasa, Pendapatan Sewa
Beban   : Beban Gaji, Beban Sewa, Beban Iklan, Beban Asuransi,
          Beban Perlengkapan, Beban Penyusutan Peralatan,
          Beban Penyusutan Kendaraan, Beban Penyusutan Bangunan,
          Beban Serba-serbi, Beban Air Listrik dan Telepon

ATURAN:
- Total Debit HARUS = Total Kredit
- Aset & Beban → saldo normal Debit
- Utang, Ekuitas, Pendapatan → saldo normal Kredit

FORMAT RESPONS — jika ada transaksi, WAJIB sertakan JSON:
```json
{{
  "type": "jurnal_entry",
  "tanggal": "01 April 2008",
  "keterangan": "deskripsi singkat",
  "debit_entries":  [{{"akun": "Kas", "jumlah": 1000000}}],
  "kredit_entries": [{{"akun": "Modal", "jumlah": 1000000}}],
  "penjelasan": "alasan pemilihan akun"
}}
```

Jika user ingin setup/daftarkan perusahaan, gunakan format ini:
```json
{{
  "type": "company_setup",
  "nama": "Nama Perusahaan",
  "pemilik": "Nama Pemilik",
  "periode": "April 2008",
  "jenis": "Jenis Usaha",
  "currency": "IDR"
}}
```

Beberapa transaksi → array JSON. Penyesuaian → "type": "jurnal_penyesuaian".
Gunakan nama akun PERSIS dari daftar di atas."""


# ── Main chat function ─────────────────────────────────────────────────────
def chat_with_memory(
    api_key: str,
    user_message: str,
    chat_history: list,       # format: [{"role":"human","content":"..."}, ...]
    perusahaan: dict,
    existing_accounts: list = None,
    model: str = DEFAULT_MODEL
) -> tuple:
    """
    Pola persis contoh Groq official:
      1. Buat client dengan api_key
      2. Susun chat_history dalam format Groq (role: system/user/assistant)
      3. Append pesan user
      4. Call API
      5. Append response ke history
      6. Return (response_text, updated_history)
    """

    # 1. Buat client — pakai api_key parameter atau fallback ke env
    client = Groq(
        api_key=api_key or os.environ.get("GROQ_API_KEY")
    )

    # 2. Build chat_history dalam format Groq
    #    Simpan internal: "human"/"ai" → kirim ke Groq: "user"/"assistant"
    groq_messages = [
        {
            "role": "system",
            "content": build_system_prompt(perusahaan, existing_accounts)
        }
    ]

    # Masukkan history lama (max 20 pesan terakhir)
    for msg in chat_history[-20:]:
        groq_messages.append({
            "role": "user" if msg["role"] == "human" else "assistant",
            "content": msg["content"]
        })

    # 3. Append pesan user baru
    groq_messages.append({"role": "user", "content": user_message})

    # 4. Call API
    response = client.chat.completions.create(
        model=model,
        messages=groq_messages,
        max_tokens=2048,
        temperature=0.1
    )

    response_text = response.choices[0].message.content

    # 5. Update history (format internal kita: human/ai)
    updated_history = chat_history + [
        {"role": "human",   "content": user_message},
        {"role": "ai",      "content": response_text}
    ]

    return response_text, updated_history


# ── Parse jurnal dari respons LLM ──────────────────────────────────────────
def parse_journal_from_response(response_text: str) -> list | None:
    matches = re.findall(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
    if not matches:
        return None

    entries = []
    for match in matches:
        try:
            parsed = json.loads(match.strip())
            if isinstance(parsed, list):
                entries.extend(parsed)
            elif isinstance(parsed, dict):
                entries.append(parsed)
        except json.JSONDecodeError:
            continue

    valid = [
        e for e in entries
        if e.get("type") in ("jurnal_entry", "jurnal_penyesuaian", "company_setup")
        and (
            e.get("type") == "company_setup"
            or (e.get("debit_entries") and e.get("kredit_entries"))
        )
    ]
    return valid if valid else None


# ── Ambil akun yang sudah dipakai ─────────────────────────────────────────
def get_used_accounts(jurnal: list) -> list:
    accounts = set()
    for entry in jurnal:
        for e in entry.get("debit_entries",  []): accounts.add(e["akun"])
        for e in entry.get("kredit_entries", []): accounts.add(e["akun"])
    return sorted(accounts)
