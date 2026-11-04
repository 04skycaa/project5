import os
from flask import Flask, render_template, jsonify, request
from endpoint.model import model_api

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'mushroom-grading-secret-key-2026')
app.config['MODEL_API_URL'] = os.getenv(
    'MODEL_API_URL',
    'http://127.0.0.1:5001/predict'
)
app.register_blueprint(model_api)

# Initial mock data representing live grading results
MOCK_INSPECTIONS = [
    {
        'id': '001',
        'image': 'https://upload.wikimedia.org/wikipedia/commons/thumb/5/55/20210718_Pleurotus_ostreatus_116-138.jpg/960px-20210718_Pleurotus_ostreatus_116-138.jpg',
        'category': 'Sehat',
        'category_badge': 'healthy',
        'ai_class': 'Healthy',
        'confidence': 98.4,
        'probabilities': [
            {'label': 'Healthy', 'value': 98.4},
            {'label': 'Bacterial Blotch', 'value': 0.5},
            {'label': 'Dry Bubble', 'value': 0.4},
            {'label': 'Wet Bubble', 'value': 0.4},
            {'label': 'Cobweb', 'value': 0.3}
        ],
        'discoloration': 0.8,
        'time': '10:23',
        'servo_target': 'Kotak 1: Jamur Sehat',
        'status': 'Normal'
    },
    {
        'id': '002',
        'image': 'https://images.unsplash.com/photo-1546069901-ba9599a7e63c?q=80&w=600&auto=format&fit=crop',
        'category': 'BB - Tidak Cacat',
        'category_badge': 'bb-safe',
        'ai_class': 'Bacterial Blotch',
        'confidence': 94.1,
        'probabilities': [
            {'label': 'Healthy', 'value': 2.4},
            {'label': 'Bacterial Blotch', 'value': 94.1},
            {'label': 'Dry Bubble', 'value': 1.4},
            {'label': 'Wet Bubble', 'value': 1.2},
            {'label': 'Cobweb', 'value': 0.9}
        ],
        'discoloration': 6.4,
        'time': '10:22',
        'servo_target': 'Kotak 2: BB Tidak Cacat',
        'status': 'Toleransi'
    },
    {
        'id': '003',
        'image': 'https://images.unsplash.com/photo-1504280390367-361c6d9f38f4?q=80&w=600&auto=format&fit=crop',
        'category': 'Sehat',
        'category_badge': 'healthy',
        'ai_class': 'Healthy',
        'confidence': 97.9,
        'probabilities': [
            {'label': 'Healthy', 'value': 97.9},
            {'label': 'Bacterial Blotch', 'value': 0.7},
            {'label': 'Dry Bubble', 'value': 0.6},
            {'label': 'Wet Bubble', 'value': 0.5},
            {'label': 'Cobweb', 'value': 0.3}
        ],
        'discoloration': 1.2,
        'time': '10:21',
        'servo_target': 'Kotak 1: Jamur Sehat',
        'status': 'Normal'
    },
    {
        'id': '004',
        'image': 'https://images.unsplash.com/photo-1546069901-ba9599a7e63c?q=80&w=600&auto=format&fit=crop',
        'category': 'BB - Cacat',
        'category_badge': 'bb-defect',
        'ai_class': 'Bacterial Blotch',
        'confidence': 96.8,
        'probabilities': [
            {'label': 'Healthy', 'value': 1.0},
            {'label': 'Bacterial Blotch', 'value': 96.8},
            {'label': 'Dry Bubble', 'value': 0.8},
            {'label': 'Wet Bubble', 'value': 0.8},
            {'label': 'Cobweb', 'value': 0.6}
        ],
        'discoloration': 15.7,
        'time': '10:19',
        'servo_target': 'Kotak 3: BB Cacat',
        'status': 'Cacat'
    },
    {
        'id': '005',
        'image': 'https://images.unsplash.com/photo-1546069901-ba9599a7e63c?q=80&w=600&auto=format&fit=crop',
        'category': 'Tidak Layak Konsumsi',
        'category_badge': 'unfit',
        'ai_class': 'Dry Bubble',
        'confidence': 95.2,
        'probabilities': [
            {'label': 'Healthy', 'value': 1.8},
            {'label': 'Bacterial Blotch', 'value': 1.1},
            {'label': 'Dry Bubble', 'value': 95.2},
            {'label': 'Wet Bubble', 'value': 1.1},
            {'label': 'Cobweb', 'value': 0.8}
        ],
        'discoloration': 32.5,
        'time': '10:18',
        'servo_target': 'Kotak 4: Jamur Tidak Layak',
        'status': 'Reject'
    }
]

# Aggregated statistics data from system
STATS_DATA = {
    'total_jamur': 1248,
    'jamur_sehat': 892,
    'bb_total': 241,
    'bb_cacat': 150,
    'bb_tidak_cacat': 91,
    'tidak_layak': 115,
    'percentages': {
        'sehat': 71.5,
        'bb_cacat': 12.0,
        'bb_tidak_cacat': 7.3,
        'tidak_layak': 9.2
    }
}

@app.route('/')
def dashboard():
    return render_template('dashboard.html', active_page='dashboard', inspections=MOCK_INSPECTIONS)

@app.route('/proses')
def proses_grading():
    return render_template('proses_grading.html', active_page='proses', inspections=MOCK_INSPECTIONS)

@app.route('/monitoring')
def monitoring():
    return render_template('monitoring.html', active_page='monitoring')

@app.route('/riwayat')
def riwayat_grading():
    return render_template('riwayat.html', active_page='riwayat', inspections=MOCK_INSPECTIONS)

@app.route('/laporan')
def laporan():
    return render_template('laporan.html', active_page='laporan')

# API Endpoints
@app.route('/api/stats')
def api_stats():
    return jsonify(STATS_DATA)

@app.route('/api/device-status')
def api_device_status():
    return jsonify({
        'kamera': {'status': 'online', 'fps': 30, 'resolution': '1920x1080'},
        'esp32': {'status': 'online', 'ip': '192.168.1.45', 'ping_ms': 12},
        'sensor_ir': {'status': 'online', 'state': 'monitoring'},
        'servo': {'status': 'online', 'servos': [1, 2, 3], 'position': 'idle'},
        'resnet_api': {'status': 'online', 'model': 'ResNet-50', 'latency': '42ms'},
        'database': {'status': 'online', 'engine': 'SQLite', 'sync': 'Connected'}
    })

@app.route('/api/inspections')
def api_inspections():
    return jsonify(MOCK_INSPECTIONS)

if __name__ == '__main__':
    # Run development server
    app.run(host='127.0.0.1', port=5000, debug=True)
