/**
 * XML Generation page — generate and download request XMLs.
 */
const XmlGen = {
    currentRunId: null,

    loadTestRuns() {
        App.populateRunSelect('xmlGenRunSelect');
    },

    async loadRunDetails() {
        const runId = document.getElementById('xmlGenRunSelect').value;
        if (!runId) {
            document.getElementById('xmlGenRunInfo').classList.add('hidden');
            document.getElementById('xmlGenTxnBody').innerHTML = '';
            document.getElementById('generateBtn').disabled = true;
            document.getElementById('downloadXmlBtn').disabled = true;
            return;
        }

        XmlGen.currentRunId = runId;
        console.log('[XmlGen] Loading run details:', runId);

        try {
            const res = await fetch(`/api/test-runs/${runId}`);
            const data = await res.json();

            document.getElementById('xmlGenRunInfo').classList.remove('hidden');
            document.getElementById('xmlGenRunInfo').innerHTML = `
                <div class="alert alert-info">
                    Run #${data.id} — Scheme: ${data.scheme_id} — Status: ${data.status} — Transactions: ${data.total_transactions}
                </div>
            `;

            // Enable buttons based on status
            document.getElementById('generateBtn').disabled = false;
            document.getElementById('downloadXmlBtn').disabled = (data.status === 'pending');

            // Show transactions
            const txns = data.transactions || [];
            document.getElementById('xmlGenTxnBody').innerHTML = txns.map(t => `
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
            console.error('[XmlGen] Error loading run:', err);
            App.showAlert('xmlGenResult', 'Failed to load test run details', 'danger');
        }
    },

    async generate() {
        if (!XmlGen.currentRunId) return;

        const btn = document.getElementById('generateBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="loading"></span> Generating...';

        console.log('[XmlGen] Generating XMLs for run:', XmlGen.currentRunId);

        try {
            const res = await fetch(`/api/xml/test-runs/${XmlGen.currentRunId}/generate`, {
                method: 'POST'
            });
            const data = await res.json();

            if (res.ok) {
                App.showAlert('xmlGenResult',
                    `${data.message}. Output: ${data.output_dir}`,
                    data.errors > 0 ? 'danger' : 'success');
                document.getElementById('downloadXmlBtn').disabled = false;
                XmlGen.loadRunDetails();
            } else {
                App.showAlert('xmlGenResult', data.error || 'Generation failed', 'danger');
            }
        } catch (err) {
            App.showAlert('xmlGenResult', 'Network error: ' + err.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.innerHTML = 'Generate Request XMLs';
        }
    },

    download() {
        if (!XmlGen.currentRunId) return;
        console.log('[XmlGen] Downloading XMLs for run:', XmlGen.currentRunId);
        window.open(`/api/xml/test-runs/${XmlGen.currentRunId}/download`, '_blank');
    }
};
