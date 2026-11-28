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
    save_first_inspection_with_pending_session,
)

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
