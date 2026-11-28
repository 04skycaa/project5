import os
import json
import threading
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# Konfigurasi Supabase dari environment variable
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://mcmhaiqwgurlfwoncwry.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

_supabase_client = None
_detail_kategori_name_to_id = None
_detail_kategori_id_to_name = None
_session_lock = threading.Lock()
_inspection_lock = threading.Lock()

# Timezone WIB (UTC+7)
WIB = timezone(timedelta(hours=7))

# 6 Nama Kategori Resmi sesuai tabel public.detail_kategori
DETAIL_KATEGORI_NAMES = [
    "Healthy",
    "Bacterial Blotch Tidak Cacat",
    "Bacterial Blotch Cacat",
    "Dry Bubble",
    "Cobweb Disease",
    "Wet Bubble"
]

# Set untuk deteksi duplikat berbasis memory
_processed_detections = set()
_processed_detections_lock = threading.Lock()

# Peta token pending_session_id -> record sesi DB agar dua prediksi pertama bersamaan tidak membuat 2 sesi
_pending_session_map = {}


def get_supabase():
    """Mengembalikan instance Supabase client (singleton)."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    if not SUPABASE_KEY:
        print("[WARNING] SUPABASE_KEY belum diset di file .env. Fitur Supabase nonaktif sementara.")
        return None

    try:
        from supabase import create_client
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return _supabase_client
    except Exception as e:
        print(f"[ERROR] Gagal inisialisasi Supabase client: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# MANAJEMEN TABEL DETAIL_KATEGORI
# ═══════════════════════════════════════════════════════════════════════════════

def get_detail_kategori_maps():
    """
    Mengembalikan tuple (name_to_id, id_to_name) dari tabel detail_kategori.
    Menggunakan cache dalam memori.
    """
    global _detail_kategori_name_to_id, _detail_kategori_id_to_name
    if _detail_kategori_name_to_id and _detail_kategori_id_to_name:
        return _detail_kategori_name_to_id, _detail_kategori_id_to_name

    client = get_supabase()
    if not client:
        return {}, {}

    try:
        resp = client.table("detail_kategori").select("id, nama_kategori").execute()
        if resp.data:
            _detail_kategori_name_to_id = {row["nama_kategori"]: row["id"] for row in resp.data}
            _detail_kategori_id_to_name = {row["id"]: row["nama_kategori"] for row in resp.data}
            return _detail_kategori_name_to_id, _detail_kategori_id_to_name
    except Exception as e:
        print(f"[ERROR] Gagal mengambil detail_kategori: {e}")
    return {}, {}


def get_detail_kategori_map():
    """Backward compatibility: mengembalikan {nama_kategori: uuid}."""
    name_to_id, _ = get_detail_kategori_maps()
    return name_to_id


def get_detail_kategori_reverse_map():
    """Mengembalikan {uuid: nama_kategori}."""
    _, id_to_name = get_detail_kategori_maps()
    return id_to_name


def map_to_detail_kategori(predicted_class: str = None, grade: str = None) -> str:
    """
    Memetakan hasil prediksi AI & grading PCV ke TEPAT satu dari 6 nama kategori resmi
    di tabel public.detail_kategori:
    1. Healthy
    2. Bacterial Blotch Tidak Cacat
    3. Bacterial Blotch Cacat
    4. Dry Bubble
    5. Cobweb Disease
    6. Wet Bubble

    Aturan Mutlak:
    - Tidak boleh mencocokkan substring 'tidak' jika grade adalah 'TIDAK_LAYAK'.
    - Jika input tidak valid/tidak dikenal, kembalikan None (jangan dipaksakan ke Dry Bubble).
    """
    p_clean = str(predicted_class or "").strip().replace(" ", "_")
    g_clean = str(grade or "").strip().upper()

    # 1. Healthy
    if p_clean == "Healthy" or g_clean == "SEHAT":
        return "Healthy"

    # 2. Bacterial Blotch
    if p_clean == "Bacterial_Blotch":
        if g_clean == "BB_TIDAK_CACAT":
            return "Bacterial Blotch Tidak Cacat"
        elif g_clean == "BB_CACAT":
            return "Bacterial Blotch Cacat"
        # Jika pcv/grading error sehingga grade TIDAK_LAYAK / FALLBACK,
        # kegagalan analisis tidak boleh masuk ke 6 kategori valid
        return None

    # 3. Penyakit Non-BB resmi
    if p_clean == "Dry_Bubble":
        return "Dry Bubble"
    if p_clean == "Cobweb_Disease":
        return "Cobweb Disease"
    if p_clean == "Wet_Bubble":
        return "Wet Bubble"

    # Jika kelas tidak dikenal atau None, jangan masukkan ke kategori mana pun
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# MANAJEMEN SESI ALAT (Tabel: public.sesi)
# ═══════════════════════════════════════════════════════════════════════════════

def get_current_wib_date_str():
    """Mendapatkan tanggal hari ini dalam format YYYY-MM-DD zona waktu WIB."""
    return datetime.now(WIB).strftime("%Y-%m-%d")


def get_active_session():
    """
    Mengambil sesi yang sedang aktif (waktu_selesai IS NULL).
    Jika ada lebih dari satu, ambil yang paling baru.
    """
    client = get_supabase()
    if client is None:
        return None

    try:
        response = client.table("sesi") \
            .select("id, tanggal, nomor_sesi, waktu_mulai, waktu_selesai") \
            .is_("waktu_selesai", "null") \
            .order("waktu_mulai", desc=True) \
            .limit(1) \
            .execute()
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
    except Exception as e:
        print(f"[ERROR] Gagal mengambil sesi aktif: {e}")
        return None


def start_new_session():
    """
    Memulai sesi baru secara atomik:
    1. Tutup SEMUA sesi lama yang belum ditutup (waktu_selesai IS NULL).
    2. Hitung nomor_sesi berikutnya untuk tanggal hari ini (max + 1, mulai dari 1).
    3. Simpan baris baru ke tabel 'sesi'.
    Dilindungi dengan _session_lock untuk mencegah race condition.
    """
    with _session_lock:
        client = get_supabase()
        if client is None:
            return None

        today_str = get_current_wib_date_str()
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            # 1. Tutup SEMUA sesi aktif sebelumnya
            client.table("sesi") \
                .update({"waktu_selesai": now_iso}) \
                .is_("waktu_selesai", "null") \
                .execute()

            # 2. Cari nomor sesi tertinggi untuk tanggal hari ini
            existing_today = client.table("sesi") \
                .select("nomor_sesi") \
                .eq("tanggal", today_str) \
                .order("nomor_sesi", desc=True) \
                .limit(1) \
                .execute()

            next_number = 1
            if existing_today.data and len(existing_today.data) > 0:
                next_number = int(existing_today.data[0].get("nomor_sesi", 0)) + 1

            # 3. Buat sesi baru
            payload = {
                "tanggal": today_str,
                "nomor_sesi": next_number,
                "waktu_mulai": now_iso,
                "waktu_selesai": None
            }

            response = client.table("sesi").insert(payload).execute()
            if response.data and len(response.data) > 0:
                return response.data[0]
            return None

        except Exception as e:
            print(f"[ERROR] Gagal membuat sesi baru: {e}")
            return None


def stop_active_session(target_sesi_id: str = None):
    """
    Menghentikan sesi alat yang sedang berjalan.
    Jika target_sesi_id diberikan, hanya tutup sesi tersebut jika masih aktif.
    Jika tidak diberikan, tutup SEMUA sesi yang waktu_selesai-nya masih NULL.
    """
    with _session_lock:
        client = get_supabase()
        if client is None:
            return None

        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            query = client.table("sesi").update({"waktu_selesai": now_iso})
            if target_sesi_id:
                query = query.eq("id", target_sesi_id).is_("waktu_selesai", "null")
            else:
                query = query.is_("waktu_selesai", "null")

            response = query.execute()
            if response.data and len(response.data) > 0:
                return response.data[0]
            return {"message": "Tidak ada sesi aktif yang dihentikan."}

        except Exception as e:
            print(f"[ERROR] Gagal menghentikan sesi: {e}")
            return None


# ═══════════════════════════════════════════════════════════════════════════════
# PENYIMPANAN DATA INSPEKSI (Tabel: public.inspeksi)
# Skema aktual: sesi_id (UUID), kategori_id (UUID), jumlah (INT4)
# ═══════════════════════════════════════════════════════════════════════════════

def save_first_inspection_with_pending_session(
    pending_session_id: str,
    grade: str,
    ai_class: str = None,
    confidence: float = None,
    servo_target: str = None,
    pcv_result: dict = None,
    detection_id: str = None
) -> dict:
    """
    Membuat sesi baru sekaligus mencatat inspeksi pertama secara atomik.
    - Jika pending_session_id sudah dipromosikan oleh request paralel sebelumnya,
      langsung gunakan sesi_id tersebut dan jalankan save_inspection() biasa.
    - Jika belum, buat sesi + inspeksi pertama dalam 1 transaksi (via RPC jika tersedia,
      atau insert ber-lock dengan rollback DELETE sesi otomatis jika insert inspeksi gagal).
    """
    client = get_supabase()
    if client is None:
        raise RuntimeError("Supabase client belum terkonfigurasi (SUPABASE_KEY kosong atau salah).")

    if not pending_session_id:
        raise ValueError("pending_session_id wajib diberikan.")

    nama_kategori = map_to_detail_kategori(ai_class, grade)
    if not nama_kategori:
        return {
            "saved": False,
            "message": "Hasil analisis tidak masuk ke dalam 6 kategori resmi database (tidak dihitung).",
            "grade": grade,
            "ai_class": ai_class
        }

    name_to_id, _ = get_detail_kategori_maps()
    kategori_id = name_to_id.get(nama_kategori)
    if not kategori_id:
        raise ValueError(f"kategori_id untuk '{nama_kategori}' tidak ditemukan di database.")

    with _session_lock:
        # Double-checked locking: apakah request paralel lain sudah mempromosikan pending_session_id ini?
        existing_session = _pending_session_map.get(pending_session_id)
        if existing_session and existing_session.get("id"):
            saved_data = save_inspection(
                grade=grade,
                sesi_id=existing_session["id"],
                ai_class=ai_class,
                confidence=confidence,
                servo_target=servo_target,
                pcv_result=pcv_result,
                detection_id=detection_id
            )
            saved_data["sesi_info"] = existing_session
            return saved_data

        # Cek idempotensi detection_id sebelum membuat sesi
        if detection_id:
            with _processed_detections_lock:
                if detection_id in _processed_detections:
                    return {
                        "already_processed": True,
                        "grade": grade,
                        "ai_class": ai_class
                    }
                if len(_processed_detections) > 10000:
                    _processed_detections.clear()
                _processed_detections.add(detection_id)

        today_str = get_current_wib_date_str()
        now_iso = datetime.now(timezone.utc).isoformat()
        created_session = None
        saved_data = None

        try:
            # Coba panggil fungsi RPC atomik PostgreSQL jika sudah terpasang
            try:
                rpc_resp = client.rpc("create_session_with_first_inspection", {
                    "p_tanggal": today_str,
                    "p_waktu_mulai": now_iso,
                    "p_kategori_id": kategori_id
                }).execute()
                if rpc_resp.data and len(rpc_resp.data) > 0:
                    row = rpc_resp.data[0]
                    created_session = {
                        "id": row["sesi_id"],
                        "tanggal": row["tanggal"],
                        "nomor_sesi": row["nomor_sesi"],
                        "waktu_mulai": row["waktu_mulai"],
                        "waktu_selesai": row["waktu_selesai"]
                    }
                    saved_data = {
                        "sesi_id": row["sesi_id"],
                        "kategori_id": row["kategori_id"],
                        "jumlah": row["jumlah"]
                    }
            except Exception as rpc_err:
                # Fallback jika RPC belum dibuat di DB: lakukan di bawah lock + rollback kompensasi
                print(f"[INFO] RPC atomik belum tersedia ({rpc_err}), menggunakan transaksi kompensasi ber-lock.")

            if not created_session:
                # 1. Tutup sesi aktif sebelumnya jika ada
                client.table("sesi") \
                    .update({"waktu_selesai": now_iso}) \
                    .is_("waktu_selesai", "null") \
                    .execute()

                # 2. Cari nomor_sesi berikutnya
                existing_today = client.table("sesi") \
                    .select("nomor_sesi") \
                    .eq("tanggal", today_str) \
                    .order("nomor_sesi", desc=True) \
                    .limit(1) \
                    .execute()

                next_number = 1
                if existing_today.data and len(existing_today.data) > 0:
                    next_number = int(existing_today.data[0].get("nomor_sesi", 0)) + 1

                # 3. Insert sesi baru
                sess_resp = client.table("sesi").insert({
                    "tanggal": today_str,
                    "nomor_sesi": next_number,
                    "waktu_mulai": now_iso,
                    "waktu_selesai": None
                }).execute()

                if not sess_resp.data:
                    raise RuntimeError("Gagal membuat baris sesi baru di Supabase.")
                created_session = sess_resp.data[0]

                # 4. Insert inspeksi pertama; jika gagal, rollback sesi yang baru dibuat!
                try:
                    insp_resp = client.table("inspeksi").insert({
                        "sesi_id": created_session["id"],
                        "kategori_id": kategori_id,
                        "jumlah": 1
                    }).execute()
                    if not insp_resp.data:
                        raise RuntimeError("Gagal menyimpan baris inspeksi pertama.")
                    saved_data = insp_resp.data[0]
                except Exception as insp_err:
                    # Rollback kompensasi: hapus sesi agar tidak meninggalkan sesi kosong!
                    try:
                        client.table("sesi").delete().eq("id", created_session["id"]).execute()
                    except Exception as rb_err:
                        print(f"[CRITICAL] Rollback hapus sesi gagal: {rb_err}")
                    raise insp_err

            # Simpan mapping agar request paralel dengan pending_session_id yang sama memakai sesi ini
            if len(_pending_session_map) > 1000:
                _pending_session_map.clear()
            _pending_session_map[pending_session_id] = created_session

            saved_data["nama_kategori"] = nama_kategori
            saved_data["grade"] = grade
            saved_data["ai_class"] = ai_class
            saved_data["confidence"] = confidence
            saved_data["servo_target"] = servo_target
            saved_data["sesi_info"] = created_session
            if pcv_result:
                saved_data["pcv_result"] = pcv_result

            return saved_data

        except Exception as e:
            if detection_id:
                with _processed_detections_lock:
                    _processed_detections.discard(detection_id)
            raise e


def save_inspection(
    grade: str,
    sesi_id: str,
    ai_class: str = None,
    confidence: float = None,
    servo_target: str = None,
    pcv_result: dict = None,
    detection_id: str = None
) -> dict:
    """
    Menyimpan atau menambahkan akumulasi jumlah per kategori ke tabel 'public.inspeksi' di Supabase.
    - Wajib menerima sesi_id valid yang berstatus aktif.
    - TIDAK PERNAH membuat sesi baru otomatis jika sesi_id kosong atau tidak valid.
    - Mencegah duplikasi hitungan dengan detection_id idempotency check.
    - Tabel 'inspeksi' HANYA mengisi (sesi_id, kategori_id, jumlah). Kolom 'kategori' tidak digunakan.
    - Menangani upsert secara aman dari race condition dengan _inspection_lock.
    """
    client = get_supabase()
    if client is None:
        raise RuntimeError("Supabase client belum terkonfigurasi (SUPABASE_KEY kosong atau salah).")

    if not sesi_id:
        raise ValueError("sesi_id wajib diberikan. Tidak ada sesi aktif untuk menyimpan inspeksi.")

    # 1. Cek idempotensi detection_id untuk mencegah double counting
    if detection_id:
        with _processed_detections_lock:
            if detection_id in _processed_detections:
                print(f"[INFO] Deteksi {detection_id} sudah pernah diproses. Melewati penambahan hitungan.")
                return {
                    "sesi_id": sesi_id,
                    "already_processed": True,
                    "grade": grade,
                    "ai_class": ai_class
                }
            if len(_processed_detections) > 10000:
                _processed_detections.clear()
            _processed_detections.add(detection_id)

    # 2. Verifikasi apakah sesi tersebut ada dan masih aktif
    check_sess = client.table("sesi").select("id, waktu_selesai").eq("id", sesi_id).limit(1).execute()
    if not check_sess.data:
        raise ValueError(f"Sesi dengan ID {sesi_id} tidak ditemukan.")
    if check_sess.data[0].get("waktu_selesai") is not None:
        raise ValueError(f"Sesi {sesi_id} sudah ditutup ({check_sess.data[0]['waktu_selesai']}). Tidak dapat menambahkan inspeksi.")

    # 3. Tentukan nama kategori resmi dan kategori_id
    nama_kategori = map_to_detail_kategori(ai_class, grade)
    if not nama_kategori:
        # Jika hasil analisis tidak valid/error, tidak boleh menambah hitungan 6 kategori
        return {
            "sesi_id": sesi_id,
            "saved": False,
            "message": "Hasil analisis tidak masuk ke dalam 6 kategori resmi database (tidak dihitung).",
            "grade": grade,
            "ai_class": ai_class
        }

    name_to_id, _ = get_detail_kategori_maps()
    kategori_id = name_to_id.get(nama_kategori)
    if not kategori_id:
        raise ValueError(f"kategori_id untuk '{nama_kategori}' tidak ditemukan di database.")

    # 4. Upsert atomik ber-lock untuk mencegah lost update
    with _inspection_lock:
        existing = client.table("inspeksi") \
            .select("jumlah") \
            .eq("sesi_id", sesi_id) \
            .eq("kategori_id", kategori_id) \
            .limit(1) \
            .execute()

        if existing.data and len(existing.data) > 0:
            current_count = int(existing.data[0].get("jumlah", 0) or 0)
            new_count = current_count + 1
            upd_resp = client.table("inspeksi") \
                .update({"jumlah": new_count}) \
                .eq("sesi_id", sesi_id) \
                .eq("kategori_id", kategori_id) \
                .execute()
            if not upd_resp.data:
                raise RuntimeError(f"Gagal memperbarui jumlah inspeksi untuk kategori {nama_kategori}.")
            saved_data = upd_resp.data[0]
        else:
            insert_payload = {
                "sesi_id": sesi_id,
                "kategori_id": kategori_id,
                "jumlah": 1
            }
            ins_resp = client.table("inspeksi").insert(insert_payload).execute()
            if not ins_resp.data:
                raise RuntimeError(f"Gagal menambahkan baris inspeksi baru untuk kategori {nama_kategori}.")
            saved_data = ins_resp.data[0]

    # Tempelkan metadata untuk keperluan respons UI
    saved_data["nama_kategori"] = nama_kategori
    saved_data["grade"] = grade
    saved_data["ai_class"] = ai_class
    saved_data["confidence"] = confidence
    saved_data["servo_target"] = servo_target
    if pcv_result:
        saved_data["pcv_result"] = pcv_result

    return saved_data


# ═══════════════════════════════════════════════════════════════════════════════
# PENGAMBILAN DATA & STATISTIK BERBASIS RELASI DETAIL_KATEGORI
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_latest_inspections(limit: int = 100, dari: str = None, sampai: str = None):
    """
    Mengambil riwayat baris inspeksi dari Supabase via relasi sesi & detail_kategori.
    """
    client = get_supabase()
    if client is None:
        return []

    try:
        query = client.table("sesi") \
            .select("id, tanggal, nomor_sesi, waktu_mulai, waktu_selesai, inspeksi(sesi_id, kategori_id, jumlah, detail_kategori(id, nama_kategori))")

        if dari:
            query = query.gte("tanggal", dari)
        if sampai:
            query = query.lte("tanggal", sampai)

        resp = query.order("tanggal", desc=True).order("nomor_sesi", desc=True).limit(limit).execute()
        sessions = resp.data or []

        # Ratakan (flatten) menjadi list per inspeksi untuk tabel dashboard
        flattened = []
        for s in sessions:
            insp_items = s.get("inspeksi") or []
            for item in insp_items:
                kat_info = item.get("detail_kategori") or {}
                kat_name = kat_info.get("nama_kategori", "Healthy")
                flattened.append({
                    "id": f"{s['id'][:8]}-{item['kategori_id'][:4]}",
                    "full_id": f"{s['id']}:{item['kategori_id']}",
                    "sesi_id": s["id"],
                    "kategori_id": item["kategori_id"],
                    "kategori": kat_name,
                    "grade": kat_name,
                    "jumlah": item.get("jumlah", 1),
                    "sesi": s
                })
        return flattened
    except Exception as e:
        print(f"[ERROR] Gagal mengambil data inspeksi: {e}")
        return []


def calculate_database_stats():
    """
    Menghitung statistik lengkap langsung dari data tabel 'inspeksi' dan 'sesi' di Supabase.
    Hanya menghitung sesi yang valid dan total jamur yang riil.
    """
    client = get_supabase()
    if client is None:
        return _empty_stats()

    try:
        resp_insp = client.table("inspeksi").select("sesi_id, kategori_id, jumlah, detail_kategori(nama_kategori)").execute()
        rows = resp_insp.data or []

        # Hitung hanya sesi yang memiliki aktivitas atau total sesi tercatat
        resp_sesi = client.table("sesi").select("id").execute()
        total_sesi = len(resp_sesi.data or [])
    except Exception as e:
        print(f"[ERROR] Gagal menghitung statistik database: {e}")
        return _empty_stats()

    total = sum(int(r.get("jumlah", 0) or 0) for r in rows)
    if total == 0:
        stats = _empty_stats()
        stats["total_sesi"] = total_sesi
        return stats

    sehat = 0
    bb_cacat = 0
    bb_tidak_cacat = 0
    tidak_layak = 0

    for r in rows:
        kat_info = r.get("detail_kategori") or {}
        kat_name = kat_info.get("nama_kategori", "").strip()
        jml = int(r.get("jumlah", 0) or 0)

        if kat_name == "Healthy":
            sehat += jml
        elif kat_name == "Bacterial Blotch Tidak Cacat":
            bb_tidak_cacat += jml
        elif kat_name == "Bacterial Blotch Cacat":
            bb_cacat += jml
        elif kat_name in ["Dry Bubble", "Cobweb Disease", "Wet Bubble"]:
            tidak_layak += jml

    sehat_pct = round((sehat / total) * 100, 1)
    bb_cacat_pct = round((bb_cacat / total) * 100, 1)
    bb_tidak_cacat_pct = round((bb_tidak_cacat / total) * 100, 1)
    tidak_layak_pct = round((tidak_layak / total) * 100, 1)
    keberhasilan_rate = round(((sehat + bb_tidak_cacat) / total) * 100, 1)
    reject_rate = tidak_layak_pct

    return {
        "total_jamur": total,
        "jamur_sehat": sehat,
        "bb_total": bb_cacat + bb_tidak_cacat,
        "bb_cacat": bb_cacat,
        "bb_tidak_cacat": bb_tidak_cacat,
        "tidak_layak": tidak_layak,
        "total_sesi": total_sesi,
        "keberhasilan_rate": keberhasilan_rate,
        "rata_rata_hari": round(total / max(1, total_sesi), 1),
        "reject_rate": reject_rate,
        "percentages": {
            "sehat": sehat_pct,
            "bb_cacat": bb_cacat_pct,
            "bb_tidak_cacat": bb_tidak_cacat_pct,
            "tidak_layak": tidak_layak_pct,
        },
        "is_empty": False,
        "rows": rows
    }


def _empty_stats():
    return {
        "total_jamur": 0,
        "jamur_sehat": 0,
        "bb_total": 0,
        "bb_cacat": 0,
        "bb_tidak_cacat": 0,
        "tidak_layak": 0,
        "total_sesi": 0,
        "keberhasilan_rate": 0.0,
        "rata_rata_hari": 0.0,
        "reject_rate": 0.0,
        "percentages": {
            "sehat": 0.0,
            "bb_cacat": 0.0,
            "bb_tidak_cacat": 0.0,
            "tidak_layak": 0.0,
        },
        "is_empty": True,
        "rows": []
    }


def fetch_laporan_stats(dari_str: str = "", sampai_str: str = "") -> dict:
    """
    Mengambil statistik agregat untuk laporan berdasarkan rentang tanggal.
    Memperbaiki pemetaan kategori tanpa bergantung pada kolom text kategori.
    """
    client = get_supabase()

    GRADE_COLORS = {
        "sehat": "#4ADE80",
        "bb_tidak_cacat": "#3B82F6",
        "bb_cacat": "#7C3AED",
        "tidak_layak": "#EF4444",
    }
    KAT6_COLORS = {
        "Healthy": "#4ADE80",
        "Bacterial Blotch Tidak Cacat": "#3B82F6",
        "Bacterial Blotch Cacat": "#7C3AED",
        "Dry Bubble": "#F59E0B",
        "Cobweb Disease": "#EC4899",
        "Wet Bubble": "#EF4444",
    }

    empty = {
        "dari": dari_str or "",
        "sampai": sampai_str or "",
        "total_jamur": 0, "total_sesi": 0,
        "sehat": 0, "bb_tidak_cacat": 0, "bb_cacat": 0, "tidak_layak": 0,
        "kat6": {n: 0 for n in DETAIL_KATEGORI_NAMES},
        "keberhasilan_rate": 0.0, "reject_rate": 0.0,
        "percentages": {"sehat": 0.0, "bb_tidak_cacat": 0.0, "bb_cacat": 0.0, "tidak_layak": 0.0},
        "kat6_pct": {n: 0.0 for n in DETAIL_KATEGORI_NAMES},
        "per_hari": [],
        "grade_colors": GRADE_COLORS,
        "kat6_colors": KAT6_COLORS,
        "is_empty": True,
    }

    if client is None:
        return empty

    try:
        query = client.table("sesi") \
            .select("id, tanggal, nomor_sesi, waktu_mulai, waktu_selesai, inspeksi(sesi_id, kategori_id, jumlah, detail_kategori(id, nama_kategori))")

        if dari_str and str(dari_str).strip():
            query = query.gte("tanggal", dari_str.strip())
        if sampai_str and str(sampai_str).strip():
            query = query.lte("tanggal", sampai_str.strip())

        resp = query.order("tanggal", desc=False).order("nomor_sesi", desc=False).execute()
        raw_sessions = resp.data or []
    except Exception as e:
        print(f"[ERROR] fetch_laporan_stats: {e}")
        return empty

    if not raw_sessions:
        return {**empty, "dari": dari_str or "", "sampai": sampai_str or ""}

    total_jamur = 0
    total_sesi = len(raw_sessions)
    sehat = bb_tidak_cacat = bb_cacat = tidak_layak = 0
    kat6 = {n: 0 for n in DETAIL_KATEGORI_NAMES}
    hari_dict = {}

    def _grade_of(kat_name: str):
        if kat_name == "Healthy":
            return "sehat"
        if kat_name == "Bacterial Blotch Tidak Cacat":
            return "bb_tidak_cacat"
        if kat_name == "Bacterial Blotch Cacat":
            return "bb_cacat"
        return "tidak_layak"

    for s in raw_sessions:
        date_str = s.get("tanggal") or ""
        insp_items = s.get("inspeksi") or []

        if date_str not in hari_dict:
            hari_dict[date_str] = {
                "tanggal": date_str,
                "tanggal_fmt": "/".join(reversed(date_str.split("-"))) if "-" in date_str else date_str,
                "sehat": 0, "bb_tidak_cacat": 0, "bb_cacat": 0, "tidak_layak": 0, "total": 0,
            }

        for item in insp_items:
            jml = int(item.get("jumlah", 0) or 0)
            if jml <= 0:
                continue

            kat_info = item.get("detail_kategori") or {}
            kat_official = kat_info.get("nama_kategori")
            if kat_official not in DETAIL_KATEGORI_NAMES:
                continue

            grade = _grade_of(kat_official)

            total_jamur += jml
            kat6[kat_official] = kat6.get(kat_official, 0) + jml
            if grade == "sehat":
                sehat += jml
            elif grade == "bb_tidak_cacat":
                bb_tidak_cacat += jml
            elif grade == "bb_cacat":
                bb_cacat += jml
            else:
                tidak_layak += jml

            hari_dict[date_str]["total"] += jml
            hari_dict[date_str][grade] = hari_dict[date_str].get(grade, 0) + jml

    if total_jamur == 0:
        return {**empty, "dari": dari_str, "sampai": sampai_str, "total_sesi": total_sesi}

    def pct(v): return round(v / total_jamur * 100, 1)

    return {
        "dari": dari_str,
        "sampai": sampai_str,
        "total_jamur": total_jamur,
        "total_sesi": total_sesi,
        "sehat": sehat,
        "bb_tidak_cacat": bb_tidak_cacat,
        "bb_cacat": bb_cacat,
        "tidak_layak": tidak_layak,
        "kat6": kat6,
        "keberhasilan_rate": round((sehat + bb_tidak_cacat) / total_jamur * 100, 1),
        "reject_rate": pct(tidak_layak),
        "percentages": {
            "sehat": pct(sehat),
            "bb_tidak_cacat": pct(bb_tidak_cacat),
            "bb_cacat": pct(bb_cacat),
            "tidak_layak": pct(tidak_layak),
        },
        "kat6_pct": {n: pct(v) for n, v in kat6.items()},
        "per_hari": list(hari_dict.values()),
        "grade_colors": GRADE_COLORS,
        "kat6_colors": KAT6_COLORS,
        "is_empty": False,
    }


def fetch_history_by_days():
    """
    Mengambil riwayat sesi inspeksi dikelompokkan berdasarkan hari dan nomor sesi.
    Menggunakan join resmi detail_kategori.
    """
    client = get_supabase()
    if client is None:
        return []

    try:
        resp = client.table("sesi") \
            .select("id, tanggal, nomor_sesi, waktu_mulai, waktu_selesai, inspeksi(sesi_id, kategori_id, jumlah, detail_kategori(id, nama_kategori))") \
            .order("tanggal", desc=True) \
            .order("nomor_sesi", desc=True) \
            .execute()
        raw_sessions = resp.data or []
    except Exception as e:
        print(f"[ERROR] Gagal mengambil riwayat sesi: {e}")
        return []

    days_dict = {}

    category_color_map = {
        "Healthy": "#4ADE80",
        "Bacterial Blotch Tidak Cacat": "#3B82F6",
        "Bacterial Blotch Cacat": "#7C3AED",
        "Dry Bubble": "#F59E0B",
        "Cobweb Disease": "#EC4899",
        "Wet Bubble": "#EF4444"
    }

    category_badge_map = {
        "Healthy": "healthy",
        "Bacterial Blotch Tidak Cacat": "bb-safe",
        "Bacterial Blotch Cacat": "bb-defect",
        "Dry Bubble": "unfit",
        "Cobweb Disease": "unfit",
        "Wet Bubble": "unfit"
    }

    category_status_map = {
        "Healthy": "Normal",
        "Bacterial Blotch Tidak Cacat": "Toleransi",
        "Bacterial Blotch Cacat": "Cacat",
        "Dry Bubble": "Reject",
        "Cobweb Disease": "Reject",
        "Wet Bubble": "Reject"
    }

    for s in raw_sessions:
        date_str = s.get("tanggal") or "-"
        try:
            d_parts = date_str.split("-")
            date_formatted = f"{d_parts[2]}/{d_parts[1]}/{d_parts[0]}" if len(d_parts) == 3 else date_str
        except Exception:
            date_formatted = date_str

        if date_formatted not in days_dict:
            days_dict[date_formatted] = {
                "date": date_formatted,
                "raw_date": date_str,
                "total_jamur": 0,
                "total_sesi": 0,
                "sehat": 0,
                "bb_safe": 0,
                "bb_defect": 0,
                "reject": 0,
                "sessions_list": []
            }

        day_entry = days_dict[date_formatted]
        day_entry["total_sesi"] += 1

        waktu_mulai_raw = s.get("waktu_mulai")
        waktu_selesai_raw = s.get("waktu_selesai")
        waktu_mulai_str = "-"
        waktu_selesai_str = "Sedang Berjalan"

        if waktu_mulai_raw:
            try:
                dt_m = datetime.fromisoformat(str(waktu_mulai_raw).replace("Z", "+00:00")).astimezone(WIB)
                waktu_mulai_str = dt_m.strftime("%H:%M:%S")
            except Exception:
                waktu_mulai_str = str(waktu_mulai_raw)[11:19]

        if waktu_selesai_raw:
            try:
                dt_s = datetime.fromisoformat(str(waktu_selesai_raw).replace("Z", "+00:00")).astimezone(WIB)
                waktu_selesai_str = dt_s.strftime("%H:%M:%S")
            except Exception:
                waktu_selesai_str = str(waktu_selesai_raw)[11:19]

        rentang_sesi = f"{waktu_mulai_str} - {waktu_selesai_str} WIB" if waktu_mulai_str != "-" else "-"

        insp_items = s.get("inspeksi") or []
        session_total = sum(int(i.get("jumlah", 0) or 0) for i in insp_items)

        kategori_counts = {name: 0 for name in DETAIL_KATEGORI_NAMES}
        for item in insp_items:
            kat_info = item.get("detail_kategori") or {}
            kat_name = kat_info.get("nama_kategori")
            jml = int(item.get("jumlah", 0) or 0)
            if kat_name in kategori_counts:
                kategori_counts[kat_name] += jml

        day_entry["total_jamur"] += session_total
        day_entry["sehat"] += kategori_counts["Healthy"]
        day_entry["bb_safe"] += kategori_counts["Bacterial Blotch Tidak Cacat"]
        day_entry["bb_defect"] += kategori_counts["Bacterial Blotch Cacat"]
        day_entry["reject"] += (
            kategori_counts["Dry Bubble"] +
            kategori_counts["Cobweb Disease"] +
            kategori_counts["Wet Bubble"]
        )

        kategori_rows = []
        for name in DETAIL_KATEGORI_NAMES:
            cnt = kategori_counts[name]
            pct = round((cnt / session_total) * 100, 1) if session_total > 0 else 0.0
            kategori_rows.append({
                "nama": name,
                "jumlah": cnt,
                "persen": pct,
                "badge": category_badge_map.get(name, "healthy"),
                "status": category_status_map.get(name, "Normal"),
                "warna": category_color_map.get(name, "#4ADE80")
            })

        session_obj = {
            "id": s.get("id"),
            "session_label": f"Sesi #{s.get('nomor_sesi', 1)}",
            "nomor_sesi": s.get("nomor_sesi", 1),
            "tanggal": date_formatted,
            "waktu_mulai": waktu_mulai_str,
            "waktu_selesai": waktu_selesai_str,
            "rentang_sesi": rentang_sesi,
            "total_jamur": session_total,
            "sehat": kategori_counts["Healthy"],
            "bb_safe": kategori_counts["Bacterial Blotch Tidak Cacat"],
            "bb_defect": kategori_counts["Bacterial Blotch Cacat"],
            "reject": (
                kategori_counts["Dry Bubble"] +
                kategori_counts["Cobweb Disease"] +
                kategori_counts["Wet Bubble"]
            ),
            "kategori_counts": kategori_counts,
            "kategori_rows": kategori_rows,
            "chart_labels": DETAIL_KATEGORI_NAMES,
            "chart_values": [kategori_counts[name] for name in DETAIL_KATEGORI_NAMES],
            "chart_colors": [category_color_map[name] for name in DETAIL_KATEGORI_NAMES]
        }
        day_entry["sessions_list"].append(session_obj)

    return list(days_dict.values())
