import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import cv2
import numpy as np
from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from services.grading import determine_grading
from services.pcv_analysis import analyze_bacterial_blotch
from services.supabase_service import (
    save_inspection,
    get_active_session,
)

model_api = Blueprint("model_api", __name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024

# ═══════════════════════════════════════════════════════════════════════════════
# MAPPING KELAS RESNET
# Label persis sama dengan yang divalidasi pada services/grading.py
# ═══════════════════════════════════════════════════════════════════════════════
RESNET_CLASSES = {
    0: "Bacterial_Blotch",
    1: "Cobweb_Disease",
    2: "Dry_Bubble",
    3: "Healthy",
    4: "Wet_Bubble",
}

# ═══════════════════════════════════════════════════════════════════════════════
# FALLBACK AMAN
# Jika PCV atau grading gagal akibat gambar blur/rusak/noise berlebih,
# servo diarahkan ke "SERVO_TIDAK_LAYAK" agar jamur bermasalah tidak lolos
# ke jalur konsumsi premium / toleransi dan servo fisik tidak menggantung (idle/hang).
# ═══════════════════════════════════════════════════════════════════════════════
FALLBACK_GRADE = "TIDAK_LAYAK"
FALLBACK_SERVO = "SERVO_TIDAK_LAYAK"


@model_api.post("/api/camera/predict")
def camera_predict():
    image = request.files.get("image")

    if image is None or not image.filename:
        return jsonify({"error": "Gambar tidak ditemukan."}), 400

    if not image.mimetype or not image.mimetype.startswith("image/"):
        return jsonify({"error": "File harus berupa gambar."}), 400

    image_data = image.stream.read(MAX_IMAGE_BYTES + 1)
    if not image_data:
        return jsonify({"error": "Gambar kosong."}), 400
    if len(image_data) > MAX_IMAGE_BYTES:
        return jsonify({"error": "Ukuran gambar maksimal 10 MB."}), 413

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
    raw_pred = data.get("prediction")
    if raw_pred is None:
        raw_pred = data.get("predicted_class") or data.get("disease") or "Unknown"

    confidence_val = data.get("confidence")

    # Mapping class_code & varian nama kelas
    CODE_MAP = {
        "w0_hl": "Healthy",
        "w1_bb": "Bacterial_Blotch",
        "w1_db": "Dry_Bubble",
        "w1_kw": "Cobweb_Disease",
        "w1_wb": "Wet_Bubble",
    }
    NAME_MAP = {
        "healthy": "Healthy",
        "bacterial_blotch": "Bacterial_Blotch",
        "bacterial blotch": "Bacterial_Blotch",
        "dry_bubble": "Dry_Bubble",
        "dry bubble": "Dry_Bubble",
        "cobweb": "Cobweb_Disease",
        "cobweb_disease": "Cobweb_Disease",
        "cobweb disease": "Cobweb_Disease",
        "wet_bubble": "Wet_Bubble",
        "wet bubble": "Wet_Bubble",
    }

    # Jika raw_pred adalah dictionary (misal: {'class_code': 'w1_kw', 'class_name': 'Cobweb_Disease', 'confidence': 47.87})
    if isinstance(raw_pred, dict):
        if confidence_val is None and "confidence" in raw_pred:
            confidence_val = raw_pred["confidence"]
        cand = (
            raw_pred.get("class_name")
            or raw_pred.get("disease")
            or raw_pred.get("class_code")
            or raw_pred.get("predicted_class")
            or raw_pred.get("prediction")
            or "Unknown"
        )
    elif isinstance(raw_pred, int) and raw_pred in RESNET_CLASSES:
        cand = RESNET_CLASSES[raw_pred]
    else:
        cand = str(raw_pred).strip()

    cand_str = str(cand).strip()
    if cand_str in CODE_MAP:
        predicted_class = CODE_MAP[cand_str]
    elif cand_str.lower() in NAME_MAP:
        predicted_class = NAME_MAP[cand_str.lower()]
    elif cand_str.lower().replace(" ", "_") in NAME_MAP:
        predicted_class = NAME_MAP[cand_str.lower().replace(" ", "_")]
    else:
        predicted_class = cand_str.replace(" ", "_")

    # Standarisasi confidence
    if confidence_val is None:
        confidence_val = 0
    try:
        confidence_num = float(confidence_val)
    except (ValueError, TypeError):
        confidence_num = 0.0

    if confidence_num > 1.0:
        confidence_pct = confidence_num
        confidence_fraction = confidence_num / 100.0
    else:
        confidence_pct = confidence_num * 100.0
        confidence_fraction = confidence_num

    # Standarisasi probabilities untuk frontend
    probs = data.get("probabilities")
    if not isinstance(probs, dict) or not probs:
        probs = data.get("all_predictions")
    if isinstance(probs, dict) and probs:
        norm_probs = {}
        for k, v in probs.items():
            k_clean = str(k).strip()
            k_norm = CODE_MAP.get(k_clean, NAME_MAP.get(k_clean.lower().replace(" ", "_"), k_clean.replace(" ", "_")))
            try:
                val_f = float(v)
                norm_probs[k_norm] = round(val_f / 100.0 if val_f > 1.0 else val_f, 4)
            except (ValueError, TypeError):
                norm_probs[k_norm] = 0.0
        if predicted_class not in norm_probs:
            norm_probs[predicted_class] = round(confidence_fraction, 4)
        data["probabilities"] = norm_probs
    else:
        data["probabilities"] = {predicted_class: round(confidence_fraction, 4)}

    # Update payload data agar frontend proses_grading.html selalu menerima format valid
    data["status"] = "success"
    data["prediction"] = predicted_class
    data["confidence"] = round(confidence_fraction, 4)

    # ══════════════════════════════════════════════════════════════════════════
    # ANALISIS PCV (HANYA jika prediksi == "Bacterial_Blotch")
    # ══════════════════════════════════════════════════════════════════════════
    pcv_result = None

    if predicted_class == "Bacterial_Blotch":
        try:
            # Decode buffer gambar byte ke format BGR OpenCV murni (bukan RGB PIL)
            nparr = np.frombuffer(image_data, np.uint8)
            img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if img_bgr is None or img_bgr.size == 0:
                raise ValueError("Format data gambar tidak dapat didecode oleh OpenCV.")

            # Jalankan analisis PCV (HSV Value + Otsu)
            pcv_result = analyze_bacterial_blotch(img_bgr)

        except ValueError as pcv_val_err:
            # Error validasi PCV (misal kontur < MIN_OBJECT_AREA atau gambar blur)
            current_app.logger.warning(f"Validasi PCV gagal: {pcv_val_err}")
            data.update({
                "grade": FALLBACK_GRADE,
                "servo_target": FALLBACK_SERVO,
                "pcv_required": True,
                "pcv_result": None,
                "error": f"PCV Validation Error: {pcv_val_err}"
            })
            # Tetap simpan ke database dengan status fallback
            _save_record(data, predicted_class, confidence_pct, FALLBACK_GRADE, FALLBACK_SERVO, None)
            return jsonify(data), 422

        except Exception as pcv_err:
            # Kesalahan sistem tak terduga pada PCV
            current_app.logger.error(f"Sistem PCV error: {pcv_err}")
            data.update({
                "grade": FALLBACK_GRADE,
                "servo_target": FALLBACK_SERVO,
                "pcv_required": True,
                "pcv_result": None,
                "error": f"PCV Processing Error: {pcv_err}"
            })
            _save_record(data, predicted_class, confidence_pct, FALLBACK_GRADE, FALLBACK_SERVO, None)
            return jsonify(data), 500

    # ══════════════════════════════════════════════════════════════════════════
    # PENENTUAN GRADING DAN TARGET SERVO
    # ══════════════════════════════════════════════════════════════════════════
    try:
        grading_result = determine_grading(predicted_class, pcv_result=pcv_result)
    except ValueError as grade_val_err:
        current_app.logger.warning(f"Validasi grading gagal: {grade_val_err}")
        data.update({
            "grade": FALLBACK_GRADE,
            "servo_target": FALLBACK_SERVO,
            "pcv_required": (predicted_class == "Bacterial_Blotch"),
            "pcv_result": pcv_result,
            "error": f"Grading Validation Error: {grade_val_err}"
        })
        _save_record(data, predicted_class, confidence_pct, FALLBACK_GRADE, FALLBACK_SERVO, pcv_result)
        return jsonify(data), 422
    except Exception as grade_err:
        current_app.logger.error(f"Sistem grading error: {grade_err}")
        data.update({
            "grade": FALLBACK_GRADE,
            "servo_target": FALLBACK_SERVO,
            "pcv_required": (predicted_class == "Bacterial_Blotch"),
            "pcv_result": pcv_result,
            "error": f"Grading Processing Error: {grade_err}"
        })
        _save_record(data, predicted_class, confidence_pct, FALLBACK_GRADE, FALLBACK_SERVO, pcv_result)
        return jsonify(data), 500

    grade = grading_result["grade"]
    servo_target = grading_result["servo_target"]

    data["grade"] = grade
    data["servo_target"] = servo_target
    data["pcv_required"] = grading_result.get("pcv_required", False)
    data["pcv_result"] = grading_result.get("pcv_result")

    # ══════════════════════════════════════════════════════════════════════════
    # SIMPAN KE SUPABASE (TABEL INSPEKSI & SESI — TANPA SIMPAN GAMBAR)
    # ══════════════════════════════════════════════════════════════════════════
    _save_record(data, predicted_class, confidence_pct, grade, servo_target, pcv_result)

    return jsonify(data), status


def _save_record(data, ai_class, confidence, grade, servo_target, pcv_result):
    """Menyimpan hasil pemeriksaan ke Supabase DB (tabel inspeksi & sesi) tanpa menyimpan gambar."""
    try:
        active_sesi = get_active_session()
        sesi_id = active_sesi.get("id") if active_sesi else None

        inspection_record = save_inspection(
            grade=grade,
            sesi_id=sesi_id,
            ai_class=ai_class,
            confidence=confidence,
            servo_target=servo_target,
            pcv_result=pcv_result
        )
        if inspection_record:
            data["saved_record"] = inspection_record
            if active_sesi:
                data["sesi_info"] = {
                    "nomor_sesi": active_sesi.get("nomor_sesi"),
                    "tanggal": active_sesi.get("tanggal")
                }
    except Exception as e:
        current_app.logger.error(f"Gagal simpan inspeksi ke Supabase: {e}")
