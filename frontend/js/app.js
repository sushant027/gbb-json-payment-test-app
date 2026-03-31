/**
 * App.js — Client-side routing and shared utilities.
 */
const App = {
    currentPage: 'upload',

    init() {
        console.log('[App] Initializing...');
        // Setup tab navigation
        document.querySelectorAll('.nav-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                App.navigate(tab.dataset.page);
            });
        });

        // Load initial data
        App.loadSchemes();
        App.loadTestRuns();
    },

    navigate(page) {
        console.log('[App] Navigating to:', page);
        App.currentPage = page;

        // Update tabs
        document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
        document.querySelector(`.nav-tab[data-page="${page}"]`).classList.add('active');

        // Update pages
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        document.getElementById(`page-${page}`).classList.add('active');

        // Trigger page-specific load
        switch (page) {
            case 'upload':
                App.loadTestRuns();
                break;
            case 'json-gen':
                JsonGen.loadTestRuns();
                break;
            case 'processing':
                Processing.loadTestRuns();
                break;
            case 'results':
                Results.loadTestRuns();
                break;
            case 'mapping':
                Mapping.loadSchemes();
                break;
        }
    },

    async loadSchemes() {
        try {
            const res = await fetch('/api/schemes');
            const schemes = await res.json();
            App.schemes = schemes;

            // Populate all scheme selectors
            const selects = ['uploadSchemeSelect', 'mappingSchemeSelect'];
            selects.forEach(id => {
                const sel = document.getElementById(id);
                if (!sel) return;
                const current = sel.value;
                sel.innerHTML = '<option value="">-- Select a scheme --</option>';
                schemes.forEach(s => {
                    sel.innerHTML += `<option value="${s.id}">${s.scheme_name}</option>`;
                });
                if (current) sel.value = current;
            });
            console.log('[App] Loaded', schemes.length, 'schemes');
        } catch (err) {
            console.error('[App] Failed to load schemes:', err);
        }
    },

    async loadTestRuns() {
        try {
            const res = await fetch('/api/test-runs');
            const runs = await res.json();
            App.testRuns = runs;

            // Populate test runs table on upload page
            const tbody = document.getElementById('testRunsBody');
            if (tbody) {
                tbody.innerHTML = runs.map(r => `
                    <tr>
                        <td>${r.id}</td>
                        <td>${r.scheme_name || ''}</td>
                        <td>${r.upload_filename}</td>
                        <td>${r.total_transactions}</td>
                        <td><span class="badge badge-info">${r.status}</span></td>
                        <td>${App.formatDate(r.created_at)}</td>
                    </tr>
                `).join('') || '<tr><td colspan="6" style="text-align:center">No test runs yet</td></tr>';
            }

            console.log('[App] Loaded', runs.length, 'test runs');
        } catch (err) {
            console.error('[App] Failed to load test runs:', err);
        }
    },

    populateRunSelect(selectId, filterStatus) {
        const sel = document.getElementById(selectId);
        if (!sel) return;
        const current = sel.value;
        sel.innerHTML = '<option value="">-- Select a test run --</option>';
        const runs = App.testRuns || [];
        runs.forEach(r => {
            const label = `Run #${r.id} — ${r.scheme_name || 'Unknown'} — ${r.upload_filename} (${r.status})`;
            sel.innerHTML += `<option value="${r.id}">${label}</option>`;
        });
        if (current) sel.value = current;
    },

    formatDate(dt) {
        if (!dt) return '';
        return new Date(dt).toLocaleString();
    },

    showAlert(containerId, message, type = 'info') {
        const el = document.getElementById(containerId);
        if (el) {
            el.innerHTML = `<div class="alert alert-${type}">${message}</div>`;
        }
    },

    statusBadge(status) {
        if (!status) return '<span class="badge badge-pending">N/A</span>';
        const s = status.toUpperCase();
        if (s === 'PASS' || s === 'SUCCESS') return `<span class="badge badge-pass">${status}</span>`;
        if (s === 'FAIL' || s === 'FAILURE') return `<span class="badge badge-fail">${status}</span>`;
        return `<span class="badge badge-pending">${status}</span>`;
    }
};

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', App.init);
