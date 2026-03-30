/**
 * Results page — view validation results and generate reports.
 */
const Results = {
    currentRunId: null,

    loadTestRuns() {
        App.populateRunSelect('resultsRunSelect');
    },

    async loadResults() {
        const runId = document.getElementById('resultsRunSelect').value;
        if (!runId) {
            document.getElementById('resultsSummary').classList.add('hidden');
            document.getElementById('resultsTxnBody').innerHTML = '';
            document.getElementById('resultsCreditBody').innerHTML = '';
            document.getElementById('genReportBtn').disabled = true;
            document.getElementById('dlReportBtn').disabled = true;
            return;
        }

        Results.currentRunId = runId;
        console.log('[Results] Loading results for run:', runId);

        try {
            const res = await fetch(`/api/results/test-runs/${runId}/results`);
            const data = await res.json();

            if (!res.ok) {
                App.showAlert('resultsMsg', data.error || 'Failed to load results', 'danger');
                return;
            }

            // Summary
            const summary = data.summary || {};
            document.getElementById('resultsSummary').classList.remove('hidden');
            document.getElementById('resultsSummary').innerHTML = `
                <div class="inline-flex" style="gap:16px">
                    <div class="badge badge-info">Total: ${summary.total || 0}</div>
                    <div class="badge badge-pass">Passed: ${summary.passed || 0}</div>
                    <div class="badge badge-fail">Failed: ${summary.failed || 0}</div>
                    <div class="badge badge-pending">Pending: ${summary.pending || 0}</div>
                </div>
            `;

            // Enable buttons
            document.getElementById('genReportBtn').disabled = false;
            const run = data.test_run || {};
            document.getElementById('dlReportBtn').disabled = !run.report_path;

            // Transaction results
            const txns = data.transactions || [];
            document.getElementById('resultsTxnBody').innerHTML = txns.map(t => {
                const vr = t.validation_result;
                const overall = vr && typeof vr === 'object' ? vr.overall : (vr || '');
                return `
                    <tr>
                        <td>${t.tc_id || ''}</td>
                        <td>${t.debit_account}</td>
                        <td>${t.debit_amount}</td>
                        <td>${t.expected_status || ''}</td>
                        <td>${t.actual_debit_status || ''}</td>
                        <td>${App.statusBadge(t.initiation_validation || '')}</td>
                        <td title="${(t.initiation_validation_desc || '').replace(/"/g, '&quot;')}">${t.initiation_validation_desc || ''}</td>
                        <td>${App.statusBadge(t.response_validation || '')}</td>
                        <td title="${(t.response_validation_desc || '').replace(/"/g, '&quot;')}">${t.response_validation_desc || ''}</td>
                        <td>${App.statusBadge(overall)}</td>
                    </tr>
                `;
            }).join('');

            // Credit-level results
            let creditRows = '';
            txns.forEach(t => {
                const credits = Array.isArray(t.credit_json) ? t.credit_json : [];
                credits.forEach((c, idx) => {
                    creditRows += `
                        <tr>
                            <td>${t.tc_id || ''}</td>
                            <td>${idx + 1}</td>
                            <td>${c.account || ''}</td>
                            <td>${c.amount || ''}</td>
                            <td>${c.credit_reference || ''}</td>
                            <td>${c.initiation_status || ''}</td>
                            <td>${c.response_status || ''}</td>
                            <td>${App.statusBadge(c.validation_result || '')}</td>
                        </tr>
                    `;
                });
            });
            document.getElementById('resultsCreditBody').innerHTML = creditRows ||
                '<tr><td colspan="8" style="text-align:center">No credit data</td></tr>';

        } catch (err) {
            console.error('[Results] Error:', err);
            App.showAlert('resultsMsg', 'Failed to load results: ' + err.message, 'danger');
        }
    },

    async generateReport() {
        if (!Results.currentRunId) return;

        const btn = document.getElementById('genReportBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="loading"></span> Generating...';

        try {
            const res = await fetch(`/api/results/test-runs/${Results.currentRunId}/report`, {
                method: 'POST'
            });
            const data = await res.json();

            if (res.ok) {
                App.showAlert('resultsMsg', data.message, 'success');
                document.getElementById('dlReportBtn').disabled = false;
            } else {
                App.showAlert('resultsMsg', data.error || 'Report generation failed', 'danger');
            }
        } catch (err) {
            App.showAlert('resultsMsg', 'Network error: ' + err.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.innerHTML = 'Generate XLSX Report';
        }
    },

    downloadReport() {
        if (!Results.currentRunId) return;
        window.open(`/api/results/test-runs/${Results.currentRunId}/download-report`, '_blank');
    }
};
