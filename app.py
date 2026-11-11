import os
import csv
import io
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, jsonify, request, Response
from endpoint.model import model_api
from services.supabase_service import fetch_latest_inspections, calculate_database_stats

try:
    import openpyxl
    from openpyxl.styles import (
        Font, PatternFill, Alignment, Border, Side, GradientFill
    )
    from openpyxl.utils import get_column_letter
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'mushroom-grading-secret-key-2026')
app.config['MODEL_API_URL'] = os.getenv(
    'MODEL_API_URL',
    'http://127.0.0.1:5001/predict'
)
app.register_blueprint(model_api)


def format_inspections(raw_rows):
    """Konversi format data dari Supabase ke format yang dibutuhkan template."""
    if not raw_rows:
        return []

    formatted = []
    for r in raw_rows:
        grade = str(r.get('grade') or 'Sehat')
        badge = 'healthy'
        status = 'Normal'
        display_category = grade

        if 'Tidak Cacat' in grade or 'BB_TIDAK_CACAT' in grade:
            badge = 'bb-safe'
            status = 'Toleransi'
            display_category = 'BB - Tidak Cacat'
        elif 'Cacat' in grade or 'BB_CACAT' in grade:
            badge = 'bb-defect'
            status = 'Cacat'
            display_category = 'BB - Cacat'
        elif 'Tidak Layak' in grade or 'TIDAK_LAYAK' in grade or 'unfit' in grade.lower():
            badge = 'unfit'
            status = 'Reject'
            display_category = 'Tidak Layak Konsumsi'
        elif 'Sehat' in grade or 'Healthy' in grade or 'SEHAT' in grade:
            badge = 'healthy'
            status = 'Normal'
            display_category = 'Sehat'

        # Format servo target agar rapi dibaca
        raw_servo = r.get('servo_target') or '-'
        servo_map = {
            'SERVO_SEHAT': 'Kotak 1: Jamur Sehat',
            'SERVO_BB_TIDAK_CACAT': 'Kotak 2: BB Tidak Cacat',
            'SERVO_BB_CACAT': 'Kotak 3: BB Cacat',
            'SERVO_TIDAK_LAYAK': 'Kotak 4: Jamur Tidak Layak'
        }
        display_servo = servo_map.get(raw_servo, raw_servo)

        # Format waktu — konversi dari UTC ke WIB (UTC+7)
        WIB = timezone(timedelta(hours=7))
        raw_time = r.get('inspected_at')
        time_str = '-'
        date_str = '-'
        if raw_time:
            try:
                dt = datetime.fromisoformat(str(raw_time).replace('Z', '+00:00'))
                dt_wib = dt.astimezone(WIB)
                time_str = dt_wib.strftime('%H:%M:%S')
                date_str = dt_wib.strftime('%d/%m/%Y')
            except Exception:
                time_str = str(raw_time)[11:19] or str(raw_time)[:10]

        conf_val = float(r.get('confidence', 0.0) or 0.0)
        if 0.0 < conf_val <= 1.0:
            conf_val = round(conf_val * 100.0, 1)
        else:
            conf_val = round(conf_val, 1)

        formatted.append({
            'id': str(r.get('id', ''))[:8],
            'full_id': str(r.get('id', '')),
            'image': r.get('image_path') or '',
            'category': display_category,
            'category_badge': badge,
            'ai_class': r.get('ai_class', '-'),
            'confidence': conf_val,
            'time': time_str,
            'date': date_str,
            'servo_target': display_servo,
            'status': status
        })
    return formatted


def get_current_inspections(limit=100):
    """Mengambil data riil dari Supabase tanpa data palsu."""
    supabase_data = fetch_latest_inspections(limit=limit)
    return format_inspections(supabase_data)


def get_live_stats():
    """Mengambil statistik riil dari database Supabase tanpa data dummy."""
    stats = calculate_database_stats()
    return stats


@app.route('/')
def dashboard():
    inspections = get_current_inspections(10)
    stats = get_live_stats()
    return render_template('dashboard.html', active_page='dashboard', inspections=inspections, stats=stats)


@app.route('/proses')
def proses_grading():
    inspections = get_current_inspections(10)
    stats = get_live_stats()
    return render_template('proses_grading.html', active_page='proses', inspections=inspections, stats=stats)


@app.route('/monitoring')
def monitoring():
    return render_template('monitoring.html', active_page='monitoring')


@app.route('/riwayat')
def riwayat_grading():
    inspections = get_current_inspections(100)
    stats = get_live_stats()
    return render_template('riwayat.html', active_page='riwayat', inspections=inspections, stats=stats)


