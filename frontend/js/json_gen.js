/**
 * JSON Generation page — generate and download request JSONs.
 */
const JsonGen = {
    currentRunId: null,

    loadTestRuns() {
        App.populateRunSelect('jsonGenRunSelect');
    },

    async loadRunDetails() {
        const runId = document.getElementById('jsonGenRunSelect').value;
        if (!runId) {
            document.getElementById('jsonGenRunInfo').classList.add('hidden');
            document.getElementById('jsonGenTxnBody').innerHTML = '';
            document.getElementById('generateBtn').disabled = true;
            document.getElementById('downloadJsonBtn').disabled = true;
            return;
        }

        JsonGen.currentRunId = runId;
        console.log('[JsonGen] Loading run details:', runId);

        try {
            const res = await fetch(`/api/test-runs/${runId}`);
            const data = await res.json();

            document.getElementById('jsonGenRunInfo').classList.remove('hidden');
            document.getElementById('jsonGenRunInfo').innerHTML = `
                <div class="alert alert-info">
                    Run #${data.id} — Scheme: ${data.scheme_id} — Status: ${data.status} — Transactions: ${data.total_transactions}
                </div>
            `;

            // Enable buttons based on status
            document.getElementById('generateBtn').disabled = false;
            document.getElementById('downloadJsonBtn').disabled = (data.status === 'pending');

            // Show transactions
            const txns = data.transactions || [];
            document.getElementById('jsonGenTxnBody').innerHTML = txns.map(t => `
                <tr>
                    <td>${t.tc_id || ''}</td>
                    <td>${t.debit_account}</td>
                    <td>${t.debit_amount}</td>
                    <td>${t.credit_count}</td>
                    <td>${t.batch_reference || '<span class="text-muted">Not generated</span>'}</td>
                    <td>${App.statusBadge(t.status)}</td>
                </tr>
            `).join('');

        } catch (err) {
            console.error('[JsonGen] Error loading run:', err);
            App.showAlert('jsonGenResult', 'Failed to load test run details', 'danger');
        }
    },

    async generate() {
        if (!JsonGen.currentRunId) return;

        const btn = document.getElementById('generateBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="loading"></span> Generating...';

        console.log('[JsonGen] Generating JSONs for run:', JsonGen.currentRunId);

        try {
            const res = await fetch(`/api/json/test-runs/${JsonGen.currentRunId}/generate`, {
                method: 'POST'
            });
            const data = await res.json();

            if (res.ok) {
                App.showAlert('jsonGenResult',
                    `${data.message}. Output: ${data.output_dir}`,
                    data.errors > 0 ? 'danger' : 'success');
                document.getElementById('downloadJsonBtn').disabled = false;
                JsonGen.loadRunDetails();
            } else {
                App.showAlert('jsonGenResult', data.error || 'Generation failed', 'danger');
            }
        } catch (err) {
            App.showAlert('jsonGenResult', 'Network error: ' + err.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.innerHTML = 'Generate Request JSONs';
        }
    },

    download() {
        if (!JsonGen.currentRunId) return;
        console.log('[JsonGen] Downloading JSONs for run:', JsonGen.currentRunId);
        window.open(`/api/json/test-runs/${JsonGen.currentRunId}/download`, '_blank');
    }
};
