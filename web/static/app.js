/* ═══════════════════════════════════════════════════════════════
   Session Manager Panel — Client-side JavaScript

   SECURITY: no dynamic value is ever concatenated into HTML.
   Everything server-supplied (phone numbers, OTP text, 2FA passwords,
   proxy hosts, error messages) is inserted with textContent or as a
   DOM node. Event handlers are attached with addEventListener via
   delegation on [data-action], never with inline on* attributes.

   This is what allows the CSP to drop 'unsafe-inline' from script-src.
   Reintroducing innerHTML with interpolated data here re-opens stored
   XSS: OTP bodies and 2FA passwords are attacker-influenced whenever
   sessions are imported from an untrusted source.
   ═══════════════════════════════════════════════════════════════ */

'use strict';

// ── Small DOM helpers ──────────────────────────────────────────

/** Create an element with class/text/attrs. Text is always set safely. */
function el(tag, opts = {}) {
    const node = document.createElement(tag);
    if (opts.className) node.className = opts.className;
    if (opts.text != null) node.textContent = String(opts.text);
    if (opts.attrs) {
        for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, String(v));
    }
    if (opts.style) node.setAttribute('style', opts.style);
    if (opts.children) opts.children.forEach(c => c && node.appendChild(c));
    return node;
}

/** Build an inline SVG icon from a path spec (static markup only). */
function svgIcon(paths, { size = 18, className = '', stroke = 'currentColor' } = {}) {
    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('width', size);
    svg.setAttribute('height', size);
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', stroke);
    svg.setAttribute('stroke-width', '2');
    if (className) svg.setAttribute('class', className);
    for (const [tag, attrs] of paths) {
        const child = document.createElementNS(NS, tag);
        for (const [k, v] of Object.entries(attrs)) child.setAttribute(k, v);
        svg.appendChild(child);
    }
    return svg;
}

function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
}

/** Replace a container's contents with a single centred message. */
function setMessage(container, text, className) {
    clear(container);
    container.appendChild(el('div', { className, text }));
}

const ICONS = {
    check:  [['polyline', { points: '20 6 9 17 4 12' }]],
    error:  [['circle', { cx: '12', cy: '12', r: '10' }],
             ['line', { x1: '15', y1: '9', x2: '9', y2: '15' }],
             ['line', { x1: '9', y1: '9', x2: '15', y2: '15' }]],
    info:   [['circle', { cx: '12', cy: '12', r: '10' }],
             ['line', { x1: '12', y1: '16', x2: '12', y2: '12' }],
             ['line', { x1: '12', y1: '8', x2: '12.01', y2: '8' }]],
    lock:   [['rect', { x: '3', y: '11', width: '18', height: '11', rx: '2', ry: '2' }],
             ['path', { d: 'M7 11V7a5 5 0 0 1 10 0v4' }]],
    close:  [['line', { x1: '18', y1: '6', x2: '6', y2: '18' }],
             ['line', { x1: '6', y1: '6', x2: '18', y2: '18' }]],
    search: [['circle', { cx: '11', cy: '11', r: '8' }],
             ['line', { x1: '21', y1: '21', x2: '16.65', y2: '16.65' }]],
    user:   [['path', { d: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2' }],
             ['circle', { cx: '12', cy: '7', r: '4' }]],
};

// ── Entry Animations ───────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.widget').forEach((widget, index) => {
        widget.classList.add('animate-fade-in');
        widget.style.animationDelay = `${(index % 8) * 60}ms`;
    });
});

// ── Navigation ─────────────────────────────────────────────────

function showSection(section) {
    const target = document.getElementById(section + 'Section');
    if (!target) return;

    target.scrollIntoView({ behavior: 'smooth', block: 'start' });

    const originalBorder = target.style.borderColor;
    const originalShadow = target.style.boxShadow;
    target.style.borderColor = 'var(--accent)';
    target.style.boxShadow = '0 0 16px var(--accent-dim)';
    setTimeout(() => {
        target.style.borderColor = originalBorder;
        target.style.boxShadow = originalShadow;
    }, 1200);
}

// ── Toast Notifications ────────────────────────────────────────

function showToast(message, type = 'success') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const iconSpec = type === 'success' ? ICONS.check : type === 'error' ? ICONS.error : ICONS.info;
    const iconClass = type === 'success' ? 'text-success' : type === 'error' ? 'text-danger' : 'text-accent';

    const toast = el('div', {
        className: 'toast',
        children: [
            svgIcon(iconSpec, { className: iconClass }),
            // textContent — a server-supplied error string can contain markup.
            el('span', { className: 'text-sm font-medium', text: message }),
        ],
    });

    container.appendChild(toast);
    setTimeout(() => toast.classList.add('show'), 10);
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ── API Helper ─────────────────────────────────────────────────

