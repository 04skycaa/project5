import os
import json
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# Konfigurasi Supabase dari environment variable
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://mcmhaiqwgurlfwoncwry.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

_supabase_client = None
_detail_kategori_cache = None

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

def get_detail_kategori_map():
    """Mengembalikan dict {nama_kategori: uuid} dari tabel detail_kategori."""
    global _detail_kategori_cache
    if _detail_kategori_cache:
        return _detail_kategori_cache

    client = get_supabase()
    if not client:
        return {}

    try:
        resp = client.table("detail_kategori").select("id, nama_kategori").execute()
        if resp.data:
            _detail_kategori_cache = {row["nama_kategori"]: row["id"] for row in resp.data}
            return _detail_kategori_cache
    except Exception as e:
        print(f"[ERROR] Gagal mengambil detail_kategori: {e}")
    return {}


def map_to_detail_kategori(predicted_class: str = None, grade: str = None) -> str:
    """
    Memetakan hasil prediksi AI & grading PCV ke nama kategori persis
    di tabel public.detail_kategori:
    1. Healthy
    2. Bacterial Blotch Tidak Cacat
    3. Bacterial Blotch Cacat
    4. Dry Bubble
    5. Cobweb Disease
    6. Wet Bubble
    """
    p_clean = str(predicted_class or "").strip().replace(" ", "_").lower()
    g_clean = str(grade or "").strip().lower()

    if "healthy" in p_clean or "sehat" in g_clean or "sehat" in p_clean:
        return "Healthy"
    elif "bacterial_blotch" in p_clean or "blotch" in p_clean or "bacterial" in p_clean:
        if "tidak" in g_clean or "safe" in g_clean or "toleransi" in g_clean:
            return "Bacterial Blotch Tidak Cacat"
        else:
            return "Bacterial Blotch Cacat"
    elif "dry_bubble" in p_clean or "dry" in p_clean:
        return "Dry Bubble"
    elif "cobweb" in p_clean:
        return "Cobweb Disease"
    elif "wet_bubble" in p_clean or "wet" in p_clean:
        return "Wet Bubble"
    else:
        if "tidak cacat" in g_clean:
            return "Bacterial Blotch Tidak Cacat"
        elif "cacat" in g_clean:
            return "Bacterial Blotch Cacat"
        elif "sehat" in g_clean:
            return "Healthy"
        elif "cobweb" in g_clean:
            return "Cobweb Disease"
        elif "wet" in g_clean:
            return "Wet Bubble"
        return "Dry Bubble"