@app.route('/laporan')
def laporan():
    stats = get_live_stats()
    return render_template('laporan.html', active_page='laporan', stats=stats)


# API Endpoints
@app.route('/api/stats')
def api_stats():
    return jsonify(get_live_stats())


@app.route('/api/device-status')
def api_device_status():
    return jsonify({
        'kamera': {'status': 'online', 'fps': 30, 'resolution': '1920x1080'},
        'esp32': {'status': 'online', 'ip': '192.168.1.45', 'ping_ms': 12},
        'sensor_ir': {'status': 'online', 'state': 'monitoring'},
        'servo': {'status': 'online', 'servos': [1, 2, 3], 'position': 'idle'},
        'resnet_api': {'status': 'online', 'model': 'ResNet-101 (Keras)'},
        'database': {'status': 'online', 'engine': 'Supabase PostgreSQL', 'sync': 'Connected'}
    })


@app.route('/api/inspections')
def api_inspections():
    inspections = get_current_inspections(100)
    return jsonify(inspections)


@app.route('/api/export-csv')
def export_csv():
    """Ekspor data inspeksi dari Supabase ke format Excel (.xlsx) dengan styling profesional."""
    raw_data = fetch_latest_inspections(limit=1000)
    WIB = timezone(timedelta(hours=7))
    now_wib = datetime.now(WIB)

    if not OPENPYXL_AVAILABLE:
        # Fallback ke CSV jika openpyxl tidak tersedia
        output = io.BytesIO()
        output.write(b'\xef\xbb\xbf')
        text_buf = io.StringIO()
        writer = csv.writer(text_buf, delimiter=';')
        writer.writerow(['No', 'Tanggal', 'Waktu (WIB)', 'Kategori', 'Kelas AI', 'Konfidensitas (%)', 'Tujuan Sortir'])
        for i, item in enumerate(raw_data or [], start=1):
            raw_time = item.get('inspected_at', '')
            tgl_str, wkt_str = '-', '-'
            if raw_time:
                try:
                    dt = datetime.fromisoformat(str(raw_time).replace('Z', '+00:00'))
                    dt_wib = dt.astimezone(WIB)
                    tgl_str = dt_wib.strftime('%d/%m/%Y')
                    wkt_str = dt_wib.strftime('%H:%M:%S')
                except Exception:
                    pass
            conf_raw = float(item.get('confidence', 0) or 0)
            conf_pct = round(conf_raw * 100.0, 1) if conf_raw <= 1.0 else round(conf_raw, 1)
            writer.writerow([i, tgl_str, wkt_str, item.get('grade', '-'), item.get('ai_class', '-'), conf_pct, item.get('servo_target', '-')])
        output.write(text_buf.getvalue().encode('utf-8'))
        filename = f"Grading_Jamur_{now_wib.strftime('%Y%m%d_%H%M')}.csv"
        return Response(output.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    # ── XLSX dengan openpyxl ──────────────────────────────────────────────────
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Riwayat Grading"

    # ── Palet warna ──────────────────────────────────────────────────────────
    CLR_HEADER_BG  = "2D6A4F"   # hijau tua
    CLR_HEADER_FG  = "FFFFFF"
    CLR_TITLE_BG   = "1B4332"   # hijau sangat tua
    CLR_TITLE_FG   = "FFFFFF"
    CLR_SUB_BG     = "52B788"   # hijau muda
    CLR_SUB_FG     = "FFFFFF"
    CLR_ROW_ODD    = "F0FFF4"   # hijau amat muda
    CLR_ROW_EVEN   = "FFFFFF"
    CLR_SUMM_BG    = "D8F3DC"   # hijau pastel
    CLR_SUMM_LABEL = "1B4332"
    CLR_BORDER     = "B7E4C7"
    CLR_SEHAT      = "D1FAE5"
    CLR_BB_SAFE    = "FEF9C3"
    CLR_BB_DEF     = "FFE4E6"
    CLR_REJECT     = "FECACA"

    thin = Side(style='thin', color=CLR_BORDER)
    medium = Side(style='medium', color="2D6A4F")
    border_cell  = Border(left=thin, right=thin, top=thin, bottom=thin)
    border_header = Border(left=medium, right=medium, top=medium, bottom=medium)

    def fill(hex_color):
        return PatternFill("solid", fgColor=hex_color)

    def font(bold=False, color="000000", size=11, name="Calibri"):
        return Font(bold=bold, color=color, size=size, name=name)

    def align(h="left", v="center", wrap=False):
        return Alignment(horizontal=h, vertical=v, wrap_text=wrap)

    # ── Lebar kolom ──────────────────────────────────────────────────────────
    col_widths = [5, 14, 13, 13, 22, 24, 20, 25, 48]
    for idx, w in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = w

    NUM_COLS = len(col_widths)

    # ── Baris 1: Judul besar ─────────────────────────────────────────────────
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=NUM_COLS)
    c = ws.cell(row=1, column=1, value="🍄  LAPORAN HASIL GRADING JAMUR KANCING")
    c.font      = font(bold=True, color=CLR_TITLE_FG, size=16)
    c.fill      = fill(CLR_TITLE_BG)
    c.alignment = align(h="center")
    ws.row_dimensions[1].height = 36

    # ── Baris 2: Sub-judul ───────────────────────────────────────────────────
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=NUM_COLS)
    c = ws.cell(row=2, column=1, value="Sistem Grading Otomatis Berbasis AI · Mushroom Grading System")
    c.font      = font(bold=False, color=CLR_SUB_FG, size=11)
    c.fill      = fill(CLR_SUB_BG)
    c.alignment = align(h="center")
    ws.row_dimensions[2].height = 22

    # ── Baris 3: Info ekspor ─────────────────────────────────────────────────
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=NUM_COLS)
    total_records = len(raw_data or [])
    info_text = (f"Diekspor pada: {now_wib.strftime('%d %B %Y, %H:%M WIB')}     |"
                 f"     Total Data: {total_records} inspeksi")
    c = ws.cell(row=3, column=1, value=info_text)
    c.font      = font(bold=False, color="374151", size=10)
    c.fill      = fill("F9FBE7")
    c.alignment = align(h="center")
    ws.row_dimensions[3].height = 18

    # ── Baris 4: kosong sebagai spacer ───────────────────────────────────────
    ws.row_dimensions[4].height = 8

    # ── Baris 5: Header kolom ────────────────────────────────────────────────
    headers = ["No", "ID Inspeksi", "Tanggal", "Waktu (WIB)", "Kategori / Grade",
               "Kelas Penyakit (AI)", "Konfidensitas (%)", "Tujuan Sortir", "URL Foto"]
    for col_idx, h in enumerate(headers, start=1):
        c = ws.cell(row=5, column=col_idx, value=h)
        c.font      = font(bold=True, color=CLR_HEADER_FG, size=11)
        c.fill      = fill(CLR_HEADER_BG)
        c.alignment = align(h="center")
        c.border    = border_header
    ws.row_dimensions[5].height = 24
    ws.freeze_panes = "A6"  # freeze sampai baris header

    # ── Baris data ───────────────────────────────────────────────────────────
    grade_color_map = {
        "sehat":       CLR_SEHAT,
        "bb - tidak":  CLR_BB_SAFE,
        "bb_tidak":    CLR_BB_SAFE,
        "bb - cacat":  CLR_BB_DEF,
        "bb_cacat":    CLR_BB_DEF,
        "tidak layak": CLR_REJECT,
        "tidak_layak": CLR_REJECT,
    }

    for i, item in enumerate(raw_data or [], start=1):
        row_num = 5 + i
        raw_time = item.get('inspected_at', '')
        tgl_str, wkt_str = '-', '-'
        if raw_time:
            try:
                dt = datetime.fromisoformat(str(raw_time).replace('Z', '+00:00'))
                dt_wib = dt.astimezone(WIB)
                tgl_str = dt_wib.strftime('%d/%m/%Y')
                wkt_str = dt_wib.strftime('%H:%M:%S')
            except Exception:
                tgl_str = str(raw_time)[:10]
                wkt_str = str(raw_time)[11:19]

        conf_raw = float(item.get('confidence', 0) or 0)
        conf_pct = round(conf_raw * 100.0, 1) if conf_raw <= 1.0 else round(conf_raw, 1)

        grade_str = str(item.get('grade', '-'))
        grade_lower = grade_str.lower()
        row_bg = CLR_ROW_ODD if i % 2 == 1 else CLR_ROW_EVEN

        # Tentukan warna baris berdasarkan grade
        for key, color in grade_color_map.items():
            if key in grade_lower:
                row_bg = color
                break

        row_data = [
            i,
            str(item.get('id', ''))[:8],
            tgl_str,
            wkt_str,
            grade_str,
            item.get('ai_class', '-'),
            conf_pct,
            item.get('servo_target', '-'),
            item.get('image_path', '-')
        ]

        h_aligns = ["center", "center", "center", "center", "left", "left", "center", "left", "left"]
        for col_idx, val in enumerate(row_data, start=1):
            c = ws.cell(row=row_num, column=col_idx, value=val)
            c.fill      = fill(row_bg)
            c.font      = font(size=10)
            c.alignment = align(h=h_aligns[col_idx - 1])
            c.border    = border_cell
        ws.row_dimensions[row_num].height = 20

    # ── Spacer sebelum ringkasan ──────────────────────────────────────────────
    spacer_row = 5 + total_records + 1
    ws.row_dimensions[spacer_row].height = 10

    # ── Blok Ringkasan ────────────────────────────────────────────────────────
    if raw_data:
        total = len(raw_data)
        sehat     = sum(1 for r in raw_data if ('Sehat' in str(r.get('grade', '')) or 'SEHAT' in str(r.get('grade', ''))) and 'Tidak' not in str(r.get('grade', '')) and 'TIDAK' not in str(r.get('grade', '')))
        bb_tidak  = sum(1 for r in raw_data if 'Tidak Cacat' in str(r.get('grade', '')) or 'BB_TIDAK_CACAT' in str(r.get('grade', '')))
        bb_cacat  = sum(1 for r in raw_data if ('BB - Cacat' in str(r.get('grade', '')) or 'BB_CACAT' in str(r.get('grade', ''))) and 'Tidak Cacat' not in str(r.get('grade', '')) and 'BB_TIDAK_CACAT' not in str(r.get('grade', '')))
        reject    = sum(1 for r in raw_data if 'Tidak Layak' in str(r.get('grade', '')) or 'TIDAK_LAYAK' in str(r.get('grade', '')))

        summ_start = spacer_row + 1

        # Header ringkasan
        ws.merge_cells(start_row=summ_start, start_column=1, end_row=summ_start, end_column=5)
        c = ws.cell(row=summ_start, column=1, value="📊  RINGKASAN HASIL GRADING")
        c.font      = font(bold=True, color=CLR_TITLE_FG, size=12)
        c.fill      = fill(CLR_HEADER_BG)
        c.alignment = align(h="center")
        ws.row_dimensions[summ_start].height = 24

        # Sub-header ringkasan
        summ_cols_header = ["Kategori", "Jumlah (unit)", "Persentase (%)", "Destinasi Sortir", "Keterangan"]
        for col_idx, h in enumerate(summ_cols_header, start=1):
            c = ws.cell(row=summ_start + 1, column=col_idx, value=h)
            c.font      = font(bold=True, color=CLR_HEADER_FG, size=10)
            c.fill      = fill(CLR_SUB_BG)
            c.alignment = align(h="center")
            c.border    = border_header
        ws.row_dimensions[summ_start + 1].height = 20

        def pct(n): return f"{round(n / total * 100, 1)}%" if total > 0 else "0%"

        summ_rows = [
            ("Jamur Sehat",          sehat,    pct(sehat),    "Kotak 1 · Sehat",           "Layak konsumsi premium",    CLR_SEHAT),
            ("BB - Tidak Cacat",     bb_tidak, pct(bb_tidak), "Kotak 2 · BB Tidak Cacat",  "Layak konsumsi biasa",      CLR_BB_SAFE),
            ("BB - Cacat",           bb_cacat, pct(bb_cacat), "Kotak 3 · BB Cacat",        "Perlu penanganan khusus",   CLR_BB_DEF),
            ("Tidak Layak Konsumsi", reject,   pct(reject),   "Kotak 4 · Reject",          "Dibuang / tidak dipakai",   CLR_REJECT),
            ("TOTAL",                total,    "100%",        "—",                          "—",                         CLR_SUMM_BG),
        ]

        for r_idx, (kat, jml, persen, dest, ket, bg) in enumerate(summ_rows, start=summ_start + 2):
            is_total = r_idx == summ_start + 2 + len(summ_rows) - 1
            row_vals = [kat, jml, persen, dest, ket]
            for col_idx, val in enumerate(row_vals, start=1):
                c = ws.cell(row=r_idx, column=col_idx, value=val)
                c.fill      = fill(bg)
                c.font      = font(bold=is_total, color=CLR_SUMM_LABEL, size=10)
                c.alignment = align(h="center" if col_idx != 1 else "left")
                c.border    = border_cell
            ws.row_dimensions[r_idx].height = 20

    # ── Simpan ke buffer ──────────────────────────────────────────────────────
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"Grading_Jamur_{now_wib.strftime('%Y%m%d_%H%M')}.xlsx"
    return Response(
        output.read(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
