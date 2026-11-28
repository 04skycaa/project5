import os
import json
import socket
import threading
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import cv2
import numpy as np
from dotenv import load_dotenv
from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from services.grading import CLASS_NORMALIZATION, determine_grading
from services.pcv_analysis import analyze_bacterial_blotch

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://mcmhaiqwgurlfwoncwry.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

_supabase_client = None
_detail_kategori_name_to_id = None
_detail_kategori_id_to_name = None
_session_lock = threading.Lock()
_inspection_lock = threading.Lock()
_processed_detections = set()
_processed_detections_lock = threading.Lock()
_pending_session_map = {}

WIB = timezone(timedelta(hours=7))
DETAIL_KATEGORI_NAMES = [
    "Healthy",
    "Bacterial Blotch Tidak Cacat",
    "Bacterial Blotch Cacat",
    "Dry Bubble",
    "Cobweb Disease",
    "Wet Bubble",
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


def get_detail_kategori_maps():
    """Mengembalikan peta nama/id kategori dari Supabase dengan cache dalam memori."""
    global _detail_kategori_name_to_id, _detail_kategori_id_to_name
    if _detail_kategori_name_to_id and _detail_kategori_id_to_name:
        return _detail_kategori_name_to_id, _detail_kategori_id_to_name

    client = get_supabase()
    if client is None:
        return {}, {}

    try:
        response = client.table("detail_kategori").select("id, nama_kategori").execute()
        if response.data:
            _detail_kategori_name_to_id = {
                row["nama_kategori"]: row["id"] for row in response.data
            }
            _detail_kategori_id_to_name = {
                row["id"]: row["nama_kategori"] for row in response.data
            }
            return _detail_kategori_name_to_id, _detail_kategori_id_to_name
    except Exception as e:
        print(f"[ERROR] Gagal mengambil detail_kategori: {e}")
    return {}, {}


def get_detail_kategori_map():
    """Mengembalikan {nama_kategori: uuid}."""
    name_to_id, _ = get_detail_kategori_maps()
    return name_to_id


def get_detail_kategori_reverse_map():
    """Mengembalikan {uuid: nama_kategori}."""
    _, id_to_name = get_detail_kategori_maps()
    return id_to_name


def map_to_detail_kategori(predicted_class: str = None, grade: str = None) -> str:
    """Memetakan hasil AI dan grading ke salah satu kategori resmi."""
    p_clean = str(predicted_class or "").strip().replace(" ", "_")
    g_clean = str(grade or "").strip().upper()

    if p_clean == "Healthy" or g_clean == "SEHAT":
        return "Healthy"
    if p_clean == "Bacterial_Blotch":
        if g_clean == "BB_TIDAK_CACAT":
            return "Bacterial Blotch Tidak Cacat"
        if g_clean == "BB_CACAT":
            return "Bacterial Blotch Cacat"
        return None
    if p_clean == "Dry_Bubble":
        return "Dry Bubble"
    if p_clean == "Cobweb_Disease":
        return "Cobweb Disease"
    if p_clean == "Wet_Bubble":
        return "Wet Bubble"
    return None


def get_current_wib_date_str():
    """Mendapatkan tanggal hari ini dalam format YYYY-MM-DD zona waktu WIB."""
    return datetime.now(WIB).strftime("%Y-%m-%d")


def save_first_inspection_with_pending_session(
    pending_session_id: str,
    grade: str,
    ai_class: str = None,
    confidence: float = None,
    servo_target: str = None,
    pcv_result: dict = None,
    detection_id: str = None,
) -> dict:
    """Membuat sesi baru dan mencatat inspeksi pertama secara atomik."""
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
            "ai_class": ai_class,
        }

    kategori_id = get_detail_kategori_map().get(nama_kategori)
    if not kategori_id:
        raise ValueError(f"kategori_id untuk '{nama_kategori}' tidak ditemukan di database.")

    with _session_lock:
        existing_session = _pending_session_map.get(pending_session_id)
        if existing_session and existing_session.get("id"):
            saved_data = save_inspection(
                grade=grade,
                sesi_id=existing_session["id"],
                ai_class=ai_class,
                confidence=confidence,
                servo_target=servo_target,
                pcv_result=pcv_result,
                detection_id=detection_id,
            )
            saved_data["sesi_info"] = existing_session
            return saved_data

        if detection_id:
            with _processed_detections_lock:
                if detection_id in _processed_detections:
                    return {"already_processed": True, "grade": grade, "ai_class": ai_class}
                if len(_processed_detections) > 10000:
                    _processed_detections.clear()
                _processed_detections.add(detection_id)

        today_str = get_current_wib_date_str()
        now_iso = datetime.now(timezone.utc).isoformat()
        created_session = None
        saved_data = None

        try:
            try:
                rpc_response = client.rpc("create_session_with_first_inspection", {
                    "p_tanggal": today_str,
                    "p_waktu_mulai": now_iso,
                    "p_kategori_id": kategori_id,
                }).execute()
                if rpc_response.data:
                    row = rpc_response.data[0]
                    created_session = {
                        "id": row["sesi_id"],
                        "tanggal": row["tanggal"],
                        "nomor_sesi": row["nomor_sesi"],
                        "waktu_mulai": row["waktu_mulai"],
                        "waktu_selesai": row["waktu_selesai"],
                    }
                    saved_data = {
                        "sesi_id": row["sesi_id"],
                        "kategori_id": row["kategori_id"],
                        "jumlah": row["jumlah"],
                    }
            except Exception as rpc_error:
                print(
                    "[INFO] RPC atomik belum tersedia "
                    f"({rpc_error}), menggunakan transaksi kompensasi ber-lock."
                )

            if not created_session:
                client.table("sesi") \
                    .update({"waktu_selesai": now_iso}) \
                    .is_("waktu_selesai", "null") \
                    .execute()

                existing_today = client.table("sesi") \
                    .select("nomor_sesi") \
                    .eq("tanggal", today_str) \
                    .order("nomor_sesi", desc=True) \
                    .limit(1) \
                    .execute()
                next_number = 1
                if existing_today.data:
                    next_number = int(existing_today.data[0].get("nomor_sesi", 0)) + 1

                session_response = client.table("sesi").insert({
                    "tanggal": today_str,
                    "nomor_sesi": next_number,
                    "waktu_mulai": now_iso,
                    "waktu_selesai": None,
                }).execute()
                if not session_response.data:
                    raise RuntimeError("Gagal membuat baris sesi baru di Supabase.")
                created_session = session_response.data[0]

                try:
                    inspection_response = client.table("inspeksi").insert({
                        "sesi_id": created_session["id"],
                        "kategori_id": kategori_id,
                        "jumlah": 1,
                    }).execute()
                    if not inspection_response.data:
                        raise RuntimeError("Gagal menyimpan baris inspeksi pertama.")
                    saved_data = inspection_response.data[0]
                except Exception:
                    try:
                        client.table("sesi").delete().eq("id", created_session["id"]).execute()
                    except Exception as rollback_error:
                        print(f"[CRITICAL] Rollback hapus sesi gagal: {rollback_error}")
                    raise

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
        except Exception:
            if detection_id:
                with _processed_detections_lock:
                    _processed_detections.discard(detection_id)
            raise


