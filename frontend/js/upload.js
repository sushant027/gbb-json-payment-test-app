/**
 * Upload page — upload Excel file and create test run.
 */
const Upload = {
    async submit() {
        const schemeId = document.getElementById('uploadSchemeSelect').value;
        const fileInput = document.getElementById('uploadFile');

        if (!schemeId) {
            App.showAlert('uploadResult', 'Please select a scheme.', 'danger');
            return;
        }
        if (!fileInput.files.length) {
            App.showAlert('uploadResult', 'Please select an Excel file.', 'danger');
            return;
        }

        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('scheme_id', schemeId);

        const btn = document.getElementById('uploadBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="loading"></span> Uploading...';

        console.log('[Upload] Submitting file:', fileInput.files[0].name);

        try {
            const res = await fetch('/api/test-runs/upload', {
                method: 'POST',
                body: formData
            });
            const data = await res.json();

            if (res.ok) {
                App.showAlert('uploadResult',
                    `Test run created (ID: ${data.test_run_id}) with ${data.transactions_created} transactions.`,
                    'success');
                fileInput.value = '';
                App.loadTestRuns();
                console.log('[Upload] Success:', data);
            } else {
                App.showAlert('uploadResult', data.error || 'Upload failed', 'danger');
                console.error('[Upload] Error:', data);
            }
        } catch (err) {
            App.showAlert('uploadResult', 'Network error: ' + err.message, 'danger');
            console.error('[Upload] Network error:', err);
        } finally {
            btn.disabled = false;
            btn.innerHTML = 'Upload &amp; Create Test Run';
        }
    }
};
