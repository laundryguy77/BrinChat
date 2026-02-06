/**
 * Knowledge Base Manager for BrinChat
 * Handles document upload, listing, and deletion
 */
class KnowledgeManager {
    constructor() {
        this.documents = [];
        this.stats = { document_count: 0, chunk_count: 0 };
    }

    /**
     * Initialize knowledge base UI and event listeners
     */
    init() {
        this.setupEventListeners();
        this.loadStats();
        this.loadDocuments();
    }

    /**
     * Setup event listeners for knowledge base UI
     */
    setupEventListeners() {
        const uploadArea = document.getElementById('kb-upload-area');
        const fileInput = document.getElementById('kb-file-input');

        if (uploadArea && fileInput) {
            // Click to upload
            uploadArea.addEventListener('click', () => fileInput.click());

            // File input change
            fileInput.addEventListener('change', (e) => {
                if (e.target.files.length > 0) {
                    this.uploadFiles(Array.from(e.target.files));
                    e.target.value = '';
                }
            });

            // Drag and drop
            uploadArea.addEventListener('dragover', (e) => {
                e.preventDefault();
                uploadArea.classList.add('border-primary', 'bg-primary/10');
            });

            uploadArea.addEventListener('dragleave', (e) => {
                e.preventDefault();
                uploadArea.classList.remove('border-primary', 'bg-primary/10');
            });

            uploadArea.addEventListener('drop', (e) => {
                e.preventDefault();
                uploadArea.classList.remove('border-primary', 'bg-primary/10');
                const files = Array.from(e.dataTransfer.files);
                this.uploadFiles(files);
            });
        }
    }

    /**
     * Load knowledge base statistics
     */
    async loadStats() {
        try {
            const response = await fetch('/api/knowledge/stats', {
                credentials: 'include'
            });

            if (response.ok) {
                this.stats = await response.json();
                this.updateStatsDisplay();
            }
        } catch (error) {
            console.error('Failed to load KB stats:', error);
        }
    }

    /**
     * Update stats display
     */
    updateStatsDisplay() {
        const docCount = document.getElementById('kb-doc-count');
        const chunkCount = document.getElementById('kb-chunk-count');

        if (docCount) docCount.textContent = this.stats.document_count;
        if (chunkCount) chunkCount.textContent = this.stats.chunk_count;
    }

    /**
     * Load document list
     */
    async loadDocuments() {
        try {
            const response = await fetch('/api/knowledge/documents', {
                credentials: 'include'
            });

            if (response.ok) {
                this.documents = await response.json();
                this.renderDocuments();
            }
        } catch (error) {
            console.error('Failed to load documents:', error);
        }
    }

    /**
     * Render document list
     */
    renderDocuments() {
        const container = document.getElementById('kb-documents');
        if (!container) return;

        if (this.documents.length === 0) {
            container.innerHTML = `
                <div class="text-center text-gray-500 text-sm py-4">
                    No documents uploaded yet
                </div>
            `;
            return;
        }

        container.innerHTML = this.documents.map(doc => `
            <div class="flex items-center gap-3 p-3 bg-background-dark rounded-lg group" data-id="${doc.id}">
                <span class="material-symbols-outlined text-primary">${this.getFileIcon(doc.file_type)}</span>
                <div class="flex-1 min-w-0">
                    <div class="text-sm text-white truncate" title="${doc.filename}">${doc.filename}</div>
                    <div class="text-xs text-gray-500">${doc.chunk_count} chunks</div>
                </div>
                <button class="kb-delete-btn text-gray-500 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-all" data-id="${doc.id}">
                    <span class="material-symbols-outlined text-sm">delete</span>
                </button>
            </div>
        `).join('');

        // Add delete handlers
        container.querySelectorAll('.kb-delete-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.deleteDocument(btn.dataset.id);
            });
        });
    }

    /**
     * Get icon for file type
     */
    getFileIcon(fileType) {
        const icons = {
            'pdf': 'picture_as_pdf',
            'text': 'description',
            'code': 'code'
        };
        return icons[fileType] || 'description';
    }

    /**
     * Upload a single file with retry logic
     */
    async uploadFileWithRetry(file, maxRetries = 3) {
        let lastError = null;

        for (let attempt = 1; attempt <= maxRetries; attempt++) {
            try {
                const formData = new FormData();
                formData.append('file', file);

                const response = await fetch('/api/knowledge/upload', {
                    method: 'POST',
                    credentials: 'include',
                    body: formData
                });

                const result = await response.json();

                if (response.ok) {
                    return { file: file.name, success: true, result };
                }

                // Don't retry on duplicate (not an error)
                if (result.status === 'duplicate') {
                    return { file: file.name, success: true, result };
                }

                lastError = result;
                console.warn(`Upload attempt ${attempt}/${maxRetries} failed for ${file.name}:`, result);

            } catch (error) {
                lastError = error;
                console.warn(`Upload attempt ${attempt}/${maxRetries} error for ${file.name}:`, error);
            }

            // Wait before retry (exponential backoff: 1s, 2s, 4s)
            if (attempt < maxRetries) {
                await new Promise(resolve => setTimeout(resolve, 1000 * Math.pow(2, attempt - 1)));
            }
        }

        console.error(`Upload failed after ${maxRetries} attempts for ${file.name}`);
        return { file: file.name, success: false, error: lastError };
    }

    /**
     * Upload files to knowledge base
     */
    async uploadFiles(files) {
        const progressDiv = document.getElementById('kb-upload-progress');
        const statusSpan = document.getElementById('kb-upload-status');

        if (progressDiv) progressDiv.classList.remove('hidden');
        if (statusSpan) statusSpan.textContent = `Uploading ${files.length} file(s)...`;

        // Filter oversized files first
        const validFiles = files.filter(file => {
            if (file.size > 150 * 1024 * 1024) {
                alert(`File "${file.name}" exceeds 150MB limit`);
                return false;
            }
            return true;
        });

        // Upload files sequentially to avoid overwhelming the server
        const results = [];
        for (let i = 0; i < validFiles.length; i++) {
            const file = validFiles[i];
            if (statusSpan) statusSpan.textContent = `Uploading ${i + 1}/${validFiles.length}: ${file.name}`;
            const result = await this.uploadFileWithRetry(file);
            results.push(result);
        }

        // Report failures
        const failures = results.filter(r => !r.success);
        if (failures.length > 0) {
            alert(`Failed to upload: ${failures.map(f => f.file).join(', ')}`);
        }

        if (progressDiv) progressDiv.classList.add('hidden');

        // Refresh stats and document list
        await this.loadStats();
        await this.loadDocuments();
    }

    /**
     * Delete a document
     */
    async deleteDocument(documentId) {
        if (!confirm('Delete this document from the knowledge base?')) return;

        try {
            const response = await fetch(`/api/knowledge/documents/${documentId}`, {
                method: 'DELETE',
                credentials: 'include'
            });

            if (response.ok) {
                await this.loadStats();
                await this.loadDocuments();
            } else {
                alert('Failed to delete document');
            }
        } catch (error) {
            console.error('Delete error:', error);
            alert('Failed to delete document');
        }
    }
}

// Global knowledge manager instance
const knowledgeManager = new KnowledgeManager();
