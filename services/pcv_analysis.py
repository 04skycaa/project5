import cv2
import numpy as np

OBJECT_THRESH = 40
GAUSSIAN_KERNEL = (5, 5)
MORPH_CLOSE_KERNEL_SIZE = 5
MIN_OBJECT_AREA = 500
MIN_CONTRAST = 30.0
MIN_SPOT_AREA = 20
GLARE_V_UPPER = 240
MORPH_OPEN_KERNEL_SIZE = 3


def analyze_bacterial_blotch(image, use_clahe=False):
    """
    Analisis kandidat area gelap pada jamur menggunakan:
    - Gaussian Blur + Grayscale threshold 40 + Morphology Close untuk segmentasi objek
    - Convex Hull untuk memulihkan lekukan kontur akibat bercak gelap di tepi
    - HSV Value channel (opsi CLAHE)
    - Filter pantulan cahaya (glare > 240)
    - Threshold Otsu + verifikasi kontras (MIN_CONTRAST)
    - Morphology Open + Connected Components filtering (MIN_SPOT_AREA)
    - Perhitungan persentase area gelap

    Parameters:
        image: gambar OpenCV format BGR
        use_clahe: bool (default False), opsi CLAHE pada channel Value

    Returns:
        dict: {
            'threshold_v': float,
            'surface_area': int,
            'spot_area': int,
            'spot_percentage': float,
            'contrast': float
        }
    """
    if image is None or not isinstance(image, np.ndarray):
        raise ValueError("Gambar tidak valid.")

    if image.size == 0:
        raise ValueError("Gambar kosong.")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    blurred = cv2.GaussianBlur(gray, GAUSSIAN_KERNEL, 0)

    _, binary = cv2.threshold(
        blurred,
        OBJECT_THRESH,
        255,
        cv2.THRESH_BINARY
    )

    close_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (MORPH_CLOSE_KERNEL_SIZE, MORPH_CLOSE_KERNEL_SIZE)
    )
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, close_kernel)

    contours, _ = cv2.findContours(
        binary,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    if not contours:
        raise ValueError("Objek jamur tidak ditemukan.")

    largest_contour = max(contours, key=cv2.contourArea)
    contour_area = cv2.contourArea(largest_contour)

    if contour_area < MIN_OBJECT_AREA:
        raise ValueError(
            f"Luas objek jamur ({contour_area} px) lebih kecil dari MIN_OBJECT_AREA ({MIN_OBJECT_AREA} px)."
        )

    hull = cv2.convexHull(largest_contour)
    object_mask = np.zeros(gray.shape, dtype=np.uint8)
    cv2.drawContours(object_mask, [hull], -1, 255, thickness=cv2.FILLED)

    surface_area = cv2.countNonZero(object_mask)
    if surface_area == 0:
        raise ValueError("Luas objek jamur tidak valid.")

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    value_channel = hsv[:, :, 2]

    if use_clahe:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        value_channel = clahe.apply(value_channel)

    in_object = (object_mask == 255)
    v_pixels_all = value_channel[in_object]
    v_pixels = v_pixels_all[v_pixels_all <= GLARE_V_UPPER]

    if v_pixels.size == 0:
        return {
            "threshold_v": 0.0,
            "surface_area": int(surface_area),
            "spot_area": 0,
            "spot_percentage": 0.0,
            "contrast": 0.0
        }
    
    v_pixels = v_pixels.astype(np.uint8)

    threshold_v, _ = cv2.threshold(
        v_pixels.reshape(-1, 1),
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    threshold_v = float(threshold_v)

    dark_group = v_pixels[v_pixels <= threshold_v]
    bright_group = v_pixels[v_pixels > threshold_v]

    if dark_group.size == 0 or bright_group.size == 0:
        contrast = 0.0
    else:
        mean_dark = float(dark_group.mean())
        mean_bright = float(bright_group.mean())
        contrast = mean_bright - mean_dark

    if contrast < MIN_CONTRAST:
        return {
            "threshold_v": round(threshold_v, 2),
            "surface_area": int(surface_area),
            "spot_area": 0,
            "spot_percentage": 0.0,
            "contrast": round(contrast, 2)
        }

    spot_binary = np.zeros(gray.shape, dtype=np.uint8)
    dark_pixels = (value_channel <= threshold_v) & in_object
    spot_binary[dark_pixels] = 255

    open_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (MORPH_OPEN_KERNEL_SIZE, MORPH_OPEN_KERNEL_SIZE)
    )
    spot_binary = cv2.morphologyEx(spot_binary, cv2.MORPH_OPEN, open_kernel)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(spot_binary, connectivity=8)
    clean_spot = np.zeros(gray.shape, dtype=np.uint8)
    for i in range(1, num_labels):
        if stats[i, cv2.CC_STAT_AREA] >= MIN_SPOT_AREA:
            clean_spot[labels == i] = 255

    spot_area = cv2.countNonZero(clean_spot)
    spot_percentage = (spot_area / surface_area) * 100.0

    return {
        "threshold_v": round(threshold_v, 2),
        "surface_area": int(surface_area),
        "spot_area": int(spot_area),
        "spot_percentage": round(float(spot_percentage), 2),
        "contrast": round(contrast, 2)
    }
