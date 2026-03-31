/**
 * Mapping page — scheme management and JSON field mapping via click-to-map UI.
 *
 * Workflow:
 * 1. Create/select a scheme
 * 2. Upload sample JSON for each type (request/initiation/response)
 * 3. Credit repeating block auto-detected from JSON structure (arrays of objects)
 * 4. Click Excel column (left), then click JSON node (right) to create mapping
 *    - Nodes inside credit block are auto-classified as credit fields
 * 5. Configure special fields (batch ref, credit ref, status fields/codes)
 * 6. Save mapping config
 */
const Mapping = {
    currentSchemeId: null,
    currentJsonType: 'request',
    jsonTree: null,
    selectedSource: null,    // Currently selected left-panel item
    mappingConfig: {},       // Full mapping config being built
    filenamePattern: {},     // Filename pattern from scheme (stored in separate DB column)
    allJsonPaths: [],        // All paths from parsed JSON
    detectedBlocks: [],      // Auto-detected repeating blocks (arrays of objects)
    creditNodePaths: new Set(), // Paths that belong to the credit block

    // Excel columns available for request JSON mapping
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
            Mapping.isResponseSplit = scheme.is_response_xml_split === 'Y';
            const responseFailTab = document.getElementById('responseFailTab');
            if (responseFailTab) {
                responseFailTab.style.display = Mapping.isResponseSplit ? '' : 'none';
            }

            // Load filename_pattern from scheme
            Mapping.filenamePattern = scheme.filename_pattern || {};

            document.getElementById('mappingWorkspace').classList.remove('hidden');
            Mapping.switchJsonType('request');
        } catch (err) {
            console.error('[Mapping] Error loading scheme:', err);
        }
    },

    switchJsonType(type) {
        Mapping.currentJsonType = type;
        Mapping.selectedSource = null;
        Mapping.detectedBlocks = [];
        Mapping.creditNodePaths = new Set();

        // Update tab active state
        document.querySelectorAll('#mappingWorkspace .inner-tab').forEach(t => t.classList.remove('active'));
        if (event && event.target) {
            event.target.classList.add('active');
        }

        // Update label
        document.getElementById('jsonTypeLabel').textContent =
            type.charAt(0).toUpperCase() + type.slice(1);

        // Show/hide status fields (only for initiation/response/response_fail)
        document.getElementById('statusFieldsArea').classList.toggle('hidden', type === 'request');

        // Show/hide success indicator (only for response tab when split is enabled)
        const successIndicatorRow = document.getElementById('successIndicatorRow');
        if (successIndicatorRow) {
            successIndicatorRow.style.display =
                (type === 'response' && Mapping.isResponseSplit) ? '' : 'none';
        }

        // Show/hide filename pattern area (only for request)
        document.getElementById('filenamePatternArea').classList.toggle('hidden', type !== 'request');

        // Update left panel based on type
        Mapping._buildLeftPanel();

        // Load existing config for this type
        Mapping._loadExistingConfig();

        // Clear tree
        document.getElementById('jsonTreeContainer').innerHTML = '<span class="text-muted">Upload a sample JSON to see its structure</span>';
        document.getElementById('mappingArea').classList.remove('hidden');

        console.log('[Mapping] Switched to JSON type:', type);
    },

    async uploadSampleJson() {
        const fileInput = document.getElementById('sampleJsonFile');
        if (!fileInput.files.length) {
            alert('Please select a JSON file');
            return;
        }

        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('json_type', Mapping.currentJsonType);

        console.log('[Mapping] Uploading sample JSON:', fileInput.files[0].name);

        try {
            const res = await fetch(`/api/schemes/${Mapping.currentSchemeId}/parse-json`, {
                method: 'POST',
                body: formData
            });
            const data = await res.json();

            if (res.ok) {
                Mapping.jsonTree = data.tree;
                Mapping.allJsonPaths = [];

                // Auto-detect repeating blocks (arrays of objects) and populate dropdowns
                Mapping.detectedBlocks = Mapping._detectRepeatingBlocks(data.tree);
                Mapping._populateBatchContainerDropdown();
                Mapping._populateCreditBlockDropdown();

                // Render tree
                Mapping._renderJsonTree(data.tree);
                Mapping._populatePathSelects();
                console.log('[Mapping] JSON parsed successfully, detected %d repeating blocks',
                    Mapping.detectedBlocks.length);
            } else {
                alert(data.error || 'Failed to parse JSON');
            }
        } catch (err) {
            alert('Error: ' + err.message);
        }
    },

    /**
     * Walk the parsed JSON tree to find arrays of objects (repeating blocks).
     * Returns array of { parent_path, repeat_element, count }.
     */
    _detectRepeatingBlocks(node, parentPath = '') {
        const blocks = [];

        if (node.type === 'array' && node.item_count > 0 && node.children && node.children.length > 0) {
            // Check if children represent an object (first child has children = keys of object)
            const firstChild = node.children[0];
            if (firstChild && (firstChild.type === 'object' || firstChild.children)) {
                // This is an array of objects — it's a repeating block
                // parent_path = path to the parent object containing this array
                // repeat_element = the key name of this array
                const parts = node.path.split('.');
                const repeatElement = parts.pop();
                const parentPathStr = parts.join('.');

                blocks.push({
                    parent_path: parentPathStr,
                    repeat_element: repeatElement,
                    count: node.item_count || node.children.length
                });
                console.log('[Mapping] Detected repeating block: %s.%s (%d items)',
                    parentPathStr, repeatElement, node.item_count);
            }
        }

        // Recurse into children
        if (node.children) {
            node.children.forEach(child => {
                blocks.push(...Mapping._detectRepeatingBlocks(child, node.path));
            });
        }

        return blocks;
    },

    /**
     * Populate the credit block dropdown with detected repeating blocks.
     */
    _populateCreditBlockDropdown() {
        const select = document.getElementById('creditBlockSelect');
        const existingParent = document.getElementById('creditParentPath').value;
        const existingRepeat = document.getElementById('creditRepeatElement').value;

        select.innerHTML = '<option value="">-- Select Credit Block --</option>';

        Mapping.detectedBlocks.forEach((block, idx) => {
            const parentLabel = block.parent_path || '(root)';
            const label = `${parentLabel}.${block.repeat_element} (${block.count} items)`;
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

        if (!matched) {
            // Do NOT auto-select — require user to explicitly choose the credit block.
            // Previously this auto-selected index 1 (first detected block), which
            // incorrectly picked BatchDetails instead of CreditAccount in nested structures.
            select.selectedIndex = 0;
        }

        Mapping.onCreditBlockSelected();
    },

    /**
     * Populate the batch container dropdown with top-level arrays from the JSON tree.
     * These are arrays that are direct children of the root key (e.g., BatchDetails).
     */
    _populateBatchContainerDropdown() {
        const select = document.getElementById('batchContainerSelect');
        select.innerHTML = '<option value="">-- Auto-detect --</option>';

        if (Mapping.jsonTree && Mapping.jsonTree.children) {
            Mapping.jsonTree.children
                .filter(c => c.type === 'array')
                .forEach(child => {
                    const label = `${child.key} (${child.item_count || 0} items)`;
                    select.innerHTML += `<option value="${child.key}">${label}</option>`;
                });
        }

        // Auto-select from existing config
        const type = Mapping.currentJsonType;
        const existingContainer = (Mapping.mappingConfig[type] || {}).batch_container;
        if (existingContainer) {
            select.value = existingContainer;
        }

        console.log('[Mapping] Batch container dropdown populated');
    },

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

                console.log('[Mapping] Credit block selected: %s.%s, credit paths: %d',
                    block.parent_path, block.repeat_element, Mapping.creditNodePaths.size);
            } catch (e) {
                console.error('[Mapping] Error parsing credit block selection:', e);
            }
        } else {
            document.getElementById('creditParentPath').value = '';
            document.getElementById('creditRepeatElement').value = '';
        }

        // Re-render tree to update credit node visual tagging
        if (Mapping.jsonTree) {
            Mapping.allJsonPaths = [];
            Mapping._renderJsonTree(Mapping.jsonTree);
            Mapping._populatePathSelects();
        }

        // Reclassify existing mappings
        Mapping._reclassifyMappings();
    },

    /**
     * Reclassify misplaced fields between debit fields[], top_level_fields[], and credit_block.fields[].
     *
     * - Fields in fields[] that belong to the credit block are moved to credit_block.fields[].
     * - Fields in fields[] that are NOT inside the batch container are moved to top_level_fields[].
     * - Fields in top_level_fields[] that ARE inside the batch container are moved to fields[].
     */
    _reclassifyMappings() {
        const type = Mapping.currentJsonType;
        const config = Mapping.mappingConfig[type];
        if (!config) return;
        if (!config.fields) config.fields = [];

        const creditParent = document.getElementById('creditParentPath').value;
        const creditRepeat = document.getElementById('creditRepeatElement').value;

        if (!config.credit_block) config.credit_block = { fields: [] };
        if (!config.credit_block.fields) config.credit_block.fields = [];
        if (!config.top_level_fields) config.top_level_fields = [];

        let changed = false;

        // 1. Move credit fields from fields[] to credit_block.fields[]
        if (creditParent && creditRepeat) {
            const creditPrefix = `${creditParent}.${creditRepeat}.`;
            const toCredit = [];
            config.fields = config.fields.filter(f => {
                const p = f.json_path || '';
                if (p.startsWith(creditPrefix)) {
                    f.json_path = p.substring(creditPrefix.length);
                    toCredit.push(f);
                    return false;
                }
                if (Mapping.creditNodePaths.has(p)) {
                    toCredit.push(f);
                    return false;
                }
                return true;
            });
            toCredit.forEach(f => config.credit_block.fields.push(f));
            if (toCredit.length > 0) {
                console.log('[Mapping] Reclassified %d fields from debit to credit:', toCredit.length, toCredit);
                changed = true;
            }
        }

        // 2. Reclassify between fields[] and top_level_fields[] based on batch container
        const batchContainer = Mapping._getBatchContainer();
        if (batchContainer) {
            // Helper: check if a cleaned path belongs inside the batch container.
            // After _cleanMappingPath strips root_key and batch_container prefix,
            // batch-level fields have paths like "DebitAccounts.DebitAccount.C6021"
            // while top-level fields have paths like "MessageId".
            // Since _cleanMappingPath already strips the batch container prefix from
            // batch-level paths, we need to check against the tree to determine level.
            // A simpler heuristic: if we can find this path under the batch container
            // in the tree, it's batch-level. Otherwise it's top-level.
            const isBatchLevelPath = (jsonPath) => {
                if (!Mapping.jsonTree) return true; // default to batch if no tree
                const rootKey = Mapping.jsonTree.key;
                // Try to find the node under root.batchContainer
                const batchFullPath = `${rootKey}.${batchContainer}`;
                const batchNode = Mapping._findNodeByPath(Mapping.jsonTree, batchFullPath);
                if (!batchNode || !batchNode.children) return true;
                // For array nodes, parse_json_to_tree flattens the first element's
                // children directly under the array node. So batchNode.children ARE
                // the batch element's children (DebitAccounts, CreditAccounts, CorporateId, etc.)
                const firstSeg = jsonPath.split('.')[0];
                return batchNode.children.some(c => c.key === firstSeg);
            };

            // Move top-level fields from fields[] to top_level_fields[]
            const toTopLevel = [];
            config.fields = config.fields.filter(f => {
                const p = f.json_path || '';
                if (!isBatchLevelPath(p)) {
                    toTopLevel.push(f);
                    return false;
                }
                return true;
            });
            toTopLevel.forEach(f => config.top_level_fields.push(f));
            if (toTopLevel.length > 0) {
                console.log('[Mapping] Reclassified %d fields from debit to top-level:', toTopLevel.length, toTopLevel);
                changed = true;
            }

            // Move batch-level fields from top_level_fields[] back to fields[]
            const toBatch = [];
            config.top_level_fields = config.top_level_fields.filter(f => {
                const p = f.json_path || '';
                if (isBatchLevelPath(p)) {
                    toBatch.push(f);
                    return false;
                }
                return true;
            });
            toBatch.forEach(f => config.fields.push(f));
            if (toBatch.length > 0) {
                console.log('[Mapping] Reclassified %d fields from top-level to debit:', toBatch.length, toBatch);
                changed = true;
            }
        }

        if (changed) {
            Mapping._renderMappingsList();
        }
    },

    /**
     * Build the set of paths that belong inside the credit block.
     */
    _buildCreditNodePaths(parentPath, repeatElement) {
        if (!Mapping.jsonTree) return;

        // Find the array node in the tree by path
        const fullPath = parentPath ? `${parentPath}.${repeatElement}` : repeatElement;
        const arrayNode = Mapping._findNodeByPath(Mapping.jsonTree, fullPath);

        if (!arrayNode || !arrayNode.children || arrayNode.children.length === 0) {
            console.warn('[Mapping] Could not find credit array node at path:', fullPath);
            return;
        }

        // Collect all leaf paths from the first representative element
        Mapping._collectCreditPaths(arrayNode.children, '');
        console.log('[Mapping] Credit node paths built:', [...Mapping.creditNodePaths]);
    },

    _findNodeByPath(node, targetPath) {
        if (node.path === targetPath) return node;
        if (node.children) {
            for (const child of node.children) {
                const found = Mapping._findNodeByPath(child, targetPath);
                if (found) return found;
            }
        }
        return null;
    },

    /**
     * Recursively collect all leaf paths from credit block children.
     */
    _collectCreditPaths(children, relativePath) {
        if (!children) return;
        children.forEach(child => {
            // For array index keys like "[0]", don't add a dot separator
            const separator = child.key.startsWith('[') ? '' : '.';
            const childRelPath = relativePath ? `${relativePath}${separator}${child.key}` : child.key;

            if (child.value !== undefined && (!child.children || child.children.length === 0)) {
                // Leaf node
                Mapping.creditNodePaths.add(childRelPath);
            }
            if (child.children && child.children.length > 0) {
                Mapping._collectCreditPaths(child.children, childRelPath);
            }
        });
    },

    _buildLeftPanel() {
        const container = document.getElementById('leftPanelItems');
        const titleEl = document.getElementById('leftPanelTitle');

        if (Mapping.currentJsonType === 'request') {
            titleEl.textContent = 'Excel Columns';
            container.innerHTML = Mapping.EXCEL_COLUMNS.map(col => `
                <div class="excel-column-item" data-source="${col}" onclick="Mapping.selectSource('${col}')">
                    ${col}
                </div>
            `).join('');

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
        document.querySelectorAll('.excel-column-item').forEach(el => {
            el.classList.toggle('selected', el.dataset.source === source);
        });
        console.log('[Mapping] Selected source:', source);
    },

    selectJsonNode(path, isCreditNode, isTopLevel = false) {
        if (!Mapping.selectedSource) {
            alert('First select a field from the left panel, then click a JSON node.');
            return;
        }

        const levelLabel = isCreditNode ? '(CREDIT)' : isTopLevel ? '(TOP-LEVEL)' : '(DEBIT)';
        console.log('[Mapping] Creating mapping:', Mapping.selectedSource, '->', path, levelLabel);

        const source = Mapping.selectedSource;

        if (source === '__auto__') {
            const prefix = prompt('Enter prefix (e.g., BATCH, CREDIT):', '');
            const length = parseInt(prompt('Enter total length:', '12')) || 12;
            const type = prompt('Type: numeric or alphanumeric', 'alphanumeric');

            Mapping._addMapping({
                source: 'auto',
                json_path: path,
                auto_generate: { type, prefix: prefix || '', length }
            }, isCreditNode, isTopLevel);
        } else if (source === '__hardcoded__') {
            const defaultValue = Mapping._getJsonTreeValue(path) || '';
            const value = prompt('Enter hardcoded value:', defaultValue);
            if (value !== null) {
                Mapping._addMapping({
                    source: 'hardcoded',
                    json_path: path,
                    value: value
                }, isCreditNode, isTopLevel);
            }
        } else if (source === '__filename__') {
            Mapping._addMapping({
                source: 'filename',
                json_path: path
            }, isCreditNode, isTopLevel);
        } else if (source === '__credit_ref_copy__') {
            Mapping._addMapping({
                source: 'credit_ref_copy',
                json_path: path
            }, isCreditNode, isTopLevel);
        } else if (source === '__batch_ref_copy__') {
            Mapping._addMapping({
                source: 'batch_ref_copy',
                json_path: path
            }, isCreditNode, isTopLevel);
        } else if (Mapping.currentJsonType === 'request') {
            Mapping._addMapping({
                source: 'excel',
                excel_column: source,
                json_path: path
            }, isCreditNode, isTopLevel);
        } else {
            Mapping._addMapping({
                json_path: path,
                map_to: source
            }, isCreditNode, isTopLevel);
        }

        Mapping.selectedSource = null;
        document.querySelectorAll('.excel-column-item').forEach(el => el.classList.remove('selected'));
        Mapping._renderMappingsList();
    },

    _addMapping(mapping, isCreditNode, isTopLevel = false) {
        const type = Mapping.currentJsonType;
        if (!Mapping.mappingConfig[type]) {
            Mapping.mappingConfig[type] = { fields: [], credit_block: { fields: [] }, top_level_fields: [] };
        }

        const config = Mapping.mappingConfig[type];

        if (isCreditNode) {
            if (!config.credit_block) config.credit_block = { fields: [] };
            if (!config.credit_block.fields) config.credit_block.fields = [];
            config.credit_block.fields.push(mapping);
            console.log('[Mapping] Added as CREDIT field:', mapping);
        } else if (isTopLevel) {
            if (!config.top_level_fields) config.top_level_fields = [];
            config.top_level_fields.push(mapping);
            console.log('[Mapping] Added as TOP-LEVEL field:', mapping);
        } else {
            if (!config.fields) config.fields = [];
            config.fields.push(mapping);
            console.log('[Mapping] Added as DEBIT field:', mapping);
        }

        // Mark JSON node as mapped
        document.querySelectorAll('.json-node-item').forEach(el => {
            if (el.dataset.path === mapping.json_path) {
                el.classList.add('mapped');
            }
        });
    },

    removeMapping(type, fieldType, index) {
        const config = Mapping.mappingConfig[type];
        if (!config) return;

        if (fieldType === true || fieldType === 'credit') {
            config.credit_block.fields.splice(index, 1);
        } else if (fieldType === 'top_level') {
            if (config.top_level_fields) config.top_level_fields.splice(index, 1);
        } else {
            config.fields.splice(index, 1);
        }
        Mapping._renderMappingsList();
    },

    _renderMappingsList() {
        const container = document.getElementById('mappingsList');
        const type = Mapping.currentJsonType;
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
                    <code>${f.json_path}</code>
                    <button class="remove-btn" onclick="Mapping.removeMapping('${type}', false, ${idx})">x</button>
                </div>
            `;
        });

        // Top-level (root) fields
        const topFields = config.top_level_fields || [];
        if (topFields.length) {
            html += '<div style="margin-top:8px;font-size:12px;font-weight:600;color:#8e44ad">Root-Level Fields:</div>';
            topFields.forEach((f, idx) => {
                const left = f.source === 'auto' ? `[Auto: ${f.auto_generate?.prefix || ''}]` :
                             f.source === 'hardcoded' ? `[Fixed: ${f.value}]` :
                             f.source === 'filename' ? '[Filename]' :
                             f.source === 'credit_ref_copy' ? '[CreditRef Copy]' :
                             f.source === 'batch_ref_copy' ? '[BatchRef Copy]' :
                             f.excel_column || f.map_to || '?';
                html += `
                    <div class="mapping-item" style="border-left:3px solid #8e44ad">
                        <strong>${left}</strong>
                        <span class="arrow">-></span>
                        <code>${f.json_path}</code>
                        <button class="remove-btn" onclick="Mapping.removeMapping('${type}', 'top_level', ${idx})">x</button>
                    </div>
                `;
            });
        }

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
                        <code>${f.json_path}</code>
                        <button class="remove-btn" onclick="Mapping.removeMapping('${type}', 'credit', ${idx})">x</button>
                    </div>
                `;
            });
        }

        container.innerHTML = html || '<span class="text-muted">No mappings configured yet</span>';
    },

    /**
     * Render the JSON tree with credit nodes visually tagged.
     * Uses the tree structure from parse_json_to_tree backend response.
     */
    _renderJsonTree(node, depth = 0, insideCreditBlock = false, creditRelativePath = '', insideBatchContainer = false) {
        const container = document.getElementById('jsonTreeContainer');
        if (depth === 0) {
            container.innerHTML = '';
            Mapping.allJsonPaths = [];
        }

        const creditParent = document.getElementById('creditParentPath').value;
        const creditRepeat = document.getElementById('creditRepeatElement').value;
        const creditFullPath = creditParent ? `${creditParent}.${creditRepeat}` : creditRepeat;

        // Check if this node is the credit array
        const isCreditArray = creditRepeat && node.path === creditFullPath;

        // Check if we're entering the parent of the credit array
        const isParentOfCredit = creditParent && node.path === creditParent;
        const childInsideCredit = insideCreditBlock || isCreditArray;

        // Check if this node is the batch container (e.g., "BatchDetails" array)
        const batchContainer = Mapping._getBatchContainer();
        const isThisBatchContainer = batchContainer && node.key === batchContainer && !insideBatchContainer;
        const childInsideBatch = insideBatchContainer || isThisBatchContainer;

        // Build credit-relative path
        let currentCreditRelativePath = '';
        if (insideCreditBlock && !isCreditArray) {
            // For array index keys like "[0]", don't add a dot separator
            const sep = node.key.startsWith('[') ? '' : '.';
            currentCreditRelativePath = creditRelativePath
                ? `${creditRelativePath}${sep}${node.key}`
                : node.key;
        }

        const el = document.createElement('div');
        el.style.paddingLeft = (depth * 20) + 'px';

        // Node label
        const labelSpan = document.createElement('span');
        labelSpan.className = 'xml-element';

        if (node.type === 'object') {
            labelSpan.textContent = `${node.key} {`;
            if (isCreditArray) {
                labelSpan.innerHTML = `${node.key} [] <span style="color:#28a745;font-size:11px;font-weight:600">(Credit Block - ${node.item_count || ''} items)</span>`;
                labelSpan.style.color = '#28a745';
            } else if (node.type === 'array') {
                labelSpan.textContent = `${node.key} [${node.item_count || ''}]`;
            }
        } else if (node.type === 'array') {
            if (isCreditArray) {
                labelSpan.innerHTML = `${node.key} [] <span style="color:#28a745;font-size:11px;font-weight:600">(Credit Block - ${node.item_count || ''} items)</span>`;
                labelSpan.style.color = '#28a745';
            } else {
                labelSpan.textContent = `${node.key} [${node.item_count || ''}]`;
            }
        } else {
            // Leaf node — clickable for mapping
            const isInsideCredit = insideCreditBlock && !isCreditArray;
            const mappingPath = isInsideCredit ? currentCreditRelativePath : node.path;

            // Remove the root key prefix from the path for mapping
            const cleanPath = Mapping._cleanMappingPath(mappingPath, isInsideCredit);
            Mapping.allJsonPaths.push(cleanPath);

            // Determine if this is a top-level field (outside batch container, not credit)
            const isTopLevel = !isInsideCredit && !insideBatchContainer && depth > 0;

            const creditStyle = isInsideCredit ? 'border-left:2px solid #28a745;padding-left:4px;' : '';
            const topLevelStyle = isTopLevel ? 'border-left:2px solid #8e44ad;padding-left:4px;' : '';
            const nodeStyle = creditStyle || topLevelStyle;
            const creditLabel = isInsideCredit ? '<span style="color:#28a745;font-size:10px"> [credit]</span>' : '';
            const topLevelLabel = isTopLevel ? '<span style="color:#8e44ad;font-size:10px"> [root]</span>' : '';
            const extraLabel = creditLabel || topLevelLabel;

            const escapedPath = cleanPath.replace(/'/g, "\\'");
            el.innerHTML = `
                <span class="xml-node-item json-node-item" data-path="${cleanPath}"
                      style="${nodeStyle}color:#0d6efd;cursor:pointer;"
                      onclick="Mapping.selectJsonNode('${escapedPath}', ${isInsideCredit}, ${isTopLevel})">
                    ${node.key}: <span class="node-type">"${Mapping._truncate(node.value || '', 40)}"</span>
                    ${extraLabel}
                </span>
            `;
            container.appendChild(el);
            return;
        }

        el.appendChild(labelSpan);
        container.appendChild(el);

        // Render children
        if (node.children) {
            node.children.forEach(child => {
                Mapping._renderJsonTree(child, depth + 1, childInsideCredit || isParentOfCredit, currentCreditRelativePath, childInsideBatch);
            });
        }

        // Closing brace
        if (node.type === 'object' || node.type === 'array') {
            const closeEl = document.createElement('div');
            closeEl.style.paddingLeft = (depth * 20) + 'px';
            closeEl.innerHTML = `<span class="xml-element">${node.type === 'array' ? '' : ''}</span>`;
            // Don't add closing braces to keep tree compact
        }
    },

    /**
     * Clean a mapping path by removing the root key and batch container prefixes.
     * e.g., "Payments.BatchDetails.DebitAccounts.DebitAccount.C6021"
     *    -> "DebitAccounts.DebitAccount.C6021" (relative to batch object)
     *
     * The generator places fields relative to the batch object, so we must
     * strip both the root_key ("Payments") and batch_container ("BatchDetails").
     */
    _cleanMappingPath(fullPath, isCredit) {
        if (isCredit) return fullPath; // Credit paths are already relative

        // Remove root key prefix if tree has single root
        if (Mapping.jsonTree && Mapping.jsonTree.key !== '(root)') {
            const rootKey = Mapping.jsonTree.key;
            if (fullPath.startsWith(rootKey + '.')) {
                fullPath = fullPath.substring(rootKey.length + 1);
            }

            // Strip batch container prefix (e.g., "BatchDetails.")
            // The batch container is the array that holds transaction batches.
            const batchContainer = Mapping._getBatchContainer();
            if (batchContainer && fullPath.startsWith(batchContainer + '.')) {
                fullPath = fullPath.substring(batchContainer.length + 1);
            }
        }

        return fullPath;
    },

    /**
     * Get the current batch container name — from the dropdown or auto-detect.
     */
    _getBatchContainer() {
        const select = document.getElementById('batchContainerSelect');
        if (select && select.value) return select.value;
        return Mapping._detectBatchContainer();
    },

    /**
     * Auto-detect batch container: the first array child of the root object.
     */
    _detectBatchContainer() {
        if (Mapping.jsonTree && Mapping.jsonTree.children) {
            const arrayChild = Mapping.jsonTree.children.find(c => c.type === 'array');
            if (arrayChild) return arrayChild.key;
        }
        return '';
    },

    _getJsonTreeValue(path) {
        if (!Mapping.jsonTree || !path) return '';

        // Try to find the node by searching the tree
        const node = Mapping._findNodeByPath(Mapping.jsonTree, path);
        if (node && node.value !== undefined) return node.value;

        // Try with root key prefix
        if (Mapping.jsonTree.key !== '(root)') {
            const fullPath = `${Mapping.jsonTree.key}.${path}`;
            const node2 = Mapping._findNodeByPath(Mapping.jsonTree, fullPath);
            if (node2 && node2.value !== undefined) return node2.value;
        }

        return '';
    },

    _populatePathSelects() {
        const paths = Mapping.allJsonPaths;
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
        const type = Mapping.currentJsonType;
        const config = Mapping.mappingConfig[type] || {};

        // Load credit block config
        const primaryBlock = (config.repeating_blocks || []).find(b => b.name === 'credits') || {};
        const cb = config.credit_block || primaryBlock;
        document.getElementById('creditParentPath').value = cb.parent_path || '';
        document.getElementById('creditRepeatElement').value = cb.repeat_element || '';

        const select = document.getElementById('creditBlockSelect');
        if (cb.parent_path && cb.repeat_element) {
            const label = `${cb.parent_path}.${cb.repeat_element} (saved)`;
            const value = JSON.stringify({ parent_path: cb.parent_path, repeat_element: cb.repeat_element });
            select.innerHTML = `<option value="">-- Upload JSON to detect --</option>
                <option value='${value}' selected>${label}</option>`;
            Mapping.creditNodePaths = new Set();
            if (Mapping.jsonTree) {
                Mapping._buildCreditNodePaths(cb.parent_path, cb.repeat_element);
            }
        } else {
            select.innerHTML = '<option value="">-- Upload JSON to detect --</option>';
        }

        // Load multi-batch flag and batch container (request only)
        if (type === 'request') {
            document.getElementById('isMultiBatch').checked = config.is_multi_batch || false;

            const batchContainerSelect = document.getElementById('batchContainerSelect');
            if (config.batch_container) {
                // Ensure option exists in dropdown
                let found = false;
                for (const opt of batchContainerSelect.options) {
                    if (opt.value === config.batch_container) {
                        found = true;
                        break;
                    }
                }
                if (!found) {
                    batchContainerSelect.innerHTML += `<option value="${config.batch_container}">${config.batch_container} (saved)</option>`;
                }
                batchContainerSelect.value = config.batch_container;
            }
        }

        // Load filename pattern (request only)
        if (type === 'request') {
            const fp = Mapping.filenamePattern || {};
            document.getElementById('filenamePrefix').value = fp.prefix || '';
            document.getElementById('filenameDateFormat').value = fp.date_format || '';
        }

        // Load special fields
        document.getElementById('batchRefSelect').value = config.batch_reference_field || '';
        document.getElementById('creditRefSelect').value = config.credit_reference_field || '';

        if (type !== 'request') {
            // Load success indicator for response type
            if (type === 'response' && Mapping.isResponseSplit) {
                document.getElementById('successIndicatorPath').value = config.success_indicator_path || '';
                document.getElementById('successIndicatorValue').value = config.success_indicator_value || '';
            }

            document.getElementById('debitStatusSelect').value = config.debit_status_field || '';
            document.getElementById('creditStatusSelect').value = config.credit_status_field || '';

            const dsv = config.debit_status_values || {};
            document.getElementById('debitSuccessCodes').value = (dsv.success || []).join(',');
            document.getElementById('debitFailureCodes').value = (dsv.failure || []).join(',');
            document.getElementById('debitPendingCodes').value = (dsv.pending || []).join(',');

            const csv = config.credit_status_values || {};
            document.getElementById('creditSuccessCodes').value = (csv.success || []).join(',');
            document.getElementById('creditFailureCodes').value = (csv.failure || []).join(',');
            document.getElementById('creditPendingCodes').value = (csv.pending || []).join(',');
        }

        Mapping._renderMappingsList();
    },

    async saveMappingConfig() {
        if (!Mapping.currentSchemeId) return;

        const type = Mapping.currentJsonType;
        if (!Mapping.mappingConfig[type]) {
            Mapping.mappingConfig[type] = {};
        }

        const config = Mapping.mappingConfig[type];

        // Batch & credit reference
        config.batch_reference_field = document.getElementById('batchRefSelect').value;
        config.credit_reference_field = document.getElementById('creditRefSelect').value;

        // Filename pattern (request only)
        if (type === 'request') {
            Mapping.mappingConfig.filename_pattern = {
                prefix: document.getElementById('filenamePrefix').value.trim(),
                date_format: document.getElementById('filenameDateFormat').value.trim()
            };
        }

        // Credit block config
        if (!config.credit_block) config.credit_block = { fields: [] };
        config.credit_block.parent_path = document.getElementById('creditParentPath').value;
        config.credit_block.repeat_element = document.getElementById('creditRepeatElement').value;

        // Build repeating_blocks array — strip root_key and batch_container from parent_path
        // so the backend gets paths relative to the batch object.
        let creditParentPath = config.credit_block.parent_path || '';
        {
            const rootKey = config.root_key || (Mapping.jsonTree ? Mapping.jsonTree.key : '');
            const batchContainer = config.batch_container || Mapping._getBatchContainer();
            const fullPrefix = [rootKey, batchContainer].filter(Boolean).join('.');
            if (fullPrefix && creditParentPath.startsWith(fullPrefix + '.')) {
                creditParentPath = creditParentPath.substring(fullPrefix.length + 1);
            } else if (fullPrefix && creditParentPath === fullPrefix) {
                creditParentPath = '';
            }
        }

        const primaryBlock = {
            name: 'credits',
            parent_path: creditParentPath,
            repeat_element: config.credit_block.repeat_element,
            fields: config.credit_block.fields || []
        };
        const additionalBlocks = (config.repeating_blocks || []).filter(b => b.name !== 'credits');
        config.repeating_blocks = [primaryBlock, ...additionalBlocks].filter(b => b.repeat_element);

        // Multi-batch flag and batch container (request only)
        if (type === 'request') {
            config.is_multi_batch = document.getElementById('isMultiBatch').checked;

            // Batch container: use dropdown selection, or auto-detect
            const batchContainerSelect = document.getElementById('batchContainerSelect');
            if (batchContainerSelect.value) {
                config.batch_container = batchContainerSelect.value;
            }
        }

        // Root key from tree
        if (Mapping.jsonTree) {
            config.root_key = Mapping.jsonTree.key !== '(root)' ? Mapping.jsonTree.key : '';

            // Auto-detect batch container if not explicitly set
            if (!config.batch_container && Mapping.jsonTree.children) {
                const arrayChild = Mapping.jsonTree.children.find(c => c.type === 'array');
                if (arrayChild) {
                    config.batch_container = arrayChild.key;
                }
            }
        }

        // Success indicator (response only, when split is enabled)
        if (type === 'response' && Mapping.isResponseSplit) {
            config.success_indicator_path = document.getElementById('successIndicatorPath').value.trim();
            config.success_indicator_value = document.getElementById('successIndicatorValue').value.trim();
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
