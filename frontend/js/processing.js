/**
 * Processing page — GBB submission, initiation/response file matching.
 */
const Processing = {
    currentRunId: null,

    loadTestRuns() {
        App.populateRunSelect('procRunSelect');
    },

    async loadRunDetails() {
        const runId = document.getElementById('procRunSelect').value;
        if (!runId) {
            document.getElementById('procRunInfo').classList.add('hidden');
            document.getElementById('procTxnBody').innerHTML = '';
            Processing._disableAll();
            return;
        }

        Processing.currentRunId = runId;
        console.log('[Processing] Loading run:', runId);

        try {
            const res = await fetch(`/api/test-runs/${runId}`);
            const data = await res.json();

            document.getElementById('procRunInfo').classList.remove('hidden');
            document.getElementById('procRunInfo').innerHTML = `
                <div class="alert alert-info">
                    Run #${data.id} — Status: <strong>${data.status}</strong> — Transactions: ${data.total_transactions}
                </div>
            `;

            // Enable buttons based on status
            Processing._updateButtons(data.status);

            // Show transaction status
            const txns = data.transactions || [];
            document.getElementById('procTxnBody').innerHTML = txns.map(t => `
                <tr>
                    <td>${t.tc_id || ''}</td>
                    <td>${t.batch_reference || ''}</td>
                    <td>${t.actual_debit_status || ''}</td>
                    <td>${t.initiation_xml_path ? 'Matched' : '<span class="text-muted">--</span>'}</td>
                    <td>${t.response_xml_path ? 'Matched' : '<span class="text-muted">--</span>'}</td>
                    <td>${App.statusBadge(t.status)}</td>
                </tr>
            `).join('');

            // Update workflow steps
            Processing._updateSteps(data.status);

        } catch (err) {
            console.error('[Processing] Error:', err);
        }
    },

    async submitGbb() {
        if (!Processing.currentRunId) return;

        const btn = document.getElementById('submitGbbBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="loading"></span> Submitting...';

        // Get optional GBB URL override
        const gbbUrl = document.getElementById('gbbUrlInput').value.trim();
        const body = gbbUrl ? JSON.stringify({ gbb_url: gbbUrl }) : '{}';

        try {
            const res = await fetch(`/api/processing/test-runs/${Processing.currentRunId}/submit-gbb`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: body
            });
            const data = await res.json();

            if (res.ok) {
                App.showAlert('procResult', data.message, 'success');
            } else {
                App.showAlert('procResult', data.error || 'Submit failed', 'danger');
            }
            Processing.loadRunDetails();
        } catch (err) {
            App.showAlert('procResult', 'Network error: ' + err.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.innerHTML = 'Submit JSON to GBB';
        }
    },

    async processInitiation() {
        if (!Processing.currentRunId) return;
        await Processing._callApi('process-initiation', 'procInitBtn', 'Search Initiation Files');
    },

    async processResponse() {
        if (!Processing.currentRunId) return;
        await Processing._callApi('process-response', 'procRespBtn', 'Search Response Files');
    },

    async validate() {
        if (!Processing.currentRunId) return;
        const btn = document.getElementById('validateBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="loading"></span> Validating...';

        try {
            const res = await fetch(`/api/results/test-runs/${Processing.currentRunId}/validate`, {
                method: 'POST'
            });
            const data = await res.json();

            if (res.ok) {
                App.showAlert('procResult', data.message, 'success');
            } else {
                App.showAlert('procResult', data.error || 'Validation failed', 'danger');
            }
            Processing.loadRunDetails();
        } catch (err) {
            App.showAlert('procResult', 'Network error: ' + err.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.innerHTML = 'Run Validation';
        }
    },

    async _callApi(action, btnId, btnLabel) {
        const btn = document.getElementById(btnId);
        btn.disabled = true;
        btn.innerHTML = `<span class="loading"></span> Processing...`;

        console.log('[Processing]', action, 'for run:', Processing.currentRunId);

        try {
            const res = await fetch(`/api/processing/test-runs/${Processing.currentRunId}/${action}`, {
                method: 'POST'
            });
            const data = await res.json();

            if (res.ok) {
                App.showAlert('procResult', data.message, 'success');
            } else {
                App.showAlert('procResult', data.error || `${action} failed`, 'danger');
            }
            Processing.loadRunDetails();
        } catch (err) {
            App.showAlert('procResult', 'Network error: ' + err.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.innerHTML = btnLabel;
        }
    },

    _updateButtons(status) {
        const s = status || '';
        document.getElementById('submitGbbBtn').disabled = !['json_generated'].includes(s);
        document.getElementById('procInitBtn').disabled = !['initiated', 'submitted', 'initiation_processed', 'response_processed'].includes(s);
        document.getElementById('procRespBtn').disabled = !['initiated', 'submitted', 'initiation_processed', 'response_processed'].includes(s);
        document.getElementById('validateBtn').disabled = !['initiation_processed', 'response_processed'].includes(s);
    },

    _updateSteps(status) {
        const steps = {
            'step-submit': ['initiated', 'submitted', 'initiation_processed', 'response_processed', 'validated', 'completed'],
            'step-initiation': ['initiation_processed', 'response_processed', 'validated', 'completed'],
            'step-response': ['response_processed', 'validated', 'completed'],
            'step-validate': ['validated', 'completed']
        };

        Object.entries(steps).forEach(([id, doneStatuses]) => {
            const el = document.getElementById(id);
            el.classList.remove('active', 'done');
            if (doneStatuses.includes(status)) {
                el.classList.add('done');
            }
        });
    },

    _disableAll() {
        ['submitGbbBtn', 'procInitBtn', 'procRespBtn', 'validateBtn'].forEach(id => {
            document.getElementById(id).disabled = true;
        });
    }
};
