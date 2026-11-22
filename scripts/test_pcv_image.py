"""
Script untuk menguji fungsi PCV (HSV Value + Otsu) dan Grading pada gambar nyata / uji.

Penggunaan:
    .\.venv\Scripts\python.exe scripts/test_pcv_image.py
    .\.venv\Scripts\python.exe scripts/test_pcv_image.py "path/ke/foto_jamur.jpg"
"""

import sys
import os
import cv2
import numpy as np

# Pastikan folder project masuk ke sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.pcv_analysis import analyze_bacterial_blotch
from services.grading import determine_grading


def main():
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
        if not os.path.exists(image_path):
            print(f"[ERROR] File tidak ditemukan: {image_path}")
            return
        img = cv2.imread(image_path)
        print(f"\n[INFO] Membaca gambar dari: {image_path}")
    else:
        print("\n[INFO] Tidak ada file input. Membuat gambar simulasi jamur berbercak (Bacterial Blotch)...")
        # Buat jamur sintetis: jamur terang (210) dengan bercak gelap (55)
        img = np.zeros((400, 400, 3), dtype=np.uint8)
        cv2.circle(img, (200, 200), 130, (210, 210, 210), -1)  # jamur
        cv2.circle(img, (200, 200), 45, (55, 55, 55), -1)      # bercak di tengah
        cv2.circle(img, (260, 150), 25, (60, 60, 60), -1)      # bercak tambahan

    # 1. Jalankan analisis PCV
    try:
        pcv_result = analyze_bacterial_blotch(img)
    except Exception as e:
        print(f"[GAGAL] Analisis PCV menghasilkan error: {e}")
        return

    # 2. Jalankan grading untuk Bacterial Blotch
    try:
        grading = determine_grading("Bacterial_Blotch", pcv_result=pcv_result)
    except Exception as e:
        print(f"[GAGAL] Grading menghasilkan error: {e}")
        return

    # 3. Tampilkan hasil di terminal
    print("\n" + "=" * 55)
    print("           HASIL PEMERIKSAAN LOGIKA PCV")
    print("=" * 55)
    print(f"Luas Permukaan Jamur : {pcv_result['surface_area']:,} piksel")
    print(f"Threshold Otsu V     : {pcv_result['threshold_v']}")
    print(f"Kontras (Delta V)    : {pcv_result.get('contrast', '-')}")
    print(f"Luas Area Bercak     : {pcv_result['spot_area']:,} piksel")
    print(f"Persentase Bercak    : {pcv_result['spot_percentage']}%")
    print("-" * 55)
    print(f"Diagnosa AI          : Bacterial_Blotch")
    print(f"Hasil Grading        : {grading['grade']}")
    print(f"Target Servo         : {grading['servo_target']}")
    status_toleransi = (
        "Bercak > 10% (Cacat - Ditolak)"
        if pcv_result['spot_percentage'] > 10
        else "Bercak <= 10% (Toleransi - Lolos)"
    )
    print(f"Keterangan           : {status_toleransi}")
    print("=" * 55)

    # 4. Simpan gambar visualisasi (overlay bercak merah)
    out_file = "debug_pcv_result.png"
    # Highlight area bercak dengan warna merah transparan
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(cv2.GaussianBlur(gray, (5, 5), 0), 40, 255, cv2.THRESH_BINARY)
    cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cnts:
        hull = cv2.convexHull(max(cnts, key=cv2.contourArea))
        mask = np.zeros(gray.shape, dtype=np.uint8)
        cv2.drawContours(mask, [hull], -1, 255, -1)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2]
        spot_mask = (v <= pcv_result['threshold_v']) & (mask == 255)

        overlay = img.copy()
        overlay[spot_mask] = (0, 0, 255)  # Beri warna merah pada bercak
        vis = cv2.addWeighted(img, 0.65, overlay, 0.35, 0)
        cv2.imwrite(out_file, vis)
        print(f"\n[SUKSES] Gambar visualisasi deteksi bercak disimpan ke: {out_file}\n")


if __name__ == "__main__":
    main()

