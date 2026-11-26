CREATE TABLE IF NOT EXISTS public.detail_kategori (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  nama_kategori text NOT NULL,
  CONSTRAINT detail_kategori_pkey PRIMARY KEY (id),
  CONSTRAINT detail_kategori_nama_kategori_key UNIQUE (nama_kategori)
) TABLESPACE pg_default;

CREATE TABLE IF NOT EXISTS public.sesi (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  tanggal date NOT NULL,
  nomor_sesi integer NOT NULL,
  waktu_mulai timestamp with time zone NOT NULL DEFAULT now(),
  waktu_selesai timestamp with time zone NULL,
  CONSTRAINT sesi_pkey PRIMARY KEY (id),
  CONSTRAINT sesi_tanggal_nomor_sesi_key UNIQUE (tanggal, nomor_sesi)
) TABLESPACE pg_default;

CREATE TABLE IF NOT EXISTS public.inspeksi (
  kategori text NOT NULL,
  sesi_id uuid NOT NULL,
  kategori_id uuid NOT NULL,
  jumlah integer NOT NULL DEFAULT 0,
  CONSTRAINT inspeksi_pkey PRIMARY KEY (sesi_id, kategori_id),
  CONSTRAINT inspeksi_kategori_fkey
    FOREIGN KEY (kategori_id) REFERENCES public.detail_kategori(id),
  CONSTRAINT inspeksi_sesi_id_fkey
    FOREIGN KEY (sesi_id) REFERENCES public.sesi(id) ON DELETE CASCADE
) TABLESPACE pg_default;

-- Migrate the earlier schema while preserving inspection rows.
ALTER TABLE public.inspeksi
  ALTER COLUMN kategori SET NOT NULL,
  ALTER COLUMN sesi_id SET NOT NULL,
  ALTER COLUMN kategori_id SET NOT NULL,
  ALTER COLUMN jumlah SET DEFAULT 0,
  ALTER COLUMN jumlah SET NOT NULL;

ALTER TABLE public.inspeksi
  DROP CONSTRAINT IF EXISTS inspeksi_pkey,
  DROP CONSTRAINT IF EXISTS inspeksi_sesi_kategori_id_key,
  DROP CONSTRAINT IF EXISTS inspeksi_kategori_id_fkey,
  DROP CONSTRAINT IF EXISTS inspeksi_kategori_fkey,
  DROP CONSTRAINT IF EXISTS inspeksi_sesi_id_fkey;

ALTER TABLE public.inspeksi
  ADD CONSTRAINT inspeksi_pkey PRIMARY KEY (sesi_id, kategori_id),
  ADD CONSTRAINT inspeksi_kategori_fkey
    FOREIGN KEY (kategori_id) REFERENCES public.detail_kategori(id),
  ADD CONSTRAINT inspeksi_sesi_id_fkey
    FOREIGN KEY (sesi_id) REFERENCES public.sesi(id) ON DELETE CASCADE;
