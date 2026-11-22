r"""
Unit test untuk services/pcv_analysis.py dan services/grading.py.

Jalankan dengan perintah:
    .\.venv\Scripts\python.exe -m pytest tests/test_pcv_grading.py -v
"""

import numpy as np
import cv2
import pytest

from services.pcv_analysis import analyze_bacterial_blotch, MIN_OBJECT_AREA
from services.grading import determine_grading, BB_SPOT_THRESHOLD


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER PEMBUAT GAMBAR SINTETIS
# ═══════════════════════════════════════════════════════════════════════════════

def create_synthetic_mushroom(size=300, radius=100, mushroom_v=200, spot_radius=0, spot_v=50):
    """
    Membuat citra sintetis BGR jamur bulat:
    - Background hitam (0)
    - Badan jamur dengan intensitas V tertentu
    - Bercak gelap opsional di tengah jamur
    """
    img = np.zeros((size, size, 3), dtype=np.uint8)
    center = (size // 2, size // 2)

    # Gambar badan jamur
    cv2.circle(img, center, radius, (mushroom_v, mushroom_v, mushroom_v), -1)

    # Gambar bercak gelap jika spot_radius > 0
    if spot_radius > 0:
        cv2.circle(img, center, spot_radius, (spot_v, spot_v, spot_v), -1)

    return img


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT TEST PCV ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPCVAnalysis:
    def test_image_none(self):
        """Uji 1: Gambar None harus raise ValueError."""
        with pytest.raises(ValueError, match="tidak valid"):
            analyze_bacterial_blotch(None)

    def test_image_empty(self):
        """Uji 2: Gambar kosong (array size 0) harus raise ValueError."""
        empty_img = np.array([], dtype=np.uint8)
        with pytest.raises(ValueError, match="kosong"):
            analyze_bacterial_blotch(empty_img)

    def test_no_mushroom_detected(self):
        """Uji 3: Gambar hitam tanpa objek jamur harus raise ValueError."""
        black_img = np.zeros((200, 200, 3), dtype=np.uint8)
        with pytest.raises(ValueError, match="Objek jamur tidak ditemukan"):
            analyze_bacterial_blotch(black_img)

    def test_noise_smaller_than_min_object_area(self):
        """Uji 4: Objek kecil di bawah MIN_OBJECT_AREA harus ditolak."""
        tiny_img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.circle(tiny_img, (100, 100), 5, (200, 200, 200), -1)  # Area ~78 px < 500 px
        with pytest.raises(ValueError, match="MIN_OBJECT_AREA"):
            analyze_bacterial_blotch(tiny_img)

    def test_clean_mushroom(self):
        """Uji 5: Jamur bersih tanpa bercak harus menghasilkan spot_percentage = 0%."""
        clean_img = create_synthetic_mushroom(size=300, radius=100, mushroom_v=200, spot_radius=0)
        res = analyze_bacterial_blotch(clean_img)

        assert res["surface_area"] > 0
        assert res["spot_area"] == 0
        assert res["spot_percentage"] == 0.0
        assert "threshold_v" in res
        assert "contrast" in res

    def test_spotted_mushroom(self):
        """Uji 6: Jamur dengan bercak buatan (lingkaran gelap) harus terdeteksi bercaknya."""
        spotted_img = create_synthetic_mushroom(size=300, radius=100, mushroom_v=200, spot_radius=30, spot_v=50)
        res = analyze_bacterial_blotch(spotted_img)

        assert res["surface_area"] > 0
        assert res["spot_area"] > 0
        assert res["spot_percentage"] > 0.0
        assert res["contrast"] >= 30.0  # Kontras terdeteksi signifikan

    def test_clahe_option(self):
        """Uji 7: Parameter use_clahe=True berjalan tanpa exception."""
        spotted_img = create_synthetic_mushroom(size=300, radius=100, mushroom_v=200, spot_radius=30, spot_v=50)
        res = analyze_bacterial_blotch(spotted_img, use_clahe=True)
        assert res["spot_percentage"] >= 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT TEST GRADING
# ═══════════════════════════════════════════════════════════════════════════════

class TestGrading:
    def test_healthy(self):
        """Uji Healthy: Grade SEHAT, servo SERVO_SEHAT, tidak butuh PCV."""
        res = determine_grading("Healthy")
        assert res["grade"] == "SEHAT"
        assert res["servo_target"] == "SERVO_SEHAT"
        assert res["pcv_required"] is False
        assert res["pcv_result"] is None

    def test_non_bb_diseases(self):
        """Uji penyakit non-BB: Grade TIDAK_LAYAK."""
        for disease in ["Dry_Bubble", "Cobweb_Disease", "Wet_Bubble"]:
            res = determine_grading(disease)
            assert res["grade"] == "TIDAK_LAYAK"
            assert res["servo_target"] == "SERVO_TIDAK_LAYAK"
            assert res["pcv_required"] is False

    def test_bb_without_pcv_raises(self):
        """Uji Bacterial_Blotch tanpa pcv_result harus raise ValueError."""
        with pytest.raises(ValueError, match="Hasil PCV diperlukan"):
            determine_grading("Bacterial_Blotch")

    def test_bb_invalid_pcv_structure(self):
        """Uji Bacterial_Blotch dengan struktur pcv_result cacat."""
        with pytest.raises(ValueError, match="dictionary"):
            determine_grading("Bacterial_Blotch", pcv_result="bukan-dict")

        with pytest.raises(ValueError, match="spot_percentage"):
            determine_grading("Bacterial_Blotch", pcv_result={"threshold_v": 100})

        with pytest.raises(ValueError, match="numerik"):
            determine_grading("Bacterial_Blotch", pcv_result={"spot_percentage": "bukan-angka"})

    def test_bb_boundary_grading(self):
        """
        Uji grading di sekitar batas 10% (BB_SPOT_THRESHOLD):
        - 9.9%  -> BB_TIDAK_CACAT
        - 10.0% -> BB_TIDAK_CACAT (<= 10%)
        - 10.1% -> BB_CACAT       (> 10%)
        """
        # Di bawah 10%
        res_below = determine_grading("Bacterial_Blotch", {"spot_percentage": 9.9})
        assert res_below["grade"] == "BB_TIDAK_CACAT"
        assert res_below["servo_target"] == "SERVO_BB_TIDAK_CACAT"

        # Tepat di batas 10%
        res_edge = determine_grading("Bacterial_Blotch", {"spot_percentage": 10.0})
        assert res_edge["grade"] == "BB_TIDAK_CACAT"
        assert res_edge["servo_target"] == "SERVO_BB_TIDAK_CACAT"

        # Di atas 10%
        res_above = determine_grading("Bacterial_Blotch", {"spot_percentage": 10.1})
        assert res_above["grade"] == "BB_CACAT"
        assert res_above["servo_target"] == "SERVO_BB_CACAT"

    def test_unknown_class_raises(self):
        """Uji kelas yang tidak terdaftar harus raise ValueError."""
        with pytest.raises(ValueError, match="tidak dikenal"):
            determine_grading("Penyakit_Misterius")