def save_inspection(
    grade: str,
    sesi_id: str,
    ai_class: str = None,
    confidence: float = None,
    servo_target: str = None,
    pcv_result: dict = None,
    detection_id: str = None,
) -> dict:
    """Menambahkan jumlah kategori inspeksi pada sesi aktif."""
    client = get_supabase()
    if client is None:
        raise RuntimeError("Supabase client belum terkonfigurasi (SUPABASE_KEY kosong atau salah).")
    if not sesi_id:
        raise ValueError("sesi_id wajib diberikan. Tidak ada sesi aktif untuk menyimpan inspeksi.")

    if detection_id:
        with _processed_detections_lock:
            if detection_id in _processed_detections:
                print(f"[INFO] Deteksi {detection_id} sudah pernah diproses. Melewati penambahan hitungan.")
                return {
                    "sesi_id": sesi_id,
                    "already_processed": True,
                    "grade": grade,
                    "ai_class": ai_class,
                }
            if len(_processed_detections) > 10000:
                _processed_detections.clear()
            _processed_detections.add(detection_id)

    session_response = client.table("sesi") \
        .select("id, waktu_selesai") \
        .eq("id", sesi_id) \
        .limit(1) \
        .execute()
    if not session_response.data:
        raise ValueError(f"Sesi dengan ID {sesi_id} tidak ditemukan.")
    if session_response.data[0].get("waktu_selesai") is not None:
        raise ValueError(
            f"Sesi {sesi_id} sudah ditutup "
            f"({session_response.data[0]['waktu_selesai']}). Tidak dapat menambahkan inspeksi."
        )

    nama_kategori = map_to_detail_kategori(ai_class, grade)
    if not nama_kategori:
        return {
            "sesi_id": sesi_id,
            "saved": False,
            "message": "Hasil analisis tidak masuk ke dalam 6 kategori resmi database (tidak dihitung).",
            "grade": grade,
            "ai_class": ai_class,
        }

    kategori_id = get_detail_kategori_map().get(nama_kategori)
    if not kategori_id:
        raise ValueError(f"kategori_id untuk '{nama_kategori}' tidak ditemukan di database.")

    with _inspection_lock:
        existing = client.table("inspeksi") \
            .select("jumlah") \
            .eq("sesi_id", sesi_id) \
            .eq("kategori_id", kategori_id) \
            .limit(1) \
            .execute()
        if existing.data:
            current_count = int(existing.data[0].get("jumlah", 0) or 0)
            update_response = client.table("inspeksi") \
                .update({"jumlah": current_count + 1}) \
                .eq("sesi_id", sesi_id) \
                .eq("kategori_id", kategori_id) \
                .execute()
            if not update_response.data:
                raise RuntimeError(f"Gagal memperbarui jumlah inspeksi untuk kategori {nama_kategori}.")
            saved_data = update_response.data[0]
        else:
            insert_response = client.table("inspeksi").insert({
                "sesi_id": sesi_id,
                "kategori_id": kategori_id,
                "jumlah": 1,
            }).execute()
            if not insert_response.data:
                raise RuntimeError(f"Gagal menambahkan baris inspeksi baru untuk kategori {nama_kategori}.")
            saved_data = insert_response.data[0]

    saved_data["nama_kategori"] = nama_kategori
    saved_data["grade"] = grade
    saved_data["ai_class"] = ai_class
    saved_data["confidence"] = confidence
    saved_data["servo_target"] = servo_target
    if pcv_result:
        saved_data["pcv_result"] = pcv_result
    return saved_data

