"""Statistik sesi realtime dan rekomendasi penanganan penyakit oleh LLM (tidak disimpan)."""
import json
import os
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from endpoint.model import DETAIL_KATEGORI_NAMES, get_supabase

BB_NAMES = ("Bacterial Blotch Tidak Cacat", "Bacterial Blotch Cacat")
NON_BB_DISEASES = ("Dry Bubble", "Cobweb Disease", "Wet Bubble")
BB_LABEL = "Bacterial Blotch"

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"    

SYSTEM_PROMPT = (
    "Kamu asisten budidaya jamur kancing (Agaricus bisporus) untuk operator sortasi di Indonesia. "
    "Tulis saran penanganan yang singkat dan praktis dalam bahasa Indonesia sederhana. "
    "Gunakan teks biasa tanpa markdown (jangan pakai tanda #, *, atau -). "
    "Jangan menyebut dosis atau merek bahan kimia/pestisida; fokus pada sanitasi, kelembapan, "
    "ventilasi, suhu, penanganan jamur terdampak, dan pencegahan. "
    "Jangan mengarang fakta; bila tidak yakin, sarankan konfirmasi ke penyuluh atau ahli budidaya."
)


class SesiTidakValid(Exception):
    """Sesi tidak ditemukan atau belum selesai."""


def get_session_counts(sesi_id: str) -> dict:
    """Jumlah jamur per 6 kategori resmi untuk satu sesi (dari database)."""
    client = get_supabase()
    if client is None:
        raise RuntimeError("Supabase belum terkonfigurasi.")
    resp = client.table("inspeksi") \
        .select("jumlah, detail_kategori(nama_kategori)") \
        .eq("sesi_id", sesi_id) \
        .execute()
    counts = {name: 0 for name in DETAIL_KATEGORI_NAMES}
    for row in resp.data or []:
        name = (row.get("detail_kategori") or {}).get("nama_kategori")
        if name in counts:
            counts[name] += int(row.get("jumlah") or 0)
    return counts


def build_stats(counts: dict) -> dict:
    total = sum(counts.values())
    rows = [{
        "nama": name,
        "jumlah": counts[name],
        "persen": round(counts[name] / total * 100, 1) if total else 0.0,
    } for name in DETAIL_KATEGORI_NAMES]
    return {"total": total, "rows": rows}


def pick_target_diseases(counts: dict) -> list:
    """
    Aturan: BB (cacat + tidak cacat digabung) vs penyakit lain.
    - BB terbanyak     -> BB + penyakit non-BB terbanyak (kalau ada)
    - Non-BB terbanyak -> penyakit itu saja
    - Tidak ada penyakit -> []
    Jika seri, BB menang (sort stabil mempertahankan urutan awal).
    """
    bb_total = sum(counts[n] for n in BB_NAMES)
    candidates = [(BB_LABEL, bb_total)] + [(n, counts[n]) for n in NON_BB_DISEASES]
    candidates = [c for c in candidates if c[1] > 0]
    if not candidates:
        return []
    candidates.sort(key=lambda c: c[1], reverse=True)
    top = candidates[0]
    if top[0] != BB_LABEL:
        return [top]
    return [top] + candidates[1:2]


def build_prompt(targets: list, counts: dict) -> str:
    total = sum(counts.values())
    rincian = ", ".join(f"{name}: {counts[name]}" for name in DETAIL_KATEGORI_NAMES)
    daftar = "\n".join(f"- {label} ({jumlah} jamur)" for label, jumlah in targets)
    return (
        f"Satu sesi sortasi berisi {total} jamur. Rincian: {rincian}.\n"
        "Catatan: jumlah Bacterial Blotch sudah menggabungkan yang cacat dan tidak cacat.\n\n"
        "Buat rekomendasi penanganan untuk penyakit berikut, sesuai urutan:\n"
        f"{daftar}\n\n"
        "Untuk tiap penyakit tulis: nama penyakit sebagai judul satu baris, lalu satu kalimat "
        "penyebab umum, tiga langkah penanganan, dan satu kalimat pencegahan. "
        "Maksimal 120 kata per penyakit."
    )


def call_llm(prompt: str) -> str:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    if not api_key or not model:
        raise RuntimeError("GROQ_API_KEY atau LLM_MODEL belum diatur di file .env.")

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 900,
    }).encode("utf-8")
    request = Request(
        GROQ_URL,
        data=body,
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            "authorization": f"Bearer {api_key}",
            # User-Agent eksplisit: sebagian layanan di balik Cloudflare menolak
            # User-Agent bawaan urllib.
            "user-agent": "project5-mushroom-grading/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=45) as response:
            data = json.loads(response.read())
    except HTTPError as error:
        if error.code == 401:
            raise RuntimeError("API key LLM tidak valid atau sudah dicabut.") from error
        if error.code == 429:
            raise RuntimeError("Batas pemakaian LLM tercapai. Coba lagi beberapa saat.") from error
        raise RuntimeError(f"LLM menolak permintaan (HTTP {error.code}).") from error
    except (socket.timeout, TimeoutError):
        raise RuntimeError("LLM terlalu lama merespons.") from None
    except URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            raise RuntimeError("LLM terlalu lama merespons.") from None
        raise RuntimeError("LLM tidak dapat dihubungi.") from error
    except json.JSONDecodeError:
        raise RuntimeError("Respons LLM tidak valid.") from None

    choices = data.get("choices") or []
    text = ((choices[0].get("message") or {}).get("content") or "").strip() if choices else ""
    if not text:
        raise RuntimeError("LLM tidak mengembalikan teks.")
    return text

def generate_recommendation(sesi_id: str) -> dict:
    """Buat rekomendasi LLM untuk sesi yang sudah selesai (tidak disimpan di database)."""
    client = get_supabase()
    if client is None:
        raise RuntimeError("Supabase belum terkonfigurasi.")

    resp = client.table("sesi") \
        .select("id, waktu_selesai") \
        .eq("id", sesi_id) \
        .limit(1) \
        .execute()
    if not resp.data:
        raise SesiTidakValid("Sesi tidak ditemukan.")
    if resp.data[0].get("waktu_selesai") is None:
        raise SesiTidakValid("Sesi masih berjalan. Rekomendasi dibuat setelah sesi diakhiri.")

    counts = get_session_counts(sesi_id)
    targets = pick_target_diseases(counts)
    result = {"stats": build_stats(counts), "targets": [t[0] for t in targets]}

    if not targets:
        return {**result, "status": "no_disease", "text": None}
    return {**result, "status": "ready", "text": call_llm(build_prompt(targets, counts))}