const CSRF_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

function readCookie(name) {
    const match = document.cookie.match('(?:^|; )' + name + '=([^;]*)');
    return match ? decodeURIComponent(match[1]) : '';
}

async function api(url, options = {}) {
    try {
        const method = (options.method || 'GET').toUpperCase();
        const headers = { 'Content-Type': 'application/json', ...options.headers };
        // Double-submit CSRF token: the cookie is readable only by JS on this
        // origin, so echoing it back as a header proves the request did not
        // originate from a cross-site page. See web/csrf.py.
        if (CSRF_METHODS.has(method)) {
            headers['X-CSRF-Token'] = readCookie('csrf_token');
        }
        const res = await fetch(url, {
            ...options,
            headers,
        });

        let data = {};
        try { data = await res.json(); } catch (_) { /* non-JSON body */ }

        if (!res.ok) {
            throw new Error(data.detail || data.error || `Request failed (${res.status})`);
        }
        return data;
    } catch (e) {
        if (e instanceof TypeError) {
            showToast('Network error. Please try again.', 'error');
        } else {
            showToast(e.message, 'error');
        }
        throw e;
    }
}

// ── Sessions ───────────────────────────────────────────────────

const SPAM_CLASSES = { FREE: 'success', SPAM: 'warning', BANNED: 'danger', Unknown: 'muted' };
const CONTACT_CLASSES = { NoLimit: 'success', Limited: 'warning', Unknown: 'muted' };
const DEAD_STATUSES = ['died', 'Die', 'BANNED', 'Dead'];

function buildSessionRow(phone) {
    const isDead = DEAD_STATUSES.includes(phone.account_status);

    const actions = el('div', {
        className: 'flex flex-wrap justify-end gap-2',
        style: 'min-width: 140px;',
    });

    const addBtn = (label, className, action) => {
        const btn = el('button', { className, text: label });
        // Closure over the phone value — nothing is interpolated into markup,
        // so a phone containing quotes or markup cannot break out.
        btn.addEventListener('click', () => sessionAction(action, phone.phone));
        actions.appendChild(btn);
    };

    if (!isDead) addBtn('OTP', 'btn btn-glass btn-sm', 'otp');
    if (phone.has_2fa) addBtn('2FA', 'btn btn-glass btn-sm', '2fa');
    if (!isDead) addBtn('Log Out', 'btn btn-danger btn-sm', 'logout');
    addBtn('Del', 'btn btn-danger btn-sm', 'delete');

    return el('tr', {
        children: [
            el('td', { children: [el('span', { className: 'code-pill', text: '+' + phone.phone })] }),
            el('td', { children: [el('span', {
                className: 'badge ' + (SPAM_CLASSES[phone.spam_status] || 'muted'),
                text: phone.spam_status,
            })] }),
            el('td', { children: [el('span', {
                className: 'badge ' + (CONTACT_CLASSES[phone.contact_status] || 'muted'),
                text: phone.contact_status,
            })] }),
            el('td', { className: 'text-right', children: [actions] }),
        ],
    });
}

