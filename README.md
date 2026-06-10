# akuntansi-kelompok4

## Supabase database

Aplikasi bisa menyimpan data perusahaan/transaksi ke Supabase agar tidak hilang saat cache/cookie browser berubah.

1. Buat project gratis di Supabase.
2. Buka SQL Editor, jalankan isi file `supabase_schema.sql`.
3. Buat file `.env` di root project:

```env
FLASK_SECRET_KEY=ganti_dengan_random_text
SUPABASE_URL=https://PROJECT_ID.supabase.co
SUPABASE_ANON_KEY=isi_anon_key_supabase
SUPABASE_TABLE=accsys_companies
SUPABASE_OWNER_ID=kelompok4
```

Kalau `SUPABASE_URL` dan `SUPABASE_ANON_KEY` belum diisi, aplikasi otomatis fallback ke penyimpanan session seperti sebelumnya.
