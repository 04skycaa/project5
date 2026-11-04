/**
 * Mushroom Grading System - Dashboard Interactive Logic
 * iOS Glassmorphism Interface
 */

document.addEventListener('DOMContentLoaded', () => {
  initLiveClock();
  initTrendChart();
  initDonutChart();
  initModalHandling();
  initLiveSimulatedFeed();
});

/* 1. Real-time Live Clock */
function initLiveClock() {
  const dateElement = document.getElementById('liveDateBadge');
  if (!dateElement) return;

  const days = ['Min', 'Sen', 'Sel', 'Rab', 'Kam', 'Jum', 'Sab'];
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];

  function update() {
    const now = new Date();
    const dayName = days[now.getDay()];
    const date = now.getDate();
    const month = months[now.getMonth()];
    const year = now.getFullYear();
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');

    dateElement.textContent = `${dayName}, ${date} ${month} ${year} • ${hours}:${minutes} WIB`;
  }

  update();
  setInterval(update, 1000);
}

/* 2. Tren Hasil Grading Area Chart (Chart.js) */
let trendChartInstance = null;
function initTrendChart() {
  const ctx = document.getElementById('trendChart');
  if (!ctx) return;

  const labels = ['21 Okt', '22 Okt', '23 Okt', '24 Okt', '25 Okt', '26 Okt', '27 Okt'];

  // Create smooth Apple-like gradients
  const chartCtx = ctx.getContext('2d');
  
  const greenGradient = chartCtx.createLinearGradient(0, 0, 0, 220);
  greenGradient.addColorStop(0, 'rgba(16, 185, 129, 0.45)');
  greenGradient.addColorStop(1, 'rgba(16, 185, 129, 0.02)');

  const blueGradient = chartCtx.createLinearGradient(0, 0, 0, 220);
  blueGradient.addColorStop(0, 'rgba(0, 122, 255, 0.35)');
  blueGradient.addColorStop(1, 'rgba(0, 122, 255, 0.02)');

  const purpleGradient = chartCtx.createLinearGradient(0, 0, 0, 220);
  purpleGradient.addColorStop(0, 'rgba(139, 92, 246, 0.35)');
  purpleGradient.addColorStop(1, 'rgba(139, 92, 246, 0.02)');

  const redGradient = chartCtx.createLinearGradient(0, 0, 0, 220);
  redGradient.addColorStop(0, 'rgba(255, 59, 48, 0.30)');
  redGradient.addColorStop(1, 'rgba(255, 59, 48, 0.02)');

  trendChartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Sehat',
          data: [120, 135, 140, 165, 158, 205, 220],
          borderColor: '#34C759',
          backgroundColor: greenGradient,
          fill: true,
          tension: 0.42,
          borderWidth: 2.5,
          pointBackgroundColor: '#FFFFFF',
          pointBorderColor: '#34C759',
          pointBorderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 6,
        },
        {
          label: 'BB (Tidak Cacat)',
          data: [70, 95, 88, 92, 105, 112, 120],
          borderColor: '#007AFF',
          backgroundColor: blueGradient,
          fill: true,
          tension: 0.42,
          borderWidth: 2.5,
          pointBackgroundColor: '#FFFFFF',
          pointBorderColor: '#007AFF',
          pointBorderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 6,
        },
        {
          label: 'BB (Cacat)',
          data: [45, 60, 52, 65, 60, 70, 75],
          borderColor: '#8B5CF6',
          backgroundColor: purpleGradient,
          fill: true,
          tension: 0.42,
          borderWidth: 2.5,
          pointBackgroundColor: '#FFFFFF',
          pointBorderColor: '#8B5CF6',
          pointBorderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 6,
        },
        {
          label: 'Tidak Layak',
          data: [20, 28, 24, 32, 29, 36, 40],
          borderColor: '#FF3B30',
          backgroundColor: redGradient,
          fill: true,
          tension: 0.42,
          borderWidth: 2.5,
          pointBackgroundColor: '#FFFFFF',
          pointBorderColor: '#FF3B30',
          pointBorderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 6,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          position: 'bottom',
          labels: {
            usePointStyle: true,
            boxWidth: 6,
            boxHeight: 6,
            font: {
              family: '-apple-system, BlinkMacSystemFont, "Plus Jakarta Sans", sans-serif',
              size: 9.5,
              weight: '600'
            },
            color: '#64748B',
            padding: 8
          }
        },
        tooltip: {
          backgroundColor: 'rgba(255, 255, 255, 0.92)',
          titleColor: '#0F172A',
          bodyColor: '#334155',
          borderColor: 'rgba(255, 255, 255, 0.9)',
          borderWidth: 1,
          cornerRadius: 12,
          boxPadding: 6,
          usePointStyle: true,
          bodyFont: {
            weight: '600'
          },
          padding: 12,
          shadowOffsetX: 0,
          shadowOffsetY: 8,
          shadowBlur: 16,
          shadowColor: 'rgba(0, 0, 0, 0.1)'
        }
      },
      scales: {
        x: {
          grid: {
            display: false,
          },
          ticks: {
            color: '#94A3B8',
            font: {
              size: 11,
              weight: '500'
            }
          }
        },
        y: {
          border: {
            dash: [4, 4],
          },
          grid: {
            color: 'rgba(226, 232, 240, 0.6)',
          },
          ticks: {
            color: '#94A3B8',
            font: {
              size: 11,
              weight: '500'
            },
            stepSize: 50
          }
        }
      }
    }
  });
}