async function viewCountrySessions(folder) {
    const overlay = document.getElementById('modalOverlay');
    const body = document.getElementById('modalBody');
    const title = document.getElementById('modalTitle');

    clear(title);
    title.appendChild(el('div', { className: 'status-dot' }));
    title.appendChild(el('span', { text: ' Fetching data...' }));

    clear(body);
    body.appendChild(el('div', {
        className: 'flex-col items-center justify-center p-8 text-muted',
        children: [
            el('div', { className: 'skeleton w-full h-8 mb-4' }),
            el('div', { className: 'skeleton w-full h-8 mb-4' }),
            el('div', { className: 'skeleton w-3/4 h-8' }),
        ],
    }));
    overlay.classList.add('active');

    try {
        const data = await api('/api/sessions');
        const country = data[folder];

        if (!country) {
            setMessage(body, 'Region not found in dataset', 'text-center p-8 text-danger font-medium');
            clear(title);
            title.appendChild(el('span', { text: 'Error' }));
            return;
        }

        clear(title);
        title.appendChild(el('span', { text: `${country.flag} ${country.name} ` }));
        title.appendChild(el('span', {
            className: 'badge muted ml-2',
            text: `${country.phones.length} active`,
        }));

        // Search box
        const searchWrap = el('div', { className: 'mb-2 relative' });
        const icon = svgIcon(ICONS.search, { size: 16 });
        icon.setAttribute('class', 'absolute left-3 top-1/2');
        icon.setAttribute('style', 'transform: translateY(-50%); color: var(--text-muted);');
        const input = el('input', {
            className: 'form-input',
            style: 'padding-left: 36px;',
            attrs: {
                type: 'text', id: 'sessionSearchInput',
                placeholder: 'Search identity, risk profile, or status...',
            },
        });
        input.addEventListener('keyup', filterSessions);
        searchWrap.appendChild(icon);
        searchWrap.appendChild(input);

        // Table
        const headRow = el('tr');
        ['Identity (Phone)', 'Risk Profile', 'Access Level'].forEach(h =>
            headRow.appendChild(el('th', { text: h })));
        headRow.appendChild(el('th', { className: 'text-right', text: 'Action' }));

        const tbody = el('tbody');
        country.phones.forEach(p => tbody.appendChild(buildSessionRow(p)));

        const table = el('table', {
            className: 'table',
            attrs: { id: 'sessionsTable' },
            children: [el('thead', { children: [headRow] }), tbody],
        });

        clear(body);
        body.appendChild(searchWrap);
        body.appendChild(el('div', { className: 'table-container mt-4', children: [table] }));

        setTimeout(() => input.focus(), 100);
    } catch (e) {
        setMessage(body, 'Failed to load sessions', 'text-center p-8 text-danger font-medium');
    }
}

function filterSessions() {
    const input = document.getElementById('sessionSearchInput');
    const table = document.getElementById('sessionsTable');
    if (!input || !table) return;

    const filter = input.value.toUpperCase();
    const rows = table.getElementsByTagName('tr');
    for (let i = 1; i < rows.length; i++) {
        const text = rows[i].textContent || '';
        rows[i].style.display = text.toUpperCase().indexOf(filter) > -1 ? '' : 'none';
    }
}

async function sessionAction(action, phone) {
    if (action === 'logout') {
        if (!confirm(`Terminate session for +${phone}?`)) return;
        try {
            await api(`/api/sessions/${encodeURIComponent(phone)}/logout`, { method: 'POST' });
            showToast(`Session +${phone} terminated successfully`);
            closeModal();
            setTimeout(() => location.reload(), 1000);
        } catch (e) { /* handled by api() */ }
    } else if (action === 'delete') {
        if (!confirm(`Permanently erase +${phone}?`)) return;
        try {
            await api(`/api/sessions/${encodeURIComponent(phone)}`, { method: 'DELETE' });
            showToast(`Record +${phone} erased`);
            closeModal();
            setTimeout(() => location.reload(), 1000);
        } catch (e) { /* handled by api() */ }
    } else if (action === 'otp') {
        showToast('Fetching OTP...', 'info');
        try {
            const data = await api(`/api/sessions/${encodeURIComponent(phone)}/otp`);
            if (data.found) {
                // Tag-stripping here is COSMETIC only (the message is formatted
                // for Telegram). Safety comes from textContent in showSecretModal.
                showSecretModal(phone, String(data.message).replace(/<[^>]*>/g, ''), 'OTP Code');
            } else {
                showToast(data.message || 'No verification code found', 'error');
            }
        } catch (e) { /* handled by api() */ }
    } else if (action === '2fa') {
        showToast('Fetching 2FA password...', 'info');
        try {
            const data = await api(`/api/sessions/${encodeURIComponent(phone)}/2fa`);
            if (data.found) {
                showSecretModal(phone, data.password, '2FA Password');
            } else {
                showToast(data.message || 'No 2FA password found', 'error');
            }
        } catch (e) { /* handled by api() */ }
    }
}

/**
 * Show a secret (OTP code or 2FA password) in a modal.
 * `text` is rendered with textContent and the copy handler closes over the
 * raw value — it is never interpolated into markup or into an onclick string.
 */
