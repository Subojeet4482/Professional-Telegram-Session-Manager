/* ═══════════════════════════════════════════════════════════════
   Dashboard-specific behaviour.

   Moved out of an inline <script> in dashboard.html so the CSP can
   forbid inline script execution. Server data arrives through a
   non-executable JSON island (<script type="application/json">),
   which CSP does not treat as script.
   ═══════════════════════════════════════════════════════════════ */

'use strict';

(function () {
    function readJsonIsland(id, fallback) {
        const node = document.getElementById(id);
        if (!node) return fallback;
        try {
            return JSON.parse(node.textContent) ?? fallback;
        } catch (e) {
            console.warn('Malformed data island:', id);
            return fallback;
        }
    }

    const STATUS_COLORS = {
        FREE: '#3DD598',
        SPAM: '#FFC542',
        NEW: '#5B8CFF',
        NEW_REGISTERED: '#5B8CFF',
        DEAD: '#FF5A5F',
        Unknown: '#94A3B8',
        UNKNOWN: '#94A3B8',
    };

    function renderChart(spamData) {
        const canvas = document.getElementById('spamChart');
        const skeleton = document.getElementById('chartSkeleton');
        const centerText = document.getElementById('chartCenterText');

        if (!canvas || Object.keys(spamData).length === 0) {
            if (skeleton) {
                skeleton.textContent = 'No chart data';
                skeleton.className = 'flex items-center justify-center h-full text-muted text-xs';
            }
            return;
        }

        if (typeof Chart === 'undefined') {
            console.warn('Chart.js failed to load');
            if (skeleton) {
                skeleton.textContent = 'Chart unavailable';
                skeleton.className = 'flex items-center justify-center h-full text-muted text-xs';
            }
            return;
        }

        const labels = Object.keys(spamData);
        const values = Object.values(spamData);

        new Chart(canvas, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: labels.map(l => STATUS_COLORS[l] || '#334155'),
                    borderWidth: 0,
                    hoverOffset: 4,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '75%',
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(17, 21, 28, 0.95)',
                        titleFont: { family: 'Inter', size: 12 },
                        bodyFont: { family: 'Inter', size: 13, weight: 'bold' },
                        padding: 10,
                        cornerRadius: 8,
                        displayColors: true,
                        borderColor: 'rgba(255,255,255,0.06)',
                        borderWidth: 1,
                    },
                },
                animation: {
                    onComplete: () => {
                        if (skeleton) skeleton.classList.add('hidden');
                        canvas.classList.remove('opacity-0');
                        if (centerText) centerText.classList.remove('opacity-0');
                    },
                },
            },
        });
    }

    function formatTimestamps() {
        document.querySelectorAll('.ts').forEach(node => {
            const ts = parseInt(node.dataset.ts, 10);
            if (!ts) return;
            node.textContent = new Date(ts * 1000).toLocaleString(undefined, {
                month: 'short', day: 'numeric',
                hour: '2-digit', minute: '2-digit', second: '2-digit',
            });
        });
    }

    function startAutoRefresh() {
        setInterval(() => {
            fetch('/api/sessions')
                .then(r => (r.ok ? r.json() : null))
                .then(data => {
                    if (!data) return;
                    let total = 0;
                    let countries = 0;
                    for (const key in data) {
                        countries++;
                        total += data[key].phones.length;
                    }
                    const totalEl = document.getElementById('totalSessions');
                    const countryEl = document.getElementById('totalCountries');
                    if (totalEl) totalEl.textContent = String(total);
                    if (countryEl) countryEl.textContent = String(countries);
                })
                .catch(() => { /* transient network error */ });
        }, 30000);
    }

    document.addEventListener('DOMContentLoaded', () => {
        renderChart(readJsonIsland('spam-data', {}));
        formatTimestamps();
        startAutoRefresh();
    });
})();