/* 3. Distribusi Kategori Donut Chart */
let donutChartInstance = null;
function initDonutChart() {
  const ctx = document.getElementById('donutChart');
  if (!ctx) return;

  const centerLabel = document.querySelector('.donut-center-label');

  donutChartInstance = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Sehat', 'BB (Cacat)', 'BB (Tidak Cacat)', 'Tidak Layak'],
      datasets: [{
        data: [892, 150, 91, 115],
        backgroundColor: [
          '#34C759', // Sehat (Green)
          '#8B5CF6', // BB Cacat (Purple)
          '#007AFF', // BB Tidak Cacat (Blue)
          '#FF3B30'  // Tidak Layak (Red)
        ],
        borderWidth: 3,
        borderColor: '#FFFFFF',
        hoverOffset: 6
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '76%',
      onHover: (event, activeElements) => {
        if (!centerLabel) return;
        if (activeElements && activeElements.length > 0) {
          centerLabel.classList.add('is-hidden');
        } else {
          centerLabel.classList.remove('is-hidden');
        }
      },
      onClick: (event, activeElements) => {
        if (!centerLabel) return;
        if (activeElements && activeElements.length > 0) {
          centerLabel.classList.add('is-hidden');
        }
      },
      plugins: {
        legend: {
          display: false // We use custom HTML legend matching the design
        },
        tooltip: {
          backgroundColor: 'rgba(255, 255, 255, 0.98)',
          titleColor: '#0F172A',
          bodyColor: '#334155',
          borderColor: 'rgba(226, 232, 240, 0.95)',
          borderWidth: 1.5,
          cornerRadius: 12,
          padding: 10,
          boxPadding: 4,
          usePointStyle: true,
          callbacks: {
            label: function(context) {
              const value = context.parsed || 0;
              const total = 1248;
              const pct = ((value / total) * 100).toFixed(1);
              return ` ${value} (${pct}%)`;
            }
          }
        }
      }
    }
  });

  // Restore center label smoothly when pointer leaves the chart area
  ctx.addEventListener('mouseleave', () => {
    if (centerLabel) {
      centerLabel.classList.remove('is-hidden');
    }
  });
}

