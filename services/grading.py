BB_SPOT_THRESHOLD = 10.0

NON_BB_DISEASES = {
    "Dry_Bubble",
    "Cobweb_Disease",
    "Wet_Bubble",
}


def determine_grading(prediction, pcv_result=None):
    """
    Menentukan grade dan target servo berdasarkan
    hasil klasifikasi ResNet dan analisis PCV.

    Input:
        prediction: nama kelas diagnosa AI (str)
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

    prediction = str(prediction).strip()

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
    elif prediction in NON_BB_DISEASES:
        return {
            "grade": "TIDAK_LAYAK",
            "servo_target": "SERVO_TIDAK_LAYAK",
            "pcv_required": False,
            "pcv_result": None
        }

    else:
        raise ValueError(f"Kelas prediksi tidak dikenal: {prediction}")