function showSecretModal(phone, text, title = 'Secret') {
    const existing = document.getElementById('otpModalOverlay');
    if (existing) existing.remove();

    const value = text == null ? '' : String(text);

    const closeBtn = el('button', {
        className: 'modal-close btn-icon bg-glass border hover:bg-hover text-muted rounded-sm',
        children: [svgIcon(ICONS.close, { size: 20 })],
    });

    const secretBox = el('div', {
        className: 'p-4 bg-glass border rounded-md font-mono text-primary text-base',
        style: 'user-select: all; white-space: pre-wrap; word-break: break-all;',
        text: value,
    });

    const copyBtn = el('button', {
        className: 'btn btn-primary btn-md w-full mt-2',
        text: 'Copy & Close',
    });

    const content = el('div', {
        className: 'modal-content max-w-md',
        children: [
            el('div', {
                className: 'modal-header p-5 border-b flex-between',
                children: [
                    el('div', {
                        className: 'modal-title font-semibold text-lg flex items-center gap-2',
                        children: [
                            svgIcon(ICONS.lock, { size: 20, stroke: 'var(--accent)' }),
                            el('span', { text: title }),
                        ],
                    }),
                    closeBtn,
                ],
            }),
            el('div', {
                className: 'modal-body p-6 flex-col gap-4',
                children: [
                    el('div', {
                        className: 'text-sm text-secondary',
                        children: [
                            el('span', { text: 'Account: ' }),
                            el('span', { className: 'code-pill', text: '+' + phone }),
                        ],
                    }),
                    secretBox,
                    copyBtn,
                ],
            }),
        ],
    });

    const overlay = el('div', { className: 'modal-overlay active', attrs: { id: 'otpModalOverlay' } });
    overlay.style.zIndex = '2050';
    overlay.appendChild(content);

    overlay.addEventListener('click', () => overlay.remove());
    content.addEventListener('click', ev => ev.stopPropagation());
    closeBtn.addEventListener('click', () => overlay.remove());
    copyBtn.addEventListener('click', () => {
        navigator.clipboard.writeText(value)
            .then(() => showToast('Copied to clipboard'))
            .catch(() => showToast('Could not copy to clipboard', 'error'));
        overlay.remove();
    });

    document.body.appendChild(overlay);
}