/* 4. Inspection Detail Modal Handling */
const inspectionData = {
  '001': {
    id: '001',
    img: 'https://images.unsplash.com/photo-1504280390367-361c6d9f38f4?q=80&w=600&auto=format&fit=crop',
    aiClass: 'Healthy (Confidence: 98.4%)',
    discoloration: '0.8%',
    category: 'Jamur Sehat',
    categoryClass: 'healthy',
    servoTarget: 'Kotak 1: Jamur Sehat',
    time: '10:23 WIB - 27 Okt 2025',
    notes: 'Kondisi payung dan tudung jamur putih bersih, tanpa bercak patogen.'
  },
  '002': {
    id: '002',
    img: 'https://images.unsplash.com/photo-1546069901-ba9599a7e63c?q=80&w=600&auto=format&fit=crop',
    aiClass: 'Bacterial Blotch (Confidence: 94.1%)',
    discoloration: '6.4%',
    category: 'BB - Tidak Cacat',
    categoryClass: 'bb-safe',
    servoTarget: 'Kotak 2: BB Tidak Cacat',
    time: '10:22 WIB - 27 Okt 2025',
    notes: 'Bercak cokelat ringan seluas 6.4%, masih di bawah ambang batas cacat (10%). Aman untuk konsumsi olahan.'
  },
  '003': {
    id: '003',
    img: 'https://images.unsplash.com/photo-1504280390367-361c6d9f38f4?q=80&w=600&auto=format&fit=crop',
    aiClass: 'Healthy (Confidence: 97.9%)',
    discoloration: '1.2%',
    category: 'Jamur Sehat',
    categoryClass: 'healthy',
    servoTarget: 'Kotak 1: Jamur Sehat',
    time: '10:21 WIB - 27 Okt 2025',
    notes: 'Permukaan jamur mulus, tekstur kokoh, kualitas Grade A.'
  },
  '004': {
    id: '004',
    img: 'https://images.unsplash.com/photo-1546069901-ba9599a7e63c?q=80&w=600&auto=format&fit=crop',
    aiClass: 'Bacterial Blotch (Confidence: 96.8%)',
    discoloration: '15.7%',
    category: 'BB - Cacat',
    categoryClass: 'bb-defect',
    servoTarget: 'Kotak 3: BB Cacat',
    time: '10:19 WIB - 27 Okt 2025',
    notes: 'Diskolorasi mencapai 15.7% (melebihi ambang batas toleransi 10%). Masuk kategori sortiran cacat.'
  },
  '005': {
    id: '005',
    img: 'https://images.unsplash.com/photo-1546069901-ba9599a7e63c?q=80&w=600&auto=format&fit=crop',
    aiClass: 'Dry Bubble (Verticillium fungicola) (Confidence: 95.2%)',
    discoloration: '32.5%',
    category: 'Tidak Layak Konsumsi',
    categoryClass: 'unfit',
    servoTarget: 'Kotak 4: Jamur Tidak Layak',
    time: '10:18 WIB - 27 Okt 2025',
    notes: 'Terdeteksi penyakit Dry Bubble dengan deformasi struktur jamur. Ditolak untuk konsumsi.'
  }
};

function initModalHandling() {
  const modalOverlay = document.getElementById('detailModal');
  const closeBtn = document.getElementById('closeModalBtn');
  const detailButtons = document.querySelectorAll('.btn-detail');

  detailButtons.forEach(btn => {
    btn.addEventListener('click', (e) => {
      const row = e.target.closest('tr');
      const id = row.getAttribute('data-id');
      const data = inspectionData[id];
      if (data) {
        document.getElementById('modalId').textContent = `Mushroom #${data.id}`;
        document.getElementById('modalImage').src = data.img;
        document.getElementById('modalAiClass').textContent = data.aiClass;
        document.getElementById('modalDiscoloration').textContent = data.discoloration;
        document.getElementById('modalTarget').textContent = data.servoTarget;
        document.getElementById('modalTime').textContent = data.time;
        document.getElementById('modalNotes').textContent = data.notes;

        const catBadge = document.getElementById('modalCategory');
        catBadge.textContent = data.category;
        catBadge.className = `badge-grade ${data.categoryClass}`;

        modalOverlay.classList.add('active');
      }
    });
  });

  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      modalOverlay.classList.remove('active');
    });
  }

  modalOverlay?.addEventListener('click', (e) => {
    if (e.target === modalOverlay) {
      modalOverlay.classList.remove('active');
    }
  });
}

/* 5. Simulated Live Conveyor Camera Feed Pulse */
function initLiveSimulatedFeed() {
  const statusText = document.querySelector('.detection-status-text span');
  if (!statusText) return;

  const states = [
    'Deteksi berjalan...',
    'Objek terdeteksi: Jamur Kancing',
    'Menghitung % diskolorasi...',
    'Keputusan: Jamur Sehat (Servo 1 Aktif)'
  ];
  let stateIndex = 0;

  setInterval(() => {
    stateIndex = (stateIndex + 1) % states.length;
    if (statusText) {
      statusText.textContent = states[stateIndex];
    }
  }, 4500);
}
