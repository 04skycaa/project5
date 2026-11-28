-- ==============================================================================
-- DOKUMENTASI STRUKTUR TABEL AKTUAL SUPABASE
-- Sinkron dengan skema live Supabase (Dashboard)
-- Catatan:
-- - Tabel 'inspeksi' HANYA memiliki 3 kolom: sesi_id, kategori_id, jumlah
-- - Kolom 'kategori' bertipe text SUDAH DIHAPUS dari tabel inspeksi
-- - Primary Key inspeksi adalah komposit (sesi_id, kategori_id)
-- ==============================================================================

-- 1. TABEL DETAIL KATEGORI
CREATE TABLE IF NOT EXISTS public.detail_kategori (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  nama_kategori text NOT NULL,
  CONSTRAINT detail_kategori_pkey PRIMARY KEY (id),
  CONSTRAINT detail_kategori_nama_kategori_key UNIQUE (nama_kategori)
) TABLESPACE pg_default;

-- 2. TABEL SESI
CREATE TABLE IF NOT EXISTS public.sesi (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  tanggal date NOT NULL,
  nomor_sesi integer NOT NULL,
  waktu_mulai timestamp with time zone NOT NULL DEFAULT now(),
  waktu_selesai timestamp with time zone NULL,
  CONSTRAINT sesi_pkey PRIMARY KEY (id),
  CONSTRAINT sesi_tanggal_nomor_sesi_key UNIQUE (tanggal, nomor_sesi)
) TABLESPACE pg_default;

-- 3. TABEL INSPEKSI (AKUMULASI JUMLAH JAMUR PER SESI & KATEGORI)
CREATE TABLE IF NOT EXISTS public.inspeksi (
  sesi_id uuid NOT NULL,
  kategori_id uuid NOT NULL,
  jumlah integer NOT NULL DEFAULT 0,
  CONSTRAINT inspeksi_pkey PRIMARY KEY (sesi_id, kategori_id),
  CONSTRAINT inspeksi_sesi_id_fkey
    FOREIGN KEY (sesi_id) REFERENCES public.sesi(id) ON DELETE CASCADE,
  CONSTRAINT inspeksi_kategori_id_fkey
    FOREIGN KEY (kategori_id) REFERENCES public.detail_kategori(id)
) TABLESPACE pg_default;

-- Indeks pendukung untuk performa join & filter
CREATE INDEX IF NOT EXISTS idx_inspeksi_sesi_id ON public.inspeksi(sesi_id);
CREATE INDEX IF NOT EXISTS idx_inspeksi_kategori_id ON public.inspeksi(kategori_id);
CREATE INDEX IF NOT EXISTS idx_sesi_tanggal_nomor ON public.sesi(tanggal, nomor_sesi);
CREATE INDEX IF NOT EXISTS idx_sesi_waktu_selesai ON public.sesi(waktu_selesai);