async function exportAllSessions() {
    showToast('Preparing export archive...', 'info');
    try {
        const res = await fetch('/api/sessions/export/all');
        if (!res.ok) {
            let detail = 'Export failed';
            try {
                const data = await res.json();
                detail = data.detail || data.error || detail;
            } catch (_) { /* ignore */ }
            throw new Error(detail);
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = el('a', { attrs: { href: url, download: 'sessions_export.zip' } });
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        showToast('Export downloaded successfully!');
    } catch (e) {
        showToast(e.message || 'Export failed', 'error');
    }
}

// ── Admins ─────────────────────────────────────────────────────

function showAddAdmin() {
    document.getElementById('addAdminModal').classList.add('active');
}

function closeAddAdmin() {
    document.getElementById('addAdminModal').classList.remove('active');
}

async function addAdmin() {
    const userId = document.getElementById('adminUserId').value;
    const role = document.getElementById('adminRole').value;

    if (!userId) {
        showToast('A Telegram user ID is required', 'error');
        return;
    }

    try {
        await api('/api/admins', {
            method: 'POST',
            body: JSON.stringify({ user_id: parseInt(userId, 10), role }),
        });
        showToast(`User ${userId} added`);
        closeAddAdmin();
        setTimeout(() => location.reload(), 800);
    } catch (e) { /* handled by api() */ }
}

async function removeAdmin(userId) {
    if (!confirm(`Revoke access for ${userId}?`)) return;
    try {
        await api(`/api/admins/${encodeURIComponent(userId)}`, { method: 'DELETE' });
        showToast(`Access revoked for ${userId}`);
        setTimeout(() => location.reload(), 800);
    } catch (e) { /* handled by api() */ }
}

// ── Settings ───────────────────────────────────────────────────

function settingsCard(titleText, iconSpec, rows) {
    const header = el('div', {
        className: 'flex-between',
        children: [
            el('span', { className: 'font-semibold text-sm', text: titleText }),
            iconSpec ? svgIcon(iconSpec, { size: 16, stroke: 'var(--accent)' }) : null,
        ],
    });
    return el('div', {
        className: 'bg-glass border rounded-md p-4 flex-1 flex-col gap-3 min-w-[280px]',
        children: [header, ...rows],
    });
}

function labelledPill(label, value) {
    return el('span', {
        children: [
            el('span', { text: label + ': ' }),
            el('span', { className: 'code-pill', text: value }),
        ],
    });
}

function badgeRow(label, on) {
    return el('div', {
        className: 'flex-between',
        children: [
            el('span', { text: label + ': ' }),
            el('span', { className: 'badge ' + (on ? 'success' : 'muted'), text: on ? 'ON' : 'OFF' }),
        ],
    });
}

async function refreshSettings() {
    const container = document.getElementById('settingsContent');
    setMessage(container, 'Loading configuration...',
        'p-4 border-t text-center w-full text-muted text-sm border-dashed rounded-sm');

    try {
        const data = await api('/api/settings');
        clear(container);

        // Proxy — host is operator-supplied, so textContent matters here.
        container.appendChild(settingsCard('Proxy Gateway', null, [
            el('div', {
                className: 'flex-col gap-1 text-xs text-secondary',
                children: [
                    el('span', {
                        children: [
                            el('span', { text: 'Status: ' }),
                            el('span', {
                                className: 'badge ' + (data.proxy.enabled ? 'success' : 'muted'),
                                text: data.proxy.enabled ? 'Active' : 'Offline',
                            }),
                        ],
                    }),
                    labelledPill('Type', data.proxy.type),
                    labelledPill('Host', `${data.proxy.host || 'null'}:${data.proxy.port || 'null'}`),
                ],
            }),
        ]));

        // API credentials (already masked server-side)
        container.appendChild(settingsCard('API Credentials', ICONS.lock, [
            el('div', {
                className: 'flex-col gap-1 text-xs text-secondary',
                children: [
                    labelledPill('Platform', data.api.platform_label || data.api.platform || 'desktop'),
                    labelledPill('ID', data.api.api_id),
                    labelledPill('Hash', data.api.api_hash),
                    // Example fingerprint — each account derives its own from its number.
                    labelledPill('Device', data.api.device_sample || '—'),
                ],
            }),
        ]));

        // Profile automation
        const p = data.profile;
        container.appendChild(settingsCard('Automated Profiling', ICONS.user, [
            el('div', {
                className: 'grid grid-cols-2 gap-2 text-xs text-secondary',
                children: [
                    badgeRow('Username', p.auto_username),
                    badgeRow('Name', p.auto_name),
                    badgeRow('Photo', p.auto_photo),
                    badgeRow('Bio', p.auto_bio),
                ],
            }),
        ]));
    } catch (e) {
        setMessage(container, 'Could not load configuration',
            'p-4 border border-danger text-center w-full text-danger text-sm border-dashed rounded-sm');
    }
}

// ── Scheduler ──────────────────────────────────────────────────

const SCHEDULER_CHECKBOXES = {
    auto_check: 'toggleAutoCheck',
    daily_report: 'toggleDailyReport',
    auto_backup: 'toggleAutoBackup',
};

async function toggleScheduler(feature) {
    try {
        const data = await api(`/api/scheduler/toggle/${encodeURIComponent(feature)}`, { method: 'POST' });
        showToast(`${feature.replace(/_/g, ' ')} ${data.enabled ? 'enabled' : 'disabled'}`);
    } catch (e) {
        // Revert the switch — the server rejected the change (e.g. 403).
        const cb = document.getElementById(SCHEDULER_CHECKBOXES[feature]);
        if (cb) cb.checked = !cb.checked;
    }
}

// ── Modals ─────────────────────────────────────────────────────

function closeModal() {
    const overlay = document.getElementById('modalOverlay');
    if (overlay) overlay.classList.remove('active');
}

// ── Event delegation (replaces every inline on* attribute) ─────

const CLICK_ACTIONS = {
    'scroll-section':  (t) => showSection(t.dataset.target),
    'export-all':      () => exportAllSessions(),
    'view-country':    (t) => viewCountrySessions(t.dataset.folder),
    'refresh-settings':() => refreshSettings(),
    'add-admin-open':  () => showAddAdmin(),
    'add-admin-close': () => closeAddAdmin(),
    'add-admin-submit':() => addAdmin(),
    'remove-admin':    (t) => removeAdmin(t.dataset.userId),
    'close-modal':     () => closeModal(),
};

document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action]');
    if (trigger) {
        const handler = CLICK_ACTIONS[trigger.dataset.action];
        if (handler) {
            event.preventDefault();
            handler(trigger);
            return;
        }
    }
    // Clicking a modal backdrop closes it; clicks inside the panel do not.
    const backdrop = event.target.closest('[data-dismiss-on-backdrop]');
    if (backdrop && event.target === backdrop) {
        backdrop.classList.remove('active');
    }
});

document.addEventListener('change', (event) => {
    const trigger = event.target.closest('[data-action="toggle-scheduler"]');
    if (trigger) toggleScheduler(trigger.dataset.feature);
});

// Global hotkeys
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
        closeModal();
        closeAddAdmin();
        const secret = document.getElementById('otpModalOverlay');
        if (secret) secret.remove();
    }
});
