/**
 * Mapping page — scheme management and XML field mapping via click-to-map UI.
 *
 * Workflow:
 * 1. Create/select a scheme
 * 2. Upload sample XML for each type (request/initiation/response)
 * 3. Credit repeating block auto-detected from XML structure
 * 4. Click Excel column (left), then click XML node (right) to create mapping
 *    - Nodes inside credit block are auto-classified as credit fields
 * 5. Configure special fields (batch ref, credit ref, status fields/codes)
 * 6. Save mapping config (includes debit_element auto-derived from credit block)
 */
const Mapping = {
    currentSchemeId: null,
    currentXmlType: 'request',
    xmlTree: null,
    selectedSource: null,    // Currently selected left-panel item
    mappingConfig: {},       // Full mapping config being built
    filenamePattern: {},     // Filename pattern from scheme (stored in separate DB column)
    allXmlPaths: [],         // All attribute paths from parsed XML
    detectedBlocks: [],      // Auto-detected repeating blocks
    creditNodePaths: new Set(), // Paths that belong to the credit block

    // Excel columns available for request XML mapping
    EXCEL_COLUMNS: [
        'tcid', 'scheme', 'debit_account', 'debit_account_parent', 'debit_ifsc', 'debit_amount',
        'credit_account', 'credit_ifsc', 'credit_count', 'credit_amount',
        'beneficiary_name', 'pay_mode', 'expected_result'
    ],

    // Internal field names for initiation/response mapping
    INTERNAL_FIELDS: [
        'batch_reference', 'debit_account', 'debit_amount', 'debit_status',
        'debit_remarks', 'reference', 'account', 'amount', 'status',
        'remarks', 'pay_mode', 'narration', 'unique_credit_resp_id'
    ],

    async loadSchemes() {
        await App.loadSchemes();
    },

    async createScheme() {
        const name = document.getElementById('newSchemeName').value.trim();
        if (!name) {
            alert('Please enter a scheme name');
            return;
        }

        const isResponseXmlSplit = document.getElementById('isResponseXmlSplit').checked ? 'Y' : 'N';
        console.log('[Mapping] Creating scheme:', name, 'is_response_xml_split:', isResponseXmlSplit);

        try {
            const res = await fetch('/api/schemes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ scheme_name: name, is_response_xml_split: isResponseXmlSplit })
            });
            const data = await res.json();

            if (res.ok) {
                document.getElementById('newSchemeName').value = '';
                document.getElementById('isResponseXmlSplit').checked = false;
                await App.loadSchemes();
                document.getElementById('mappingSchemeSelect').value = data.id;
                Mapping.loadScheme();
            } else {
                alert(data.error || 'Failed to create scheme');
            }
        } catch (err) {
            alert('Error: ' + err.message);
        }
    },

    async deleteScheme() {
        if (!Mapping.currentSchemeId) return;
        if (!confirm('Delete this scheme and all its mappings?')) return;

        try {
            await fetch(`/api/schemes/${Mapping.currentSchemeId}`, { method: 'DELETE' });
            Mapping.currentSchemeId = null;
            document.getElementById('mappingWorkspace').classList.add('hidden');
            await App.loadSchemes();
        } catch (err) {
            alert('Error: ' + err.message);
        }
    },

    async loadScheme() {
        const schemeId = document.getElementById('mappingSchemeSelect').value;
        if (!schemeId) {
            Mapping.currentSchemeId = null;
            document.getElementById('mappingWorkspace').classList.add('hidden');
            return;
        }

        Mapping.currentSchemeId = schemeId;
        console.log('[Mapping] Loading scheme:', schemeId);

        try {
            const res = await fetch(`/api/schemes/${schemeId}`);
            const scheme = await res.json();

            if (scheme.mapping_config) {
                Mapping.mappingConfig = scheme.mapping_config;
            } else {
                Mapping.mappingConfig = {};
            }

            // Store split response flag and show/hide Response Fail tab
            Mapping.isResponseXmlSplit = scheme.is_response_xml_split === 'Y';
            const responseFailTab = document.getElementById('responseFailTab');
            if (responseFailTab) {
                responseFailTab.style.display = Mapping.isResponseXmlSplit ? '' : 'none';
            }
            console.log('[Mapping] Loaded scheme: is_response_xml_split=%s',
                Mapping.isResponseXmlSplit ? 'Y' : 'N');
            // Load filename_pattern from scheme (stored in its own DB column)
            Mapping.filenamePattern = scheme.filename_pattern || {};

            document.getElementById('mappingWorkspace').classList.remove('hidden');
            Mapping.switchXmlType('request');
        } catch (err) {
            console.error('[Mapping] Error loading scheme:', err);
        }
    },

    switchXmlType(type) {
        Mapping.currentXmlType = type;
        Mapping.selectedSource = null;
        Mapping.detectedBlocks = [];
        Mapping.creditNodePaths = new Set();

        // Update tab active state
        document.querySelectorAll('#mappingWorkspace .inner-tab').forEach(t => t.classList.remove('active'));
        if (event && event.target) {
            event.target.classList.add('active');
        }

        // Update label
        document.getElementById('xmlTypeLabel').textContent =
            type.charAt(0).toUpperCase() + type.slice(1);

        // Show/hide status fields (only for initiation/response/response_fail)
        document.getElementById('statusFieldsArea').classList.toggle('hidden', type === 'request');

        // Show/hide success indicator tag (only for response tab when split is enabled)
        const successIndicatorRow = document.getElementById('successIndicatorRow');
        if (successIndicatorRow) {
            successIndicatorRow.style.display =
                (type === 'response' && Mapping.isResponseXmlSplit) ? '' : 'none';
        }
        console.log('[Mapping] switchXmlType: type=%s, isResponseXmlSplit=%s, '
            + 'showSuccessIndicator=%s',
            type, Mapping.isResponseXmlSplit,
            type === 'response' && Mapping.isResponseXmlSplit);
        // Show/hide filename pattern area (only for request)
        document.getElementById('filenamePatternArea').classList.toggle('hidden', type !== 'request');

        // Update left panel based on type
        Mapping._buildLeftPanel();

        // Load existing config for this type
        Mapping._loadExistingConfig();

        // Clear tree
        document.getElementById('xmlTreeContainer').innerHTML = '<span class="text-muted">Upload a sample XML to see its structure</span>';
        document.getElementById('mappingArea').classList.remove('hidden');

        console.log('[Mapping] Switched to XML type:', type);
    },

    async uploadSampleXml() {
        const fileInput = document.getElementById('sampleXmlFile');
        if (!fileInput.files.length) {
            alert('Please select an XML file');
            return;
        }

        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('xml_type', Mapping.currentXmlType);

        console.log('[Mapping] Uploading sample XML:', fileInput.files[0].name);

        try {
            const res = await fetch(`/api/schemes/${Mapping.currentSchemeId}/parse-xml`, {
                method: 'POST',
                body: formData
            });
            const data = await res.json();

            if (res.ok) {
                Mapping.xmlTree = data.tree;
                Mapping.allXmlPaths = [];

                // Auto-detect repeating blocks and populate dropdown
                Mapping.detectedBlocks = Mapping._detectRepeatingBlocks(data.tree);
                Mapping._populateCreditBlockDropdown();

                // Render tree (will mark credit nodes based on selected block)
                Mapping._renderXmlTree(data.tree);
                Mapping._populatePathSelects();
                console.log('[Mapping] XML parsed successfully, detected %d repeating blocks',
                    Mapping.detectedBlocks.length);
            } else {
                alert(data.error || 'Failed to parse XML');
            }
        } catch (err) {
            alert('Error: ' + err.message);
        }
    },

    /**
     * Walk the parsed XML tree to find repeating elements (siblings with same tag).
     * Returns array of { parent_path, repeat_element, count }.
     * Paths exclude the root element name for consistency with backend XPath navigation.
     */
    _detectRepeatingBlocks(node, parentPath = '', isRoot = true) {
        const blocks = [];
        // Root element: path stays empty; children build from ''
        const currentPath = isRoot ? '' : (parentPath ? `${parentPath}/${node.tag}` : node.tag);

        if (node.children && node.children.length > 0) {
            // Count child tags
            const tagCounts = {};
            node.children.forEach(c => {
                tagCounts[c.tag] = (tagCounts[c.tag] || 0) + 1;
            });

            for (const [tag, count] of Object.entries(tagCounts)) {
                if (count > 1) {
                    blocks.push({
                        parent_path: currentPath,
                        repeat_element: tag,
                        count: count
                    });
                    console.log('[Mapping] Detected repeating block: %s -> %s (%d items)',
                        currentPath, tag, count);
                }
            }

            // Recurse into children
            node.children.forEach(child => {
                blocks.push(...Mapping._detectRepeatingBlocks(child, currentPath, false));
            });
        }

        return blocks;
    },

    /**
     * Populate the credit block dropdown with detected repeating blocks.
     * Auto-select the first block found.
     */
    _populateCreditBlockDropdown() {
        const select = document.getElementById('creditBlockSelect');
        const existingParent = document.getElementById('creditParentPath').value;
        const existingRepeat = document.getElementById('creditRepeatElement').value;

        select.innerHTML = '<option value="">-- Select Credit Block --</option>';

        Mapping.detectedBlocks.forEach((block, idx) => {
            const parentLabel = block.parent_path || '(root)';
            const label = `${parentLabel} / ${block.repeat_element} (${block.count} items)`;
            const value = JSON.stringify({ parent_path: block.parent_path, repeat_element: block.repeat_element });
            select.innerHTML += `<option value='${value}'>${label}</option>`;
        });

        // Auto-select: match existing config, or pick first block
        let matched = false;
        if (existingParent && existingRepeat) {
            for (let i = 0; i < select.options.length; i++) {
                if (select.options[i].value) {
                    try {
                        const val = JSON.parse(select.options[i].value);
                        if (val.parent_path === existingParent && val.repeat_element === existingRepeat) {
                            select.selectedIndex = i;
                            matched = true;
                            break;
                        }
                    } catch (e) { /* skip */ }
                }
            }
        }

        if (!matched && Mapping.detectedBlocks.length > 0) {
            select.selectedIndex = 1; // first detected block
        }

        // Trigger selection
        Mapping.onCreditBlockSelected();
    },

    /**
     * Called when user selects a credit block from the dropdown.
     * Updates hidden inputs and rebuilds credit node tracking.
     */
    onCreditBlockSelected() {
        const select = document.getElementById('creditBlockSelect');
        const val = select.value;

        Mapping.creditNodePaths = new Set();

        if (val) {
            try {
                const block = JSON.parse(val);
                document.getElementById('creditParentPath').value = block.parent_path;
                document.getElementById('creditRepeatElement').value = block.repeat_element;

                // Build the set of credit node paths for classification
                Mapping._buildCreditNodePaths(block.parent_path, block.repeat_element);

                console.log('[Mapping] Credit block selected: %s -> %s, credit paths: %d',
                    block.parent_path, block.repeat_element, Mapping.creditNodePaths.size);
            } catch (e) {
                console.error('[Mapping] Error parsing credit block selection:', e);
            }
        } else {
            document.getElementById('creditParentPath').value = '';
            document.getElementById('creditRepeatElement').value = '';
        }

        // Re-render tree to update credit node visual tagging
        if (Mapping.xmlTree) {
            Mapping.allXmlPaths = [];
            Mapping._renderXmlTree(Mapping.xmlTree);
            Mapping._populatePathSelects();
        }

        // Reclassify existing mappings that may have been added before credit block was selected
        Mapping._reclassifyMappings();
    },

    /**
     * Move misclassified credit fields from debit fields[] to credit_block.fields[].
     * This handles the case where user mapped credit attributes before selecting the credit block.
     * Also converts their xml_path from full path (e.g., BatchDetails/.../CreditAccount/@C4038)
     * to relative path (e.g., @C4038).
     */
    _reclassifyMappings() {
        const type = Mapping.currentXmlType;
        const config = Mapping.mappingConfig[type];
        if (!config || !config.fields) return;

        const creditParent = document.getElementById('creditParentPath').value;
        const creditRepeat = document.getElementById('creditRepeatElement').value;
        if (!creditParent || !creditRepeat) return;

        if (!config.credit_block) config.credit_block = { fields: [] };
        if (!config.credit_block.fields) config.credit_block.fields = [];

        const toMove = [];
        config.fields = config.fields.filter(f => {
            const p = f.xml_path || '';
            const parts = p.split('/');

            // Check if path contains the repeat element name (e.g., "CreditAccount/@C4038"
            // or "BatchDetails/CreditAccounts/CreditAccount/@C4038")
            const repeatIdx = parts.findIndex(part => part === creditRepeat);
            if (repeatIdx >= 0) {
                // Convert path to relative: everything after the repeat element
                const relativeParts = parts.slice(repeatIdx + 1);
                f.xml_path = relativeParts.join('/') || p;
                toMove.push(f);
                return false;
            }

            // Check if it's an @attr path that matches a known credit attribute
            if (p.startsWith('@') && Mapping.creditNodePaths.has(p)) {
                toMove.push(f);
                return false;
            }

            return true;
        });

        toMove.forEach(f => config.credit_block.fields.push(f));

        if (toMove.length > 0) {
            console.log('[Mapping] Reclassified %d fields from debit to credit:', toMove.length, toMove);
            Mapping._renderMappingsList();
        }
    },

    /**
     * Build the set of attribute paths that belong inside the credit block.
     * Walks the parsed XML tree to find nodes under the repeat element.
     */
    _buildCreditNodePaths(parentPath, repeatElement) {
        if (!Mapping.xmlTree) return;

        // Navigate to the parent element in the tree using full path
        const pathParts = parentPath.split('/');
        let current = Mapping.xmlTree;

        for (const part of pathParts) {
            // Skip root element name
            if (part === Mapping.xmlTree.tag) continue;
            const child = (current.children || []).find(c => c.tag === part);
            if (!child) {
                console.warn('[Mapping] Could not find %s in tree for credit path building', part);
                return;
            }
            current = child;
        }

        // Find repeat elements under the parent
        const repeatChildren = (current.children || []).filter(c => c.tag === repeatElement);
        if (repeatChildren.length === 0) return;

        // Take the first repeat element as representative — recursively collect all paths
        const representative = repeatChildren[0];
        Mapping._collectCreditPaths(representative, '');

        console.log('[Mapping] Credit node paths built:', [...Mapping.creditNodePaths]);
    },

    /**
     * Recursively collect all attribute and text paths from a credit block element.
     * Paths are relative to the repeat element (e.g., @C4038, RmtInf/@attr, RmtInf/C7495).
     */
    _collectCreditPaths(node, relativePath) {
        // Collect attributes at this level
        if (node.attributes) {
            Object.keys(node.attributes).forEach(attr => {
                const path = relativePath ? `${relativePath}/@${attr}` : `@${attr}`;
                Mapping.creditNodePaths.add(path);
            });
        }
        // Collect text content path for leaf nodes
        if (node.text && (!node.children || node.children.length === 0)) {
            const path = relativePath ? relativePath : node.tag;
            Mapping.creditNodePaths.add(path);
        }
        // Recurse into children
        if (node.children) {
            node.children.forEach(child => {
                const childPath = relativePath ? `${relativePath}/${child.tag}` : child.tag;
                Mapping._collectCreditPaths(child, childPath);
            });
        }
    },

    _buildLeftPanel() {
        const container = document.getElementById('leftPanelItems');
        const titleEl = document.getElementById('leftPanelTitle');

        if (Mapping.currentXmlType === 'request') {
            titleEl.textContent = 'Excel Columns';
            container.innerHTML = Mapping.EXCEL_COLUMNS.map(col => `
                <div class="excel-column-item" data-source="${col}" onclick="Mapping.selectSource('${col}')">
                    ${col}
                </div>
            `).join('');

            // Add auto-generate and hardcoded options
            container.innerHTML += `
                <div style="margin-top:8px;padding-top:8px;border-top:1px solid #dee2e6">
                    <div class="excel-column-item" data-source="__auto__" onclick="Mapping.selectSource('__auto__')"
                         style="background:#fff3cd;border-color:#ffc107">
                        [Auto Generate]
                    </div>
                    <div class="excel-column-item" data-source="__hardcoded__" onclick="Mapping.selectSource('__hardcoded__')"
                         style="background:#d1ecf1;border-color:#0dcaf0">
                        [Hardcoded Value]
                    </div>
                    <div class="excel-column-item" data-source="__filename__" onclick="Mapping.selectSource('__filename__')"
                         style="background:#e8daef;border-color:#8e44ad">
                        [Filename Value]
                    </div>
                    <div class="excel-column-item" data-source="__credit_ref_copy__" onclick="Mapping.selectSource('__credit_ref_copy__')"
                         style="background:#d4edda;border-color:#28a745">
                        [Credit Ref Value]
                    </div>
                    <div class="excel-column-item" data-source="__batch_ref_copy__" onclick="Mapping.selectSource('__batch_ref_copy__')"
                         style="background:#fde2e2;border-color:#e74c3c">
                        [Batch Ref Value]
                    </div>
                </div>
            `;
        } else {
            titleEl.textContent = 'Internal Fields (map_to)';
            container.innerHTML = Mapping.INTERNAL_FIELDS.map(field => `
                <div class="excel-column-item" data-source="${field}" onclick="Mapping.selectSource('${field}')">
                    ${field}
                </div>
            `).join('');
        }
    },

    selectSource(source) {
        Mapping.selectedSource = source;
        // Update visual selection
        document.querySelectorAll('.excel-column-item').forEach(el => {
            el.classList.toggle('selected', el.dataset.source === source);
        });
        console.log('[Mapping] Selected source:', source);
    },

    selectXmlNode(path, isCreditNode) {
        if (!Mapping.selectedSource) {
            alert('First select a field from the left panel, then click an XML node.');
            return;
        }

        console.log('[Mapping] Creating mapping:', Mapping.selectedSource, '->', path,
            isCreditNode ? '(CREDIT)' : '(DEBIT)');

        const source = Mapping.selectedSource;

        if (source === '__auto__') {
            // Prompt for auto-generate config
            const prefix = prompt('Enter prefix (e.g., BATCH, CREDIT):', '');
            const length = parseInt(prompt('Enter total length:', '12')) || 12;
            const type = prompt('Type: numeric or alphanumeric', 'alphanumeric');

            Mapping._addMapping({
                source: 'auto',
                xml_path: path,
                auto_generate: { type, prefix: prefix || '', length }
            }, isCreditNode);
        } else if (source === '__hardcoded__') {
            // Pre-fill with value from uploaded XML as default
            const defaultValue = Mapping._getXmlTreeValue(path) || '';
            const value = prompt('Enter hardcoded value:', defaultValue);
            if (value !== null) {
                Mapping._addMapping({
                    source: 'hardcoded',
                    xml_path: path,
                    value: value
                }, isCreditNode);
            }
        } else if (source === '__filename__') {
            // Map the generated filename value to this XML tag
            Mapping._addMapping({
                source: 'filename',
                xml_path: path
            }, isCreditNode);
        } else if (source === '__credit_ref_copy__') {
            // Map the credit reference value (copy) to this XML tag
            Mapping._addMapping({
                source: 'credit_ref_copy',
                xml_path: path
            }, isCreditNode);
        } else if (source === '__batch_ref_copy__') {
            // Map the batch reference value (copy) to this XML tag
            Mapping._addMapping({
                source: 'batch_ref_copy',
                xml_path: path
            }, isCreditNode);
        } else if (Mapping.currentXmlType === 'request') {
            Mapping._addMapping({
                source: 'excel',
                excel_column: source,
                xml_path: path
            }, isCreditNode);
        } else {
            // Initiation/Response: map XML path to internal field
            Mapping._addMapping({
                xml_path: path,
                map_to: source
            }, isCreditNode);
        }

        Mapping.selectedSource = null;
        document.querySelectorAll('.excel-column-item').forEach(el => el.classList.remove('selected'));
        Mapping._renderMappingsList();
    },

    /**
     * Add a mapping to the config. Uses isCreditNode (from tree metadata) to classify.
     */
    _addMapping(mapping, isCreditNode) {
        const type = Mapping.currentXmlType;
        if (!Mapping.mappingConfig[type]) {
            Mapping.mappingConfig[type] = { fields: [], credit_block: { fields: [] } };
        }

        const config = Mapping.mappingConfig[type];

        if (isCreditNode) {
            if (!config.credit_block) config.credit_block = { fields: [] };
            if (!config.credit_block.fields) config.credit_block.fields = [];
            config.credit_block.fields.push(mapping);
            console.log('[Mapping] Added as CREDIT field:', mapping);
        } else {
            if (!config.fields) config.fields = [];
            config.fields.push(mapping);
            console.log('[Mapping] Added as DEBIT field:', mapping);
        }

        // Mark XML node as mapped
        document.querySelectorAll('.xml-node-item').forEach(el => {
            if (el.dataset.path === mapping.xml_path) {
                el.classList.add('mapped');
            }
        });
    },

    removeMapping(type, isCredit, index) {
        const config = Mapping.mappingConfig[type];
        if (!config) return;

        if (isCredit) {
            config.credit_block.fields.splice(index, 1);
        } else {
            config.fields.splice(index, 1);
        }
        Mapping._renderMappingsList();
    },

    _renderMappingsList() {
        const container = document.getElementById('mappingsList');
        const type = Mapping.currentXmlType;
        const config = Mapping.mappingConfig[type] || {};

        let html = '';

        // Debit-level fields
        const fields = config.fields || [];
        if (fields.length) {
            html += '<div style="font-size:12px;font-weight:600;color:#6c757d;margin-bottom:4px">Debit/Header Fields:</div>';
        }
        fields.forEach((f, idx) => {
            const left = f.source === 'auto' ? `[Auto: ${f.auto_generate?.prefix || ''}]` :
                         f.source === 'hardcoded' ? `[Fixed: ${f.value}]` :
                         f.source === 'filename' ? '[Filename]' :
                         f.source === 'credit_ref_copy' ? '[CreditRef Copy]' :
                         f.source === 'batch_ref_copy' ? '[BatchRef Copy]' :
                         f.excel_column || f.map_to || '?';
            html += `
                <div class="mapping-item">
                    <strong>${left}</strong>
                    <span class="arrow">-></span>
                    <code>${f.xml_path}</code>
                    <button class="remove-btn" onclick="Mapping.removeMapping('${type}', false, ${idx})">x</button>
                </div>
            `;
        });

        // Credit-level fields
        const creditFields = config.credit_block?.fields || [];
        if (creditFields.length) {
            html += '<div style="margin-top:8px;font-size:12px;font-weight:600;color:#28a745">Credit Block Fields:</div>';
            creditFields.forEach((f, idx) => {
                const left = f.source === 'auto' ? `[Auto: ${f.auto_generate?.prefix || ''}]` :
                             f.source === 'hardcoded' ? `[Fixed: ${f.value}]` :
                             f.source === 'filename' ? '[Filename]' :
                             f.source === 'credit_ref_copy' ? '[CreditRef Copy]' :
                             f.source === 'batch_ref_copy' ? '[BatchRef Copy]' :
                             f.excel_column || f.map_to || '?';
                html += `
                    <div class="mapping-item" style="border-left:3px solid #28a745">
                        <strong>${left}</strong>
                        <span class="arrow">-></span>
                        <code>${f.xml_path}</code>
                        <button class="remove-btn" onclick="Mapping.removeMapping('${type}', true, ${idx})">x</button>
                    </div>
                `;
            });
        }

        container.innerHTML = html || '<span class="text-muted">No mappings configured yet</span>';
    },

    /**
     * Render the XML tree with credit nodes visually tagged.
     * Nodes inside the credit block get data-inside-credit="true" and green styling.
     * parentPath tracks the full ancestor path (excluding root) for correct path building.
     */
    _renderXmlTree(node, depth = 0, insideCreditBlock = false, parentPath = '', creditRelativePath = '') {
        const container = document.getElementById('xmlTreeContainer');
        if (depth === 0) {
            container.innerHTML = '';
            Mapping.allXmlPaths = [];
        }

        const creditRepeat = document.getElementById('creditRepeatElement').value;
        const creditParent = document.getElementById('creditParentPath').value;

        // Build current node's full path (excluding root element name)
        let currentFullPath;
        if (depth === 0) {
            currentFullPath = '';  // Root element — path prefix is empty
        } else {
            currentFullPath = parentPath ? `${parentPath}/${node.tag}` : node.tag;
        }

        // Check if this node is the start of the credit repeating block
        const isRepeatElement = (creditRepeat && node.tag === creditRepeat && insideCreditBlock);
        // Check if this node is the parent container of the credit block
        // Use full path comparison instead of just tag name to avoid false matches
        const isInsideParent = creditParent && (
            currentFullPath === creditParent ||
            (depth === 0 && creditParent === node.tag)
        );
        const childInsideCredit = insideCreditBlock || isInsideParent;

        // Build credit-relative path (path relative to the credit repeat element)
        let currentCreditRelativePath = '';
        if (insideCreditBlock && !isRepeatElement) {
            // We're inside credit block but not at the repeat element itself
            // Build path relative to repeat element
            currentCreditRelativePath = creditRelativePath
                ? `${creditRelativePath}/${node.tag}`
                : node.tag;
        }

        const el = document.createElement('div');
        el.style.paddingLeft = (depth * 20) + 'px';

        // Element name
        const elemSpan = document.createElement('span');
        elemSpan.className = 'xml-element';

        if (isRepeatElement) {
            elemSpan.innerHTML = '&lt;' + node.tag + '&gt; <span style="color:#28a745;font-size:11px;font-weight:600">(Credit Block)</span>';
            elemSpan.style.color = '#28a745';
        } else {
            elemSpan.textContent = '<' + node.tag + '>';
        }

        el.appendChild(elemSpan);
        container.appendChild(el);

        // Attributes
        if (node.attributes) {
            Object.entries(node.attributes).forEach(([attr, val]) => {
                // Determine if this attribute is inside the credit block
                const attrInsideCredit = isRepeatElement || (insideCreditBlock && depth > 1);
                const path = Mapping._buildAttrPath(node, attr, depth, attrInsideCredit, currentFullPath, currentCreditRelativePath);
                Mapping.allXmlPaths.push(path);

                const attrEl = document.createElement('div');
                attrEl.style.paddingLeft = ((depth + 1) * 20) + 'px';

                const creditAttr = attrInsideCredit ? 'data-inside-credit="true"' : '';
                const creditStyle = attrInsideCredit ? 'border-left:2px solid #28a745;padding-left:4px;' : '';

                const escapedPath = path.replace(/'/g, "\\'");
                attrEl.innerHTML = `
                    <span class="xml-node-item xml-attribute" data-path="${path}"
                          ${creditAttr}
                          style="${creditStyle}"
                          onclick="Mapping.selectXmlNode('${escapedPath}', ${attrInsideCredit})">
                        @${attr} <span class="node-type">= "${Mapping._truncate(val, 30)}"</span>
                        ${attrInsideCredit ? '<span style="color:#28a745;font-size:10px"> [credit]</span>' : ''}
                    </span>
                `;
                container.appendChild(attrEl);
            });
        }

        // Text-content elements (leaf nodes with text, like <C7002>value</C7002>)
        if (node.text && (!node.children || node.children.length === 0)) {
            const textInsideCredit = isRepeatElement || (insideCreditBlock && depth > 1);
            let textPath = textInsideCredit
                ? (currentCreditRelativePath || node.tag)
                : (currentFullPath || node.tag);

            // Append index for repeated leaf siblings (e.g., C7495[0], C7495[1])
            if (node._repeatIndex !== undefined) {
                textPath = `${textPath}[${node._repeatIndex}]`;
            }
            Mapping.allXmlPaths.push(textPath);

            // Visual label for repeated leaves: [1/3], [2/3], etc.
            const repeatInfo = node._repeatTotal
                ? `<span style="color:#6c757d;font-size:10px;margin-left:4px">[${node._repeatIndex + 1}/${node._repeatTotal}]</span>`
                : '';

            const textEl = document.createElement('div');
            textEl.style.paddingLeft = ((depth + 1) * 20) + 'px';

            const creditAttr = textInsideCredit ? 'data-inside-credit="true"' : '';
            const creditStyle = textInsideCredit ? 'border-left:2px solid #28a745;padding-left:4px;' : '';

            const escapedTextPath = textPath.replace(/'/g, "\\'");
            textEl.innerHTML = `
                <span class="xml-node-item xml-text" data-path="${textPath}"
                      ${creditAttr}
                      style="${creditStyle}color:#0d6efd;cursor:pointer;"
                      onclick="Mapping.selectXmlNode('${escapedTextPath}', ${textInsideCredit})">
                    text() <span class="node-type">= "${Mapping._truncate(node.text, 30)}"</span>
                    ${repeatInfo}
                    ${textInsideCredit ? '<span style="color:#28a745;font-size:10px"> [credit]</span>' : ''}
                </span>
            `;
            container.appendChild(textEl);
        }

        // Children — render all leaf repeats (with indexed paths), collapse complex repeats
        if (node.children) {
            // Count occurrences of each child tag
            const tagCounts = {};
            node.children.forEach(c => {
                tagCounts[c.tag] = (tagCounts[c.tag] || 0) + 1;
            });

            const complexSeen = {};
            const leafCounter = {};

            node.children.forEach(child => {
                const isLeaf = (!child.children || child.children.length === 0);
                const count = tagCounts[child.tag];

                // Credit block repeat elements: always collapse (existing behavior)
                if (childInsideCredit && child.tag === creditRepeat) {
                    if (!complexSeen[child.tag]) {
                        complexSeen[child.tag] = true;
                        Mapping._renderXmlTree(child, depth + 1, true, currentFullPath, '');
                    }
                }
                // Complex repeated non-credit elements: collapse to first instance
                else if (count > 1 && !isLeaf) {
                    if (!complexSeen[child.tag]) {
                        complexSeen[child.tag] = true;
                        Mapping._renderXmlTree(child, depth + 1, childInsideCredit, currentFullPath, currentCreditRelativePath);
                    }
                }
                // Leaf nodes (repeated or not): render every instance with index metadata
                else {
                    if (count > 1 && isLeaf) {
                        leafCounter[child.tag] = (leafCounter[child.tag] || 0);
                        child._repeatIndex = leafCounter[child.tag];
                        child._repeatTotal = count;
                        leafCounter[child.tag]++;
                    }
                    Mapping._renderXmlTree(child, depth + 1, childInsideCredit, currentFullPath, currentCreditRelativePath);
                }
            });

            // Show count info for collapsed complex blocks
            for (const [tag, count] of Object.entries(tagCounts)) {
                if (count > 1 && complexSeen[tag]) {
                    const infoEl = document.createElement('div');
                    infoEl.style.paddingLeft = ((depth + 1) * 20) + 'px';
                    infoEl.innerHTML = `<span style="color:#6c757d;font-size:11px;font-style:italic">
                        ... ${count - 1} more ${tag} element(s) with same structure
                    </span>`;
                    container.appendChild(infoEl);
                }
            }
        }
    },

    _buildAttrPath(node, attr, depth, insideCredit, fullPath = '', creditRelativePath = '') {
        // Credit-level attributes: use credit-relative path for nested children
        if (insideCredit) {
            if (creditRelativePath) {
                return `${creditRelativePath}/@${attr}`;
            }
            return `@${attr}`;
        }
        // Use the full ancestor path for correct element nesting
        if (fullPath) {
            // Append index for repeated leaf siblings so attribute paths are unique
            // (e.g., Transform[0]/@Algorithm vs Transform[1]/@Algorithm)
            if (node._repeatIndex !== undefined) {
                fullPath = `${fullPath}[${node._repeatIndex}]`;
            }
            return `${fullPath}/@${attr}`;
        }
        // Fallback for root element (fullPath is empty at depth 0)
        return `${node.tag}/@${attr}`;
    },

    _getXmlTreeValue(path) {
        // Extract value from uploaded XML tree at a given path.
        // Supports: "Element/@Attr", "Element/Child", "@Attr", "Element/Child[0]"
        if (!Mapping.xmlTree || !path) return '';

        const parts = path.replace(/\[\d+\]/g, '').split('/');
        let node = Mapping.xmlTree;

        // Skip root element if path starts with it
        let startIdx = 0;
        if (parts[0] === node.tag) startIdx = 1;

        for (let i = startIdx; i < parts.length; i++) {
            const part = parts[i];
            if (part.startsWith('@')) {
                // Attribute on current node
                return (node.attributes && node.attributes[part.slice(1)]) || '';
            }
            // Navigate to child element
            const child = (node.children || []).find(c => c.tag === part);
            if (!child) return '';
            node = child;
        }
        // Reached a text element
        return node.text || '';
    },

    _populatePathSelects() {
        const paths = Mapping.allXmlPaths;
        ['batchRefSelect', 'creditRefSelect', 'debitStatusSelect', 'creditStatusSelect'].forEach(id => {
            const sel = document.getElementById(id);
            const current = sel.value;
            sel.innerHTML = '<option value="">-- Select --</option>';
            paths.forEach(p => {
                sel.innerHTML += `<option value="${p}">${p}</option>`;
            });
            if (current) sel.value = current;
        });
    },

    _loadExistingConfig() {
        const type = Mapping.currentXmlType;
        const config = Mapping.mappingConfig[type] || {};

        // Load credit block config into hidden inputs (supports both credit_block and repeating_blocks)
        const primaryBlock = (config.repeating_blocks || []).find(b => b.name === 'credits') || {};
        const cb = config.credit_block || primaryBlock;
        document.getElementById('creditParentPath').value = cb.parent_path || '';
        document.getElementById('creditRepeatElement').value = cb.repeat_element || '';

        // Reset the dropdown — it will be re-populated when XML is uploaded
        const select = document.getElementById('creditBlockSelect');
        if (cb.parent_path && cb.repeat_element) {
            // If we have existing config but no tree yet, show it in dropdown
            const label = `${cb.parent_path} / ${cb.repeat_element} (saved)`;
            const value = JSON.stringify({ parent_path: cb.parent_path, repeat_element: cb.repeat_element });
            select.innerHTML = `<option value="">-- Upload XML to detect --</option>
                <option value='${value}' selected>${label}</option>`;
            // Build credit node paths from existing config
            Mapping.creditNodePaths = new Set();
            if (Mapping.xmlTree) {
                Mapping._buildCreditNodePaths(cb.parent_path, cb.repeat_element);
            }
        } else {
            select.innerHTML = '<option value="">-- Upload XML to detect --</option>';
        }

        // Load filename pattern from scheme-level data (request only)
        if (type === 'request') {
            const fp = Mapping.filenamePattern || {};
            document.getElementById('filenamePrefix').value = fp.prefix || '';
            document.getElementById('filenameDateFormat').value = fp.date_format || '';
        }

        // Load special fields
        document.getElementById('batchRefSelect').value = config.batch_reference_field || '';
        document.getElementById('creditRefSelect').value = config.credit_reference_field || '';

        if (type !== 'request') {
            // Load success indicator tag for response type
            if (type === 'response' && Mapping.isResponseXmlSplit) {
                document.getElementById('successIndicatorTag').value = config.success_indicator_tag || '';
                console.log('[Mapping] Loaded success_indicator_tag:', config.success_indicator_tag || '');
            }

            document.getElementById('debitStatusSelect').value = config.debit_status_field || '';
            document.getElementById('creditStatusSelect').value = config.credit_status_field || '';

            // Separate debit and credit status values
            const dsv = config.debit_status_values || config.status_values || {};
            document.getElementById('debitSuccessCodes').value = (dsv.success || []).join(',');
            document.getElementById('debitFailureCodes').value = (dsv.failure || []).join(',');
            document.getElementById('debitPendingCodes').value = (dsv.pending || []).join(',');

            const csv = config.credit_status_values || config.status_values || {};
            document.getElementById('creditSuccessCodes').value = (csv.success || []).join(',');
            document.getElementById('creditFailureCodes').value = (csv.failure || []).join(',');
            document.getElementById('creditPendingCodes').value = (csv.pending || []).join(',');
        }

        Mapping._renderMappingsList();
    },

    async saveMappingConfig() {
        if (!Mapping.currentSchemeId) return;

        // Collect special fields for current xml type
        const type = Mapping.currentXmlType;
        if (!Mapping.mappingConfig[type]) {
            Mapping.mappingConfig[type] = {};
        }

        const config = Mapping.mappingConfig[type];

        // Batch & credit reference
        config.batch_reference_field = document.getElementById('batchRefSelect').value;
        config.credit_reference_field = document.getElementById('creditRefSelect').value;

        // Filename pattern — saved as top-level key (stored in schemes.filename_pattern column)
        if (type === 'request') {
            Mapping.mappingConfig.filename_pattern = {
                prefix: document.getElementById('filenamePrefix').value.trim(),
                date_format: document.getElementById('filenameDateFormat').value.trim()
            };
        }

        // Credit block config (maintain both credit_block for backward compat and repeating_blocks)
        if (!config.credit_block) config.credit_block = { fields: [] };
        config.credit_block.parent_path = document.getElementById('creditParentPath').value;
        config.credit_block.repeat_element = document.getElementById('creditRepeatElement').value;

        // Build repeating_blocks array from credit_block + any additional blocks
        const primaryBlock = {
            name: 'credits',
            parent_path: config.credit_block.parent_path,
            repeat_element: config.credit_block.repeat_element,
            fields: config.credit_block.fields || []
        };
        // Start with primary credit block, add additional blocks if configured
        const additionalBlocks = (config.repeating_blocks || []).filter(b => b.name !== 'credits');
        config.repeating_blocks = [primaryBlock, ...additionalBlocks].filter(b => b.parent_path && b.repeat_element);

        // Auto-derive debit_element from credit block parent path
        const parentPath = config.credit_block.parent_path || '';
        if (parentPath) {
            const firstPart = parentPath.split('/').find(p => p && p !== (Mapping.xmlTree?.tag || ''));
            if (firstPart) {
                config.debit_element = firstPart;
                console.log('[Mapping] Auto-derived debit_element:', firstPart);
            }
        }

        // Success indicator tag (response only, when split response is enabled)
        if (type === 'response' && Mapping.isResponseXmlSplit) {
            config.success_indicator_tag = document.getElementById('successIndicatorTag').value.trim();
            console.log('[Mapping] Saving success_indicator_tag:', config.success_indicator_tag);
        }

        // Status fields (initiation/response/response_fail only)
        if (type !== 'request') {
            config.debit_status_field = document.getElementById('debitStatusSelect').value;
            config.credit_status_field = document.getElementById('creditStatusSelect').value;

            config.debit_status_values = {
                success: Mapping._parseCodes('debitSuccessCodes'),
                failure: Mapping._parseCodes('debitFailureCodes'),
                pending: Mapping._parseCodes('debitPendingCodes')
            };
            config.credit_status_values = {
                success: Mapping._parseCodes('creditSuccessCodes'),
                failure: Mapping._parseCodes('creditFailureCodes'),
                pending: Mapping._parseCodes('creditPendingCodes')
            };
        }

        // Root element from tree
        if (Mapping.xmlTree) {
            config.root_element = Mapping.xmlTree.tag;
        }

        console.log('[Mapping] Saving config:', JSON.stringify(Mapping.mappingConfig, null, 2));

        try {
            const res = await fetch(`/api/schemes/${Mapping.currentSchemeId}/mapping`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(Mapping.mappingConfig)
            });
            const data = await res.json();

            if (res.ok) {
                App.showAlert('mappingMsg', 'Mapping config saved successfully!', 'success');
            } else {
                App.showAlert('mappingMsg', data.error || 'Failed to save', 'danger');
            }
        } catch (err) {
            App.showAlert('mappingMsg', 'Error: ' + err.message, 'danger');
        }
    },

    _parseCodes(inputId) {
        const val = document.getElementById(inputId).value.trim();
        if (!val) return [];
        return val.split(',').map(s => s.trim()).filter(Boolean);
    },

    _truncate(str, max) {
        if (!str) return '';
        return str.length > max ? str.substring(0, max) + '...' : str;
    }
};
