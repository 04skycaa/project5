import os
from datetime import datetime, timezone
from uuid import uuid4
from dotenv import load_dotenv

load_dotenv()

# Konfigurasi Supabase dari environment variable
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://mcmhaiqwgurlfwoncwry.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "mushroom-images")

_supabase_client = None


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


def determine_grading(ai_class: str, confidence: float = 0.0):
    """
    Fallback penentuan grade jika dipanggil tanpa hasil PCV.
    Untuk logika grading utama dengan PCV, gunakan services.grading.determine_grading.
    """
    cls_clean = (ai_class or "").strip().replace(" ", "_")
    conf_pct = confidence * 100.0 if confidence <= 1.0 else confidence

    if cls_clean == "Healthy":
        return "SEHAT", "SERVO_SEHAT"
    elif cls_clean == "Bacterial_Blotch":
        if conf_pct >= 90.0:
            return "BB_CACAT", "SERVO_BB_CACAT"
        return "BB_TIDAK_CACAT", "SERVO_BB_TIDAK_CACAT"
    elif cls_clean in ("Dry_Bubble", "Wet_Bubble", "Cobweb_Disease"):
        return "TIDAK_LAYAK", "SERVO_TIDAK_LAYAK"
    else:
        return "TIDAK_LAYAK", "SERVO_TIDAK_LAYAK"


def upload_image_to_storage(image_bytes: bytes, filename: str = None, content_type: str = "image/jpeg") -> str:
    """
    Mengunggah file gambar ke Supabase Storage (bucket: mushroom-images)
    dan mengembalikan Public URL gambar.
    """
    client = get_supabase()
    if client is None:
        return ""

    if not filename:
        ext = "jpg"
        if "png" in (content_type or ""):
            ext = "png"
        filename = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.{ext}"

    safe_filename = filename.replace(" ", "_")

    try:
        client.storage.from_(STORAGE_BUCKET).upload(
            path=safe_filename,
            file=image_bytes,
            file_options={"content-type": content_type or "image/jpeg", "upsert": "true"}
        )
        return client.storage.from_(STORAGE_BUCKET).get_public_url(safe_filename)
    except Exception as e:
        print(f"[ERROR] Gagal upload gambar ke Supabase Storage: {e}")
        return ""


def save_inspection(
    image_path: str,
    ai_class: str,
    confidence: float,
    grade: str = None,
    servo_target: str = None,
    pcv_result: dict = None
) -> dict:
    """
    Menyimpan data hasil grading jamur ke tabel 'inspections' di Supabase.
    Meneruskan pcv_result (threshold_v, spot_percentage, dll) ke objek rekaman inspeksi.
    """
    client = get_supabase()
    if client is None:
        return None

    if not grade or not servo_target:
        auto_grade, auto_servo = determine_grading(ai_class, confidence)
        grade = grade or auto_grade
        servo_target = servo_target or auto_servo

    conf_val = float(confidence)
    if conf_val > 1.0:
        conf_val = conf_val / 100.0

    data_payload = {
        "image_path": image_path,
        "inspected_at": datetime.now(timezone.utc).isoformat(),
        "ai_class": ai_class,
        "confidence": round(conf_val, 4),
        "grade": grade,
        "servo_target": servo_target
    }

    try:
        response = client.table("inspections").insert(data_payload).execute()
        saved_row = response.data[0] if (response.data and len(response.data) > 0) else data_payload
        if pcv_result:
            saved_row["pcv_result"] = pcv_result
        return saved_row
    except Exception as e:
        print(f"[ERROR] Gagal menyimpan inspeksi ke Supabase: {e}")
        return None


def fetch_latest_inspections(limit: int = 100):
    """Mengambil riwayat inspeksi terbaru dari Supabase."""
    client = get_supabase()
    if client is None:
        return []

    try:
        response = client.table("inspections") \
            .select("*") \
            .order("inspected_at", desc=True) \
            .limit(limit) \
            .execute()
        return response.data or []
    except Exception as e:
        print(f"[ERROR] Gagal mengambil riwayat inspeksi dari Supabase: {e}")
        return []


def calculate_database_stats():
    """
    Menghitung statistik lengkap langsung dari data tabel inspections di Supabase.
    Digunakan secara real-time untuk Dashboard, Laporan, dan Proses Grading.
    """
    client = get_supabase()
    rows = []
    if client is not None:
        try:
            response = client.table("inspections").select("*").execute()
            rows = response.data or []
        except Exception as e:
            print(f"[ERROR] Gagal mengambil data statistik dari Supabase: {e}")

    total = len(rows)
    if total == 0:
        # Fallback jika database belum ada data
        return {
            "total_jamur": 0,
            "jamur_sehat": 0,
            "bb_total": 0,
            "bb_cacat": 0,
            "bb_tidak_cacat": 0,
            "tidak_layak": 0,
            "keberhasilan_rate": 0.0,
            "rata_rata_hari": 0.0,
            "avg_accuracy": 0.0,
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
    total_conf = 0.0

    dates_seen = set()

    for r in rows:
        grade = str(r.get("grade") or "").strip()
        conf = float(r.get("confidence") or 0.0)
        conf_pct = conf * 100.0 if conf <= 1.0 else conf
        total_conf += conf_pct

        insp_at = r.get("inspected_at")
        if insp_at:
            dates_seen.add(str(insp_at)[:10])

        if "Tidak Cacat" in grade or "BB_TIDAK_CACAT" in grade:
            bb_tidak_cacat += 1
        elif "Cacat" in grade or "BB_CACAT" in grade:
            bb_cacat += 1
        elif "Tidak Layak" in grade or "TIDAK_LAYAK" in grade or "unfit" in grade.lower():
            tidak_layak += 1
        elif "Sehat" in grade or "Healthy" in grade or "SEHAT" in grade:
            sehat += 1
        else:
            tidak_layak += 1

    sehat_pct = round((sehat / total) * 100, 1)
    bb_cacat_pct = round((bb_cacat / total) * 100, 1)
    bb_tidak_cacat_pct = round((bb_tidak_cacat / total) * 100, 1)
    tidak_layak_pct = round((tidak_layak / total) * 100, 1)
    keberhasilan_rate = round(((sehat + bb_tidak_cacat) / total) * 100, 1)
    avg_accuracy = round(total_conf / total, 1)
    days_count = max(1, len(dates_seen))
    rata_rata_hari = round(total / days_count, 1)

    return {
        "total_jamur": total,
        "jamur_sehat": sehat,
        "bb_total": bb_cacat + bb_tidak_cacat,
        "bb_cacat": bb_cacat,
        "bb_tidak_cacat": bb_tidak_cacat,
        "tidak_layak": tidak_layak,
        "keberhasilan_rate": keberhasilan_rate,
        "rata_rata_hari": rata_rata_hari,
        "avg_accuracy": avg_accuracy,
        "reject_rate": tidak_layak_pct,
        "percentages": {
            "sehat": sehat_pct,
            "bb_cacat": bb_cacat_pct,
            "bb_tidak_cacat": bb_tidak_cacat_pct,
            "tidak_layak": tidak_layak_pct,
        },
        "is_empty": False,
        "rows": rows
    }