model_api = Blueprint("model_api", __name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024

# Mapping kelas ResNet sesuai model CNN
RESNET_CLASSES = {
    0: "Bacterial_Blotch",
    1: "Cobweb_Disease",
    2: "Dry_Bubble",
    3: "Healthy",
    4: "Wet_Bubble",
}

# Keputusan sortir jika PCV/Grading mengalami kegagalan teknis
FALLBACK_GRADE = "TIDAK_LAYAK"
FALLBACK_SERVO = "SERVO_TIDAK_LAYAK"


@model_api.post("/api/camera/predict")
def camera_predict():
    image = request.files.get("image")
    sesi_id = (request.form.get("sesi_id") or "").strip()
    pending_session_id = (request.form.get("pending_session_id") or "").strip()
    detection_id = request.form.get("detection_id") or uuid4().hex

    if image is None or not image.filename:
        return jsonify({"error": "Gambar tidak ditemukan."}), 400

    if not image.mimetype or not image.mimetype.startswith("image/"):
        return jsonify({"error": "File harus berupa gambar."}), 400

    if not sesi_id and not pending_session_id:
        return jsonify({
            "error": "Sesi tidak aktif atau belum dimulai. Harap mulai sesi terlebih dahulu."
        }), 400

    image_data = image.stream.read(MAX_IMAGE_BYTES + 1)
    if not image_data:
        return jsonify({"error": "Gambar kosong."}), 400
    if len(image_data) > MAX_IMAGE_BYTES:
        return jsonify({"error": "Ukuran gambar maksimal 10 MB."}), 413

    # Kirim ke backend inference ResNet
    boundary = uuid4().hex
    filename = secure_filename(image.filename) or "image.jpg"
    content_type = image.mimetype or "application/octet-stream"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + image_data + f"\r\n--{boundary}--\r\n".encode("ascii")

    api_request = Request(
        current_app.config["MODEL_API_URL"],
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    try:
        with urlopen(api_request, timeout=60) as response:
            status = response.status
            response_body = response.read()
    except HTTPError as error:
        status = error.code
        response_body = error.read()
    except (socket.timeout, TimeoutError):
        return jsonify({"error": "API CNN terlalu lama merespons."}), 504
    except URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            return jsonify({"error": "API CNN terlalu lama merespons."}), 504
        return jsonify({"error": "API CNN tidak dapat dihubungi."}), 503

    try:
        data = json.loads(response_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return jsonify({
            "error": "API model tidak mengembalikan JSON.",
            "detail": response_body.decode("utf-8", errors="replace")[:500],
            "status": status,
        }), 502

    if not 200 <= status < 300:
        return jsonify({
            "error": "API CNN gagal memproses gambar.",
            "detail": data,
            "status": status,
        }), status

    # ══════════════════════════════════════════════════════════════════════════
    # STANDARISASI HASIL PREDIKSI RESNET
    # ══════════════════════════════════════════════════════════════════════════
    raw_pred = data.get("prediction", "Unknown")

    if isinstance(raw_pred, int) and raw_pred in RESNET_CLASSES:
        predicted_class = RESNET_CLASSES[raw_pred]
    else:
        if isinstance(raw_pred, dict):
            pred_label = (
                raw_pred.get("class_name")
                or raw_pred.get("disease")
                or raw_pred.get("predicted_class")
                or raw_pred.get("class_code")
                or raw_pred.get("prediction")
                or "Unknown"
            )
        else:
            pred_label = raw_pred

        pred_raw_str = str(pred_label).strip()
        pred_clean = pred_raw_str.lower().replace(" ", "_")
        if pred_raw_str in CLASS_NORMALIZATION:
            predicted_class = CLASS_NORMALIZATION[pred_raw_str]
        elif pred_clean in CLASS_NORMALIZATION:
            predicted_class = CLASS_NORMALIZATION[pred_clean]
        else:
            predicted_class = pred_raw_str.replace(" ", "_")

    confidence_val = data.get("confidence")
    if confidence_val is None and isinstance(raw_pred, dict):
        confidence_val = raw_pred.get("confidence", 0)
    if confidence_val is None:
        confidence_val = 0

    if isinstance(confidence_val, (int, float)) and confidence_val <= 1.0:
        confidence_pct = confidence_val * 100.0
    else:
        confidence_pct = float(confidence_val)

    # ══════════════════════════════════════════════════════════════════════════
    # ANALISIS PCV (HANYA jika prediksi == "Bacterial_Blotch")
    # ══════════════════════════════════════════════════════════════════════════
    pcv_result = None

    if predicted_class == "Bacterial_Blotch":
        try:
            nparr = np.frombuffer(image_data, np.uint8)
            img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if img_bgr is None or img_bgr.size == 0:
                raise ValueError("Format data gambar tidak dapat didecode oleh OpenCV.")

            pcv_result = analyze_bacterial_blotch(img_bgr)

        except ValueError as pcv_val_err:
            current_app.logger.warning(f"Validasi PCV gagal: {pcv_val_err}")
            # Kegagalan teknis analisis PCV tidak disimpan ke kategori valid
            return jsonify({
                "status": "error",
                "error": f"Analisis PCV gagal: {pcv_val_err}",
                "grade": FALLBACK_GRADE,
                "servo_target": FALLBACK_SERVO,
                "prediction": predicted_class,
                "confidence": confidence_pct / 100.0 if confidence_pct > 1 else confidence_pct,
                "probabilities": data.get("probabilities", {}),
                "pcv_required": True,
                "pcv_result": None
            }), 422

        except Exception as pcv_err:
            current_app.logger.error(f"Sistem PCV error: {pcv_err}")
            return jsonify({
                "status": "error",
                "error": f"Kesalahan sistem PCV: {pcv_err}",
                "grade": FALLBACK_GRADE,
                "servo_target": FALLBACK_SERVO,
                "prediction": predicted_class,
                "confidence": confidence_pct / 100.0 if confidence_pct > 1 else confidence_pct,
                "probabilities": data.get("probabilities", {}),
                "pcv_required": True,
                "pcv_result": None
            }), 500

    # ══════════════════════════════════════════════════════════════════════════
    # PENENTUAN GRADING DAN TARGET SERVO
    # ══════════════════════════════════════════════════════════════════════════
    try:
        grading_result = determine_grading(predicted_class, pcv_result=pcv_result)
    except ValueError as grade_val_err:
        current_app.logger.warning(f"Validasi grading gagal: {grade_val_err}")
        return jsonify({
            "status": "error",
            "error": f"Grading tidak valid: {grade_val_err}",
            "grade": FALLBACK_GRADE,
            "servo_target": FALLBACK_SERVO,
            "prediction": predicted_class,
            "confidence": confidence_pct / 100.0 if confidence_pct > 1 else confidence_pct,
            "probabilities": data.get("probabilities", {}),
            "pcv_required": (predicted_class == "Bacterial_Blotch"),
            "pcv_result": pcv_result
        }), 422
    except Exception as grade_err:
        current_app.logger.error(f"Sistem grading error: {grade_err}")
        return jsonify({
            "status": "error",
            "error": f"Kesalahan grading: {grade_err}",
            "grade": FALLBACK_GRADE,
            "servo_target": FALLBACK_SERVO,
            "prediction": predicted_class,
            "confidence": confidence_pct / 100.0 if confidence_pct > 1 else confidence_pct,
            "probabilities": data.get("probabilities", {}),
            "pcv_required": (predicted_class == "Bacterial_Blotch"),
            "pcv_result": pcv_result
        }), 500

    grade = grading_result["grade"]
    servo_target = grading_result["servo_target"]

    data["grade"] = grade
    data["servo_target"] = servo_target
    data["pcv_required"] = grading_result.get("pcv_required", False)
    data["pcv_result"] = grading_result.get("pcv_result")

    # ══════════════════════════════════════════════════════════════════════════
    # SIMPAN KE SUPABASE (Sesi aktif ATAU promosikan pending_session atomik)
    # ══════════════════════════════════════════════════════════════════════════
    try:
        if sesi_id:
            saved_record = save_inspection(
                grade=grade,
                sesi_id=sesi_id,
                ai_class=predicted_class,
                confidence=confidence_pct,
                servo_target=servo_target,
                pcv_result=pcv_result,
                detection_id=detection_id
            )
        else:
            saved_record = save_first_inspection_with_pending_session(
                pending_session_id=pending_session_id,
                grade=grade,
                ai_class=predicted_class,
                confidence=confidence_pct,
                servo_target=servo_target,
                pcv_result=pcv_result,
                detection_id=detection_id
            )

        if saved_record.get("saved") is False:
            return jsonify({
                "status": "error",
                "error": saved_record.get("message", "Hasil tidak masuk dalam kategori valid."),
                "prediction": predicted_class,
                "grade": grade,
                "db_saved": False
            }), 422

        data["saved_record"] = saved_record
        if saved_record.get("sesi_info"):
            data["sesi_info"] = saved_record["sesi_info"]
        data["db_saved"] = True
    except Exception as e:
        current_app.logger.error(f"Gagal simpan inspeksi ke Supabase: {e}")
        # Jangan kirim sukses palsu! Beritahu klien bahwa data gagal disimpan
        return jsonify({
            "status": "error",
            "error": f"Penyimpanan database gagal: {str(e)}",
            "prediction": predicted_class,
            "grade": grade,
            "confidence": confidence_pct / 100.0 if confidence_pct > 1 else confidence_pct,
            "probabilities": data.get("probabilities", {}),
            "servo_target": servo_target,
            "pcv_result": pcv_result,
            "db_saved": False
        }), 500

    return jsonify(data), 200
