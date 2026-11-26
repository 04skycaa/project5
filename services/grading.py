BB_SPOT_THRESHOLD = 10.0

NON_BB_DISEASES = {
    "Dry_Bubble",
    "Cobweb_Disease",
    "Cobweb",
    "Wet_Bubble",
}

# Mapping kode model / varian penulisan ke label baku
CLASS_NORMALIZATION = {
    "w0_hl": "Healthy",
    "w1_bb": "Bacterial_Blotch",
    "w1_db": "Dry_Bubble",
    "w1_kw": "Cobweb_Disease",
    "w1_wb": "Wet_Bubble",
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


def determine_grading(prediction, pcv_result=None):
    """
    Menentukan grade dan target servo berdasarkan
    hasil klasifikasi ResNet dan analisis PCV.

    Input:
        prediction: nama kelas diagnosa AI (str atau dict dari model)
        pcv_result: dict hasil analyze_bacterial_blotch() (wajib jika Bacterial_Blotch)

    Output:
        dict: {
            'grade': str,
            'servo_target': str,
            'pcv_required': bool,
            'pcv_result': dict or None
        }
    """
    if prediction is None:
        raise ValueError("Prediction tidak boleh None.")

    # Jika model mengembalikan dictionary (misal: {'class_code': 'w1_kw', 'class_name': 'Cobweb_Disease', 'confidence': 47.87})
    if isinstance(prediction, dict):
        prediction = (
            prediction.get("class_name")
            or prediction.get("disease")
            or prediction.get("predicted_class")
            or prediction.get("class_code")
            or prediction.get("prediction")
            or ""
        )

    prediction_raw = str(prediction).strip()
    prediction_clean = prediction_raw.lower().replace(" ", "_")

    # Petakan ke nama standar jika ada di mapping
    if prediction_raw in CLASS_NORMALIZATION:
        prediction = CLASS_NORMALIZATION[prediction_raw]
    elif prediction_clean in CLASS_NORMALIZATION:
        prediction = CLASS_NORMALIZATION[prediction_clean]
    else:
        prediction = prediction_raw.replace(" ", "_")

    # Kondisi 1: Jamur sehat
    if prediction == "Healthy":
        return {
            "grade": "SEHAT",
            "servo_target": "SERVO_SEHAT",
            "pcv_required": False,
            "pcv_result": None
        }

    # Kondisi 2: Bacterial Blotch
    elif prediction == "Bacterial_Blotch":
        if pcv_result is None:
            raise ValueError("Hasil PCV diperlukan untuk Bacterial Blotch.")

        if not isinstance(pcv_result, dict):
            raise ValueError(f"Hasil PCV harus berupa dictionary, diterima {type(pcv_result).__name__}.")

        if "spot_percentage" not in pcv_result:
            raise ValueError("Dictionary hasil PCV harus memiliki key 'spot_percentage'.")

        spot_percentage = pcv_result["spot_percentage"]

        if not isinstance(spot_percentage, (int, float)) or isinstance(spot_percentage, bool):
            raise ValueError(f"Nilai 'spot_percentage' harus bertipe numerik, diterima {type(spot_percentage).__name__}.")

        if spot_percentage > BB_SPOT_THRESHOLD:
            grade = "BB_CACAT"
            servo_target = "SERVO_BB_CACAT"
        else:
            grade = "BB_TIDAK_CACAT"
            servo_target = "SERVO_BB_TIDAK_CACAT"

        return {
            "grade": grade,
            "servo_target": servo_target,
            "pcv_required": True,
            "pcv_result": pcv_result
        }
    elif prediction in NON_BB_DISEASES or prediction == "Cobweb_Disease":
        return {
            "grade": "TIDAK_LAYAK",
            "servo_target": "SERVO_TIDAK_LAYAK",
            "pcv_required": False,
            "pcv_result": None
        }

    else:
        raise ValueError(f"Kelas prediksi tidak dikenal: {prediction_raw}")
