-- ==============================================================================
-- USULAN MIGRASI TERPISAH (UNTUK DITINJAU SEBELUM DIJALANKAN DI SUPABASE)
-- Fungsi RPC atomik untuk membuat sesi baru sekaligus mencatat inspeksi pertama
-- dalam SATU transaksi PostgreSQL. Jika salah satu gagal, seluruh transaksi
-- otomatis di-rollback sehingga tidak meninggalkan sesi kosong.
-- ==============================================================================

CREATE OR REPLACE FUNCTION public.create_session_with_first_inspection(
  p_tanggal date,
  p_waktu_mulai timestamptz,
  p_kategori_id uuid
)
RETURNS TABLE (
  sesi_id uuid,
  tanggal date,
  nomor_sesi integer,
  waktu_mulai timestamptz,
  waktu_selesai timestamptz,
  kategori_id uuid,
  jumlah integer
)
LANGUAGE plpgsql
AS $$
DECLARE
  v_next_nomor integer;
  v_sesi_id uuid;
BEGIN
  -- Kunci level transaksi agar dua request bersamaan tidak bentrok nomor_sesi
  PERFORM pg_advisory_xact_lock(hashtext('sesi_' || p_tanggal::text));

  -- Tutup sesi aktif sebelumnya jika masih ada yang terbuka
  UPDATE public.sesi
  SET waktu_selesai = p_waktu_mulai
  WHERE public.sesi.waktu_selesai IS NULL;

  -- Hitung nomor_sesi berikutnya untuk tanggal WIB terkait
  SELECT COALESCE(MAX(s.nomor_sesi), 0) + 1
  INTO v_next_nomor
  FROM public.sesi s
  WHERE s.tanggal = p_tanggal;

  -- 1. Buat sesi baru
  INSERT INTO public.sesi (tanggal, nomor_sesi, waktu_mulai, waktu_selesai)
  VALUES (p_tanggal, v_next_nomor, p_waktu_mulai, NULL)
  RETURNING id INTO v_sesi_id;

  -- 2. Catat inspeksi pertama (jika gagal, seluruh transaksi otomatis rollback)
  INSERT INTO public.inspeksi (sesi_id, kategori_id, jumlah)
  VALUES (v_sesi_id, p_kategori_id, 1);

  RETURN QUERY
  SELECT
    v_sesi_id,
    p_tanggal,
    v_next_nomor,
    p_waktu_mulai,
    NULL::timestamptz,
    p_kategori_id,
    1;
END;
$$;
