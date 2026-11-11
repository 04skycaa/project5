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
    upload_image_to_storage,
    save_inspection,
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
    raw_pred = data.get("prediction", "Unknown")

    # Jika model mengembalikan integer indeks, petakan ke label standar
    if isinstance(raw_pred, int) and raw_pred in RESNET_CLASSES:
        predicted_class = RESNET_CLASSES[raw_pred]
    else:
        # Normalisasi spasi dan strip agar persis cocok dengan grading.py
        predicted_class = str(raw_pred).strip().replace(" ", "_")

    confidence_val = data.get("confidence", 0)
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
            _save_record(data, image_data, filename, content_type, predicted_class, confidence_pct, FALLBACK_GRADE, FALLBACK_SERVO, None)
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
            _save_record(data, image_data, filename, content_type, predicted_class, confidence_pct, FALLBACK_GRADE, FALLBACK_SERVO, None)
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
        _save_record(data, image_data, filename, content_type, predicted_class, confidence_pct, FALLBACK_GRADE, FALLBACK_SERVO, pcv_result)
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
        _save_record(data, image_data, filename, content_type, predicted_class, confidence_pct, FALLBACK_GRADE, FALLBACK_SERVO, pcv_result)
        return jsonify(data), 500

    grade = grading_result["grade"]
    servo_target = grading_result["servo_target"]

    data["grade"] = grade
    data["servo_target"] = servo_target
    data["pcv_required"] = grading_result.get("pcv_required", False)
    data["pcv_result"] = grading_result.get("pcv_result")

    # ══════════════════════════════════════════════════════════════════════════
    # SIMPAN KE SUPABASE (STORAGE & DATABASE)
    # ══════════════════════════════════════════════════════════════════════════
    _save_record(data, image_data, filename, content_type, predicted_class, confidence_pct, grade, servo_target, pcv_result)

    return jsonify(data), status


def _save_record(data, image_data, filename, content_type, ai_class, confidence, grade, servo_target, pcv_result):
    """Menyimpan gambar ke Supabase Storage dan hasil pemeriksaan ke Supabase DB."""
    try:
        image_url = upload_image_to_storage(image_data, filename, content_type)
        if image_url:
            data["image_url"] = image_url
            inspection_record = save_inspection(
                image_path=image_url,
                ai_class=ai_class,
                confidence=confidence,
                grade=grade,
                servo_target=servo_target,
                pcv_result=pcv_result
            )
            data["saved_record"] = inspection_record
    except Exception as e:
        current_app.logger.error(f"Gagal integrasi Supabase: {e}")
