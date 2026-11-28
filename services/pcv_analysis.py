
# 1. IMPORT LIBRARY

import cv2
import numpy as np
from skimage.filters import threshold_yen


# 2. INISIALISASI CLAHE

clahe = cv2.createCLAHE(
    clipLimit=2.0,
    tileGridSize=(8, 8)
)


# 3. FUNGSI ANALISIS PCV

def analyze_bacterial_blotch(img):

    # Memeriksa citra
    if img is None:
        raise ValueError("Gambar tidak tersedia")

    # Mengubah citra ke grayscale
    gray = cv2.cvtColor(
        img,
        cv2.COLOR_BGR2GRAY
    )

    # Membuat mask objek jamur
    _, mask_awal = cv2.threshold(
        gray,
        40,
        255,
        cv2.THRESH_BINARY
    )

    contours, _ = cv2.findContours(
        mask_awal,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    if not contours:
        raise ValueError("Objek jamur tidak ditemukan")

    # Mengambil kontur terbesar
    kontur_terbesar = max(
        contours,
        key=cv2.contourArea
    )

    object_mask = np.zeros(
        gray.shape,
        dtype=np.uint8
    )

    cv2.drawContours(
        object_mask,
        [kontur_terbesar],
        -1,
        255,
        thickness=cv2.FILLED
    )

    # Menghitung luas objek jamur
    mushroom_area = cv2.countNonZero(object_mask)

    if mushroom_area == 0:
        raise ValueError("Luas objek jamur bernilai 0")

    # Konversi citra ke HSV
    hsv = cv2.cvtColor(
        img,
        cv2.COLOR_BGR2HSV
    )

    H, S, V = cv2.split(hsv)

    # Menerapkan CLAHE pada kanal Value
    v_clahe = clahe.apply(V)

    # Mengambil piksel V hasil CLAHE pada area jamur
    v_pixels = v_clahe[object_mask == 255]

    if len(v_pixels) == 0:
        raise ValueError("Piksel objek tidak ditemukan")

    # Yen Thresholding
    threshold = threshold_yen(v_pixels)

    # Membuat mask kandidat bercak
    spot_mask = np.zeros(
        v_clahe.shape,
        dtype=np.uint8
    )

    spot_mask[
        (v_clahe < threshold) &
        (object_mask == 255)
    ] = 255

    # Deteksi kontur bercak
    spot_contours, _ = cv2.findContours(
        spot_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    # Menghitung luas kandidat bercak
    spot_area = cv2.countNonZero(spot_mask)

    # Menghitung persentase bercak
    spot_percentage = (
        spot_area / mushroom_area
    ) * 100

    # Menghitung total perimeter
    total_perimeter = sum(
        cv2.arcLength(contour, True)
        for contour in spot_contours
    )

    # Mengembalikan hasil PCV
    return {
        "threshold_yen": float(threshold),
        "mushroom_area": int(mushroom_area),
        "spot_area": int(spot_area),
        "spot_percentage": float(spot_percentage),
        "total_perimeter": float(total_perimeter)
    }