# ═══════════════════════════════════════════════════════════════════════════════
# MANAJEMEN SESI ALAT (Tabel: public.sesi)
# "kamera di klik mulai sampai kameranya berakhir atau berhenti ini hitungannya 1 sesi"
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
            .select("*") \
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
    Memulai sesi baru untuk alat yang berjalan:
    1. Jika ada sesi lama yang belum ditutup, tutup terlebih dahulu (waktu_selesai = now()).
    2. Hitung nomor_sesi berikutnya untuk tanggal hari ini (max + 1, mulai dari 1).
    3. Simpan baris baru ke tabel 'sesi'.
    """
    client = get_supabase()
    if client is None:
        return None

    now_wib = datetime.now(WIB)
    today_str = now_wib.strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        # 1. Tutup sesi aktif sebelumnya jika ada
        active = get_active_session()
        if active:
            client.table("sesi") \
                .update({"waktu_selesai": now_iso}) \
                .eq("id", active["id"]) \
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
        return payload

    except Exception as e:
        print(f"[ERROR] Gagal membuat sesi baru: {e}")
        return None


def stop_active_session():
    """
    Menghentikan sesi alat yang sedang berjalan (waktu_selesai = now()).
    """
    client = get_supabase()
    if client is None:
        return None

    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        active = get_active_session()
        if not active:
            return {"message": "Tidak ada sesi aktif saat ini."}

        response = client.table("sesi") \
            .update({"waktu_selesai": now_iso}) \
            .eq("id", active["id"]) \
            .execute()

        if response.data and len(response.data) > 0:
            return response.data[0]
        return {"id": active["id"], "waktu_selesai": now_iso}

    except Exception as e:
        print(f"[ERROR] Gagal menghentikan sesi: {e}")
        return None


def get_or_create_active_session():
    """
    Memastikan selalu ada sesi aktif saat pemeriksaan jamur dilakukan.
    Jika alat belum dinyalakan lewat tombol/kamera, sesi akan otomatis dibuat.
    """
    active = get_active_session()
    if active:
        return active
    return start_new_session()


# ═══════════════════════════════════════════════════════════════════════════════
# PENYIMPANAN DATA INSPEKSI (Tabel: public.inspeksi)
# "total jamur di kategori di tambahkan di kolom jumlah di inspeksi"
# ═══════════════════════════════════════════════════════════════════════════════

def save_inspection(
    grade: str,
    sesi_id: str = None,
    ai_class: str = None,
    confidence: float = None,
    servo_target: str = None,
    pcv_result: dict = None
) -> dict:
    """
    Menyimpan atau menambahkan akumulasi jumlah per kategori ke tabel 'public.inspeksi' di Supabase.
    - Menghubungkan ke sesi_id aktif (relasi ke tabel public.sesi).
    - Mengambil kategori_id dari tabel public.detail_kategori.
    - Menambahkan kolom jumlah (total jamur per kategori di sesi tersebut).
    - Mematuhi constraint unique (sesi_id, kategori_id).
    """
    client = get_supabase()
    if client is None:
        return None

    # Pastikan ada sesi aktif
    if not sesi_id:
        active_sesi = get_or_create_active_session()
        if active_sesi and "id" in active_sesi:
            sesi_id = active_sesi["id"]
    if not sesi_id:
        print("[ERROR] Inspeksi tidak disimpan: sesi aktif tidak tersedia.")
        return None

    nama_kategori = map_to_detail_kategori(ai_class, grade)
    kategori_map = get_detail_kategori_map()
    kategori_id = kategori_map.get(nama_kategori)
    if not kategori_id:
        print(f"[ERROR] Inspeksi tidak disimpan: kategori '{nama_kategori}' tidak ditemukan.")
        return None

    saved_row = None
    try:
        existing_resp = client.table("inspeksi") \
            .select("jumlah") \
            .eq("sesi_id", sesi_id) \
            .eq("kategori_id", kategori_id) \
            .limit(1) \
            .execute()

        if existing_resp.data and len(existing_resp.data) > 0:
            # Baris sudah ada -> update jumlah = jumlah + 1
            curr_row = existing_resp.data[0]
            new_count = int(curr_row.get("jumlah", 0)) + 1
            upd_resp = client.table("inspeksi") \
                .update({"jumlah": new_count, "kategori": nama_kategori}) \
                .eq("sesi_id", sesi_id) \
                .eq("kategori_id", kategori_id) \
                .execute()
            saved_row = upd_resp.data[0] if upd_resp.data else curr_row
            saved_row.update({
                "jumlah": new_count,
                "kategori": nama_kategori,
                "sesi_id": sesi_id,
                "kategori_id": kategori_id
            })
        else:
            # Baris belum ada -> buat baru dengan jumlah = 1
            insert_payload = {
                "kategori": nama_kategori,
                "sesi_id": sesi_id,
                "kategori_id": kategori_id,
                "jumlah": 1
            }
            ins_resp = client.table("inspeksi").insert(insert_payload).execute()
            saved_row = ins_resp.data[0] if ins_resp.data else insert_payload

    except Exception as e:
        print(f"[ERROR] Gagal menyimpan/menambah inspeksi ke Supabase: {e}")
        return None

    # Tempelkan metadata lokal untuk kebutuhan respon JSON & UI
    saved_row["id"] = f"{sesi_id}:{kategori_id}"
    saved_row["nama_kategori"] = nama_kategori
    saved_row["grade"] = grade
    saved_row["ai_class"] = ai_class
    saved_row["confidence"] = confidence
    saved_row["servo_target"] = servo_target
    if pcv_result:
        saved_row["pcv_result"] = pcv_result

    return saved_row


# ═══════════════════════════════════════════════════════════════════════════════
# PENGAMBILAN DATA & STATISTIK (Tabel: public.inspeksi, public.sesi, detail_kategori)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_latest_inspections(limit: int = 100, dari: str = None, sampai: str = None):
    """
    Mengambil riwayat baris agregasi inspeksi terbaru dari Supabase beserta info sesinya.
    Dapat difilter berdasarkan rentang tanggal sesi (dari s/d sampai).
    """
    client = get_supabase()
    if client is None:
        return []
    if limit is not None and limit <= 0:
        return []

    try:
        query = client.table("sesi") \
            .select("id, tanggal, nomor_sesi, waktu_mulai, waktu_selesai, inspeksi!inner(kategori, jumlah, sesi_id, kategori_id)")

        if dari:
            query = query.gte("tanggal", dari)
        if sampai:
            query = query.lte("tanggal", sampai)

        response = query.order("tanggal", desc=True) \
            .order("nomor_sesi", desc=True) \
            .limit(limit) \
            .execute()
        rows = []
        for session in response.data or []:
            session_info = {
                "id": session.get("id"),
                "tanggal": session.get("tanggal"),
                "nomor_sesi": session.get("nomor_sesi"),
                "waktu_mulai": session.get("waktu_mulai"),
                "waktu_selesai": session.get("waktu_selesai")
            }
            for inspection in session.get("inspeksi") or []:
                row = dict(inspection)
                row["id"] = f"{row.get('sesi_id')}:{row.get('kategori_id')}"
                row["sesi"] = session_info
                rows.append(row)
                if limit is not None and len(rows) >= limit:
                    return rows
        return rows
    except Exception as e:
        print(f"[ERROR] Gagal mengambil data inspeksi: {e}")
        return []


def calculate_database_stats():
    """
    Menghitung statistik lengkap langsung dari data tabel 'inspeksi' dan 'sesi' di Supabase.
    Menghitung total jamur berdasarkan jumlah di tabel inspeksi.
    """
    client = get_supabase()
    rows = []
    total_sesi = 0

    if client is not None:
        try:
            resp_insp = client.table("inspeksi").select("kategori, jumlah, sesi_id").execute()
            rows = resp_insp.data or []
        except Exception as e:
            print(f"[ERROR] Gagal mengambil data inspeksi dari Supabase: {e}")

        try:
            resp_sesi = client.table("sesi").select("id, tanggal, nomor_sesi").execute()
            total_sesi = len(resp_sesi.data or [])
        except Exception as e:
            print(f"[ERROR] Gagal mengambil data sesi dari Supabase: {e}")

    total = sum(int(r.get("jumlah", 0) or 0) for r in rows)
    if total == 0:
        return {
            "total_jamur": 0,
            "jamur_sehat": 0,
            "bb_total": 0,
            "bb_cacat": 0,
            "bb_tidak_cacat": 0,
            "tidak_layak": 0,
            "total_sesi": total_sesi,
            "keberhasilan_rate": 0.0,
            "rata_rata_hari": 0.0,
            "avg_accuracy": 98.2,
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

    sehat = 0
    bb_cacat = 0
    bb_tidak_cacat = 0
    tidak_layak = 0

    for r in rows:
        kat = str(r.get("kategori") or "").strip().lower()
        jml = int(r.get("jumlah", 0) or 0)

        if "healthy" in kat or "sehat" in kat:
            sehat += jml
        elif "tidak cacat" in kat or "bb_not_defective" in kat:
            bb_tidak_cacat += jml
        elif "cacat" in kat or "bb_defective" in kat:
            bb_cacat += jml
        else:
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
        "avg_accuracy": 98.2,
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


def fetch_laporan_stats(dari_str: str = "", sampai_str: str = "") -> dict:
    """
    Mengambil statistik agregat untuk laporan berdasarkan rentang tanggal.
    dari_str, sampai_str: format 'YYYY-MM-DD' (kosongkan untuk mengambil semua periode)

    Returns dict dengan:
    - 4 grade box: sehat, bb_tidak_cacat, bb_cacat, tidak_layak (+ total)
    - 6 kategori penyakit: Healthy, BB Tidak Cacat, BB Cacat, Dry Bubble, Cobweb Disease, Wet Bubble
    - per_hari: list [{tanggal, tanggal_fmt, sehat, bb_tidak_cacat, bb_cacat, tidak_layak, total}]
    - total_sesi, keberhasilan_rate, reject_rate, percentages
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
            .select("id, tanggal, nomor_sesi, waktu_mulai, waktu_selesai, inspeksi(kategori, jumlah, kategori_id)")

        if dari_str and str(dari_str).strip():
            query = query.gte("tanggal", dari_str.strip())
        if sampai_str and str(sampai_str).strip():
            query = query.lte("tanggal", sampai_str.strip())

        resp = query.order("tanggal", desc=False) \
            .order("nomor_sesi", desc=False) \
            .execute()
        raw_sessions = resp.data or []
    except Exception as e:
        print(f"[ERROR] fetch_laporan_stats: {e}")
        return empty

    if not raw_sessions:
        return {**empty, "dari": dari_str or "", "sampai": sampai_str or ""}

    # Ambil map kategori_id -> nama
    kat_map = get_detail_kategori_map()

    # Akumulasi global
    total_jamur = 0
    total_sesi = len(raw_sessions)
    sehat = bb_tidak_cacat = bb_cacat = tidak_layak = 0
    kat6 = {n: 0 for n in DETAIL_KATEGORI_NAMES}

    # Akumulasi per hari
    hari_dict: dict = {}

    def _grade_of(kat_name: str):
        """Mapping nama kategori -> grade box."""
        k = kat_name.lower()
        if "healthy" in k or "sehat" in k:
            return "sehat"
        if "tidak cacat" in k or "bb_not" in k:
            return "bb_tidak_cacat"
        if "cacat" in k and "tidak" not in k:
            return "bb_cacat"
        return "tidak_layak"  # Dry Bubble, Cobweb, Wet Bubble

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
            if jml == 0:
                continue

            # Resolve nama kategori
            kid = item.get("kategori_id")
            kat_name = kat_map.get(kid, item.get("kategori", "")) if kid else item.get("kategori", "")
            if not kat_name:
                kat_name = "Dry Bubble"

            # Normalisasi ke 6 nama resmi
            kat_official = None
            for official in DETAIL_KATEGORI_NAMES:
                if official.lower() in kat_name.lower() or kat_name.lower() in official.lower():
                    kat_official = official
                    break
            if not kat_official:
                kat_official = "Dry Bubble"

            grade = _grade_of(kat_official)

            # Akumulasi
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
    Mengambil data riwayat lengkap dari Supabase yang dikelompokkan:
    Hari (Tanggal) -> Sesi (Dropdown) -> Data Diagram Donat (Pop-up).
    """
    client = get_supabase()
    if client is None:
        return []

    try:
        # Ambil seluruh sesi berelasi dengan inspeksi
        resp = client.table("sesi") \
            .select("id, tanggal, nomor_sesi, waktu_mulai, waktu_selesai, inspeksi(kategori, jumlah, kategori_id)") \
            .order("tanggal", desc=True) \
            .order("nomor_sesi", desc=True) \
            .execute()
        raw_sessions = resp.data or []
    except Exception as e:
        print(f"[ERROR] Gagal mengambil riwayat sesi dan inspeksi: {e}")
        return []

    # Kelompokkan sesi per hari (tanggal)
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
        # Format tanggal rapi jika ISO YYYY-MM-DD
        try:
            d_parts = date_str.split("-")
            if len(d_parts) == 3:
                date_formatted = f"{d_parts[2]}/{d_parts[1]}/{d_parts[0]}"
            else:
                date_formatted = date_str
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

        # Format waktu mulai & selesai sesi
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

        # Hitung rincian kategori di sesi ini
        insp_items = s.get("inspeksi") or []
        session_total = sum(int(i.get("jumlah", 0) or 0) for i in insp_items)

        # Inisialisasi 6 kategori default
        kategori_counts = {name: 0 for name in DETAIL_KATEGORI_NAMES}
        for item in insp_items:
            kat_name = item.get("kategori") or "Healthy"
            jml = int(item.get("jumlah", 0) or 0)
            if kat_name in kategori_counts:
                kategori_counts[kat_name] += jml
            else:
                # Normalisasi jika ada perbedaan penamaan
                matched = False
                for def_name in DETAIL_KATEGORI_NAMES:
                    if kat_name.lower() in def_name.lower():
                        kategori_counts[def_name] += jml
                        matched = True
                        break
                if not matched:
                    kategori_counts["Dry Bubble"] += jml

        # Akumulasi ke ringkasan harian
        day_entry["total_jamur"] += session_total
        day_entry["sehat"] += kategori_counts["Healthy"]
        day_entry["bb_safe"] += kategori_counts["Bacterial Blotch Tidak Cacat"]
        day_entry["bb_defect"] += kategori_counts["Bacterial Blotch Cacat"]
        day_entry["reject"] += (
            kategori_counts["Dry Bubble"] +
            kategori_counts["Cobweb Disease"] +
            kategori_counts["Wet Bubble"]
        )

        # Siapkan list baris kategori untuk tabel pop-up & diagram donat
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
            # JSON terstruktur untuk pop-up & Chart.js donat
            "chart_labels": DETAIL_KATEGORI_NAMES,
            "chart_values": [kategori_counts[name] for name in DETAIL_KATEGORI_NAMES],
            "chart_colors": [category_color_map[name] for name in DETAIL_KATEGORI_NAMES]
        }
        day_entry["sessions_list"].append(session_obj)

    # Kembalikan sebagai list
    return list(days_dict.values())
