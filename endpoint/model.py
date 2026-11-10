import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from services.supabase_service import (
    determine_grading,
    upload_image_to_storage,
    save_inspection,
)

model_api = Blueprint("model_api", __name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024


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

    # Simpan gambar ke Supabase Storage & hasil ke tabel inspections
    try:
        predicted_class = data.get("prediction", "Unknown")
        confidence_val = data.get("confidence", 0)
        # Jika confidence dalam range 0-1, ubah ke persen
        if isinstance(confidence_val, (int, float)) and confidence_val <= 1.0:
            confidence_pct = confidence_val * 100.0
        else:
            confidence_pct = float(confidence_val)

        grade, servo_target = determine_grading(predicted_class, confidence_pct)
        data["grade"] = grade
        data["servo_target"] = servo_target

        # Upload ke Supabase Storage
        image_url = upload_image_to_storage(image_data, filename, content_type)
        if image_url:
            data["image_url"] = image_url
            # Simpan baris ke database Supabase
            inspection_record = save_inspection(
                image_path=image_url,
                ai_class=predicted_class,
                confidence=confidence_pct,
                grade=grade,
                servo_target=servo_target,
            )
            data["saved_record"] = inspection_record
    except Exception as e:
        current_app.logger.error(f"Gagal integrasi Supabase: {e}")

    return jsonify(data), status
