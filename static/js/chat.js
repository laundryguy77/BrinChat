// Chat Manager - Handles message display and streaming
export class ChatManager {
    constructor(app) {
        this.app = app;
        this.messageList = document.getElementById('message-list');
        this.chatContainer = document.getElementById('chat-container');
        this.isStreaming = false;
        this.currentAssistantMessage = null;
        this.editingMessageId = null;
        this.currentStreamContent = '';
        this.currentThinkingContent = '';
        this.thinkingContainer = null;
        this.currentToolCalls = [];

        // Status tracking
        this.modelStatus = 'idle'; // idle, thinking, generating, using_tool
        this.statusStartTime = null;
        this.statusTimer = null;
        this.abortController = null;

        // Auto-scroll control - pause when user scrolls up during streaming
        this.userIsScrolling = false;

        // Message queue - prevent message loss during generation
        this.messageQueue = [];
        this.isProcessingQueue = false;

        this.initializeMarkdown();
        this.initializeStatusBar();
        this.initializeScrollButton();
        this.initializeVisibilityHandler();

        // Track if last user message was voice input (for auto-TTS response)
        this.lastInputWasVoice = false;
    }
    
    /**
     * Mark that the next message is from voice input
     */
    setVoiceInput(isVoice) {
        this.lastInputWasVoice = isVoice;
    }

    /**
     * Initialize scroll-to-bottom button and user scroll detection
     */
    initializeScrollButton() {
        const scrollBtn = document.getElementById('scroll-to-bottom-btn');
        if (!scrollBtn) return;

        // Detect user scroll and control auto-scroll behavior
        this.chatContainer.addEventListener('scroll', () => {
            const isNearBottom = this.chatContainer.scrollHeight - this.chatContainer.scrollTop - this.chatContainer.clientHeight < 200;
            scrollBtn.classList.toggle('hidden', isNearBottom);

            // Pause auto-scroll if user scrolled away from bottom during streaming
            if (this.isStreaming) {
                if (!isNearBottom) {
                    this.userIsScrolling = true;
                } else {
                    // User scrolled back to bottom, resume auto-scroll
                    this.userIsScrolling = false;
                }
            }
        });

        // Click to scroll to bottom and resume auto-scroll
        scrollBtn.addEventListener('click', () => {
            this.userIsScrolling = false; // Resume auto-scroll
            this.scrollToBottom(true); // Force scroll
            scrollBtn.classList.add('hidden');
        });
    }

    /**
     * Handle page visibility changes (app switch, phone lock)
     * Reconnects stream if it was interrupted
     */
    initializeVisibilityHandler() {
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                console.log('[Chat] Page became visible, checking stream status');
                // If we were streaming and it got interrupted, the UI will show
                // Give user feedback that they may need to resend
                if (this.isStreaming && this.abortController) {
                    // Check if the stream is still alive by checking the abort signal
                    if (this.abortController.signal.aborted) {
                        console.log('[Chat] Stream was aborted while hidden');
                        this.handleStreamInterruption();
                    }
                }
            }
        });

        // Also handle page hide for cleanup
        window.addEventListener('pagehide', () => {
            if (this.isStreaming) {
                console.log('[Chat] Page hiding, stream may be interrupted');
            }
        });
    }

    /**
     * Handle stream interruption when user returns to app
     */
    handleStreamInterruption() {
        // Add a notice to the current message if one exists
        if (this.currentAssistantMessage && this.currentAssistantMessage.content) {
            const notice = document.createElement('div');
            notice.className = 'mt-2 p-2 bg-yellow-500/10 border border-yellow-500/30 rounded-lg text-yellow-400 text-sm flex items-center gap-2';
            notice.innerHTML = `
                <span class="material-symbols-outlined text-base">warning</span>
                <span>Connection interrupted. You may need to resend your message.</span>
            `;
            this.currentAssistantMessage.content.appendChild(notice);
        }
        this.stopGeneration();
    }

    /**
     * Initialize the status bar and stop button
     */
    initializeStatusBar() {
        const stopBtn = document.getElementById('stop-generation-btn');
        if (stopBtn) {
            stopBtn.addEventListener('click', () => this.stopGeneration());
        }
    }

    /**
     * Update the model status indicator
     * @param {string} status - 'idle', 'thinking', 'generating', 'using_tool'
     * @param {string} [toolName] - Name of the tool being used (for using_tool status)
     */
    updateModelStatus(status, toolName = null) {
        this.modelStatus = status;
        const statusBar = document.getElementById('model-status-bar');
        const statusIcon = document.getElementById('status-icon');
        const statusText = document.getElementById('status-text');
        const statusDuration = document.getElementById('status-duration');

        if (!statusBar) return;

        if (status === 'idle') {
            statusBar.classList.add('hidden');
            this.stopStatusTimer();
            return;
        }

        // Show the status bar
        statusBar.classList.remove('hidden');

        // Start timer if not already running
        if (!this.statusTimer) {
            this.statusStartTime = Date.now();
            this.statusTimer = setInterval(() => {
                if (statusDuration) {
                    const elapsed = Math.floor((Date.now() - this.statusStartTime) / 1000);
                    statusDuration.textContent = `${elapsed}s`;
                }
            }, 1000);
        }

        // Update icon and text based on status
        const statusConfig = {
            thinking: {
                icon: 'psychology',
                text: 'Thinking...',
                iconClass: 'text-purple-400 animate-pulse'
            },
            generating: {
                icon: 'edit_note',
                text: 'Generating response...',
                iconClass: 'text-primary animate-pulse'
            },
            using_tool: {
                icon: 'build',
                text: `Using ${toolName || 'tool'}...`,
                iconClass: 'text-green-400 animate-spin'
            }
        };

        const config = statusConfig[status] || statusConfig.generating;

        if (statusIcon) {
            statusIcon.textContent = config.icon;
            statusIcon.className = `material-symbols-outlined ${config.iconClass}`;
        }
        if (statusText) {
            statusText.textContent = config.text;
        }
    }

    /**
     * Stop the status timer
     */
    stopStatusTimer() {
        if (this.statusTimer) {
            clearInterval(this.statusTimer);
            this.statusTimer = null;
        }
        this.statusStartTime = null;
    }

    /**
     * Stop the current generation
     */
    async stopGeneration() {
        // First, try to cancel on server side (for long-running tools)
        const convId = this.app.currentConversationId;
        if (convId) {
            try {
                await fetch(`/api/chat/cancel/${convId}`, {
                    method: 'POST',
                    credentials: 'include',
                    headers: this.app.getSessionHeaders()
                });
                console.log('[Chat] Server-side cancellation requested');
            } catch (e) {
                console.warn('[Chat] Failed to send cancel request:', e);
            }
        }

        // Then abort the client-side fetch
        if (this.abortController) {
            this.abortController.abort();
            this.abortController = null;
        }

        // Add a note to the current message that it was stopped
        if (this.currentAssistantMessage && this.currentStreamContent) {
            this.appendToAssistantMessage('\n\n*[Generation stopped by user]*');
        }

        this.isStreaming = false;
        this.userIsScrolling = false; // Reset scroll control
        this.updateModelStatus('idle');

        const sendBtn = document.getElementById('send-btn');
        if (sendBtn) sendBtn.disabled = false;

        this.showToast('Generation stopped', 'info', 2000);

        // Process any queued messages
        this.processMessageQueue();
    }

    /**
     * Announce a message to screen readers via the live region.
     * @param {string} message - Message to announce
     */
    announceToScreenReader(message) {
        const srAnnouncements = document.getElementById('sr-announcements');
        if (srAnnouncements) {
            srAnnouncements.textContent = message;
            // Clear after announcement to allow repeated messages
            setTimeout(() => { srAnnouncements.textContent = ''; }, 1000);
        }
    }

    /**
     * Show a toast notification to the user
     * @param {string} message - The message to display
     * @param {string} type - 'error', 'success', 'warning', or 'info'
     * @param {number} duration - How long to show the toast (ms)
     */
    showToast(message, type = 'error', duration = 5000) {
        const colorMap = {
            error: 'bg-red-600',
            success: 'bg-green-600',
            warning: 'bg-yellow-600',
            info: 'bg-blue-600'
        };
        const iconMap = {
            error: 'error',
            success: 'check_circle',
            warning: 'warning',
            info: 'info'
        };

        const toast = document.createElement('div');
        toast.className = `fixed bottom-4 right-4 ${colorMap[type] || colorMap.error} text-white px-4 py-3 rounded-lg shadow-lg z-50 flex items-center gap-2 animate-fadeIn`;
        toast.innerHTML = `
            <span class="material-symbols-outlined text-lg">${iconMap[type] || iconMap.error}</span>
            <span>${message}</span>
        `;
        document.body.appendChild(toast);

        setTimeout(() => {
            toast.classList.add('opacity-0', 'transition-opacity', 'duration-300');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    }

    initializeMarkdown() {
        if (typeof marked !== 'undefined') {
            const renderer = new marked.Renderer();

            // Custom table renderer to wrap in scrollable div
            renderer.table = function(header, body) {
                return `<div class="overflow-x-auto my-4 rounded-lg border border-gray-700">
                    <table class="min-w-full text-sm text-left">${header}${body}</table>
                </div>`;
            };

            // Custom code renderer for inline code
            renderer.codespan = function(code) {
                return `<code class="px-1.5 py-0.5 bg-surface-dark text-orange-400 rounded text-sm font-mono">${code}</code>`;
            };

            marked.setOptions({
                renderer: renderer,
                highlight: function(code, lang) {
                    if (typeof hljs !== 'undefined' && lang && hljs.getLanguage(lang)) {
                        try {
                            return hljs.highlight(code, { language: lang }).value;
                        } catch (e) {}
                    }
                    return code;
                },
                breaks: true,
                gfm: true
            });
        }
    }

    renderMarkdown(content) {
        if (!content || typeof marked === 'undefined') return content || '';

        try {
            let html = marked.parse(content);

            // Sanitize HTML to prevent XSS attacks
            if (typeof DOMPurify !== 'undefined') {
                html = DOMPurify.sanitize(html, {
                    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'b', 'i', 'u', 'code', 'pre', 'ul', 'ol', 'li',
                                   'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'a', 'hr',
                                   'table', 'thead', 'tbody', 'tr', 'th', 'td', 'div', 'span', 'img',
                                   'del', 'ins', 'sub', 'sup', 'dl', 'dt', 'dd', 'figure', 'figcaption',
                                   'button'],
                    ALLOWED_ATTR: ['href', 'src', 'alt', 'class', 'target', 'rel', 'title', 'id',
                                   'onclick', 'style', 'width', 'height', 'colspan', 'rowspan'],
                    ALLOW_DATA_ATTR: false,
                    ADD_ATTR: ['target'],
                    FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'input', 'textarea'],
                    FORBID_ATTR: ['onerror', 'onload', 'onmouseover', 'onfocus', 'onblur']
                });
            }

            // Style code blocks with GitHub-dark theme
            html = html.replace(/<pre><code class="language-(\w+)">/g, (match, lang) => {
                return `<div class="rounded-xl overflow-hidden border border-gray-700 bg-[#0d1117] shadow-xl my-4">
                    <div class="flex items-center justify-between px-4 py-2.5 bg-[#161b22] border-b border-gray-700">
                        <span class="text-xs text-gray-400 font-mono">${lang}</span>
                        <button onclick="navigator.clipboard.writeText(this.closest('.rounded-xl').querySelector('code').textContent).then(()=>{this.innerHTML='<span class=\\'material-symbols-outlined text-sm\\'>check</span> Copied';setTimeout(()=>{this.innerHTML='<span class=\\'material-symbols-outlined text-sm\\'>content_copy</span> Copy'},1500)})" class="text-gray-400 hover:text-white transition-colors flex items-center gap-1 text-xs">
                            <span class="material-symbols-outlined text-sm">content_copy</span> Copy
                        </button>
                    </div>
                    <pre class="p-4 overflow-x-auto"><code class="language-${lang} text-sm font-mono leading-relaxed">`;
            });

            html = html.replace(/<pre><code>/g, `<div class="rounded-xl overflow-hidden border border-gray-700 bg-[#0d1117] shadow-xl my-4">
                <div class="flex items-center justify-between px-4 py-2.5 bg-[#161b22] border-b border-gray-700">
                    <span class="text-xs text-gray-400 font-mono">code</span>
                    <button onclick="navigator.clipboard.writeText(this.closest('.rounded-xl').querySelector('code').textContent).then(()=>{this.innerHTML='<span class=\\'material-symbols-outlined text-sm\\'>check</span> Copied';setTimeout(()=>{this.innerHTML='<span class=\\'material-symbols-outlined text-sm\\'>content_copy</span> Copy'},1500)})" class="text-gray-400 hover:text-white transition-colors flex items-center gap-1 text-xs">
                        <span class="material-symbols-outlined text-sm">content_copy</span> Copy
                    </button>
                </div>
                <pre class="p-4 overflow-x-auto"><code class="text-sm font-mono leading-relaxed">`);

            html = html.replace(/<\/code><\/pre>/g, '</code></pre></div>');

            // Style tables
            html = html.replace(/<thead>/g, '<thead class="bg-surface-darker text-gray-400 font-bold uppercase text-xs tracking-wider">');
            html = html.replace(/<th>/g, '<th class="px-4 py-3 border-b border-gray-700">');
            html = html.replace(/<td>/g, '<td class="px-4 py-3 text-gray-300">');
            html = html.replace(/<tr>/g, '<tr class="hover:bg-white/5 transition-colors border-b border-gray-700/50">');

            // Style blockquotes as info callouts
            html = html.replace(/<blockquote>/g, '<div class="flex gap-4 p-4 rounded-xl bg-primary/10 border border-primary/20 text-gray-200 my-4"><span class="material-symbols-outlined text-primary flex-shrink-0">info</span><div>');
            html = html.replace(/<\/blockquote>/g, '</div></div>');

            // Style links
            html = html.replace(/<a /g, '<a class="text-primary hover:underline" ');

            return html;
        } catch (e) {
            // Fallback to raw content if markdown parsing fails
            return content;
        }
    }

    /**
     * Create an expandable context section showing thinking, memories, and tools
     */
    createContextSection(metadata) {
        console.log('[Context] Creating context section with:', {
            hasThinking: !!metadata.thinking_content,
            thinkingLength: metadata.thinking_content?.length || 0,
            thinkingPreview: metadata.thinking_content?.substring(0, 100) || '(none)',
            memoriesCount: metadata.memories_used?.length || 0,
            memoriesExtracted: metadata.memories_extracted?.length || 0,
            toolsCount: metadata.tools_available?.length || 0,
            tools: metadata.tools_available
        });

        const section = document.createElement('details');
        section.className = 'context-section mt-3 border-t border-gray-700/50 pt-3';
        section.setAttribute('open', ''); // Expanded by default for debugging

        const summary = document.createElement('summary');
        summary.className = 'text-xs text-gray-500 cursor-pointer hover:text-gray-400 transition-colors flex items-center gap-1';

        // Count items to show in summary
        const items = [];
        if (metadata.thinking_content) items.push('reasoning');
        if (metadata.memories_used?.length) items.push(`${metadata.memories_used.length} memories`);
        if (metadata.memories_extracted?.length) items.push(`${metadata.memories_extracted.length} extracted`);
        if (metadata.tools_available?.length) items.push(`${metadata.tools_available.length} tools`);

        summary.innerHTML = `
            <span class="material-symbols-outlined text-sm">psychology</span>
            Context (${items.join(', ')})
        `;
        section.appendChild(summary);

        const content = document.createElement('div');
        content.className = 'mt-2 space-y-3 text-xs';

        // Thinking content (model's internal reasoning)
        if (metadata.thinking_content) {
            const thinkingDiv = document.createElement('div');
            thinkingDiv.className = 'p-3 rounded-lg bg-primary/10 border border-primary/20';
            thinkingDiv.innerHTML = `
                <div class="flex items-center gap-1 text-primary font-medium mb-1">
                    <span class="material-symbols-outlined text-sm">psychology</span>
                    Model Reasoning
                    <span class="text-[10px] text-gray-500 ml-1">(internal thought process)</span>
                </div>
                <div class="text-gray-400 max-h-48 overflow-y-auto whitespace-pre-wrap text-[11px] leading-relaxed">${this.escapeHtml(metadata.thinking_content)}</div>
            `;
            content.appendChild(thinkingDiv);
        }

        // Memories used
        if (metadata.memories_used?.length) {
            const memoriesDiv = document.createElement('div');
            memoriesDiv.className = 'p-3 rounded-lg bg-purple-500/10 border border-purple-500/20';
            memoriesDiv.innerHTML = `
                <div class="flex items-center gap-1 text-purple-400 font-medium mb-1">
                    <span class="material-symbols-outlined text-sm">memory</span>
                    Memories Used (${metadata.memories_used.length})
                </div>
                <div class="space-y-1 max-h-32 overflow-y-auto">
                    ${metadata.memories_used.map(m => `
                        <div class="text-gray-400">
                            <span class="text-purple-300">[${m.category || 'general'}]</span> ${this.escapeHtml(m.content || '')}
                        </div>
                    `).join('')}
                </div>
            `;
            content.appendChild(memoriesDiv);
        }

        // Tools available
        if (metadata.tools_available?.length) {
            const toolsDiv = document.createElement('div');
            toolsDiv.className = 'p-3 rounded-lg bg-green-500/10 border border-green-500/20';
            toolsDiv.innerHTML = `
                <div class="flex items-center gap-1 text-green-400 font-medium mb-1">
                    <span class="material-symbols-outlined text-sm">build</span>
                    Tools Available
                </div>
                <div class="flex flex-wrap gap-1">
                    ${metadata.tools_available.map(t => `
                        <span class="px-1.5 py-0.5 bg-green-500/20 text-green-300 rounded text-[10px]">${t}</span>
                    `).join('')}
                </div>
            `;
            content.appendChild(toolsDiv);
        }

        // Memories extracted from response (via [MEMORY] tags)
        if (metadata.memories_extracted?.length) {
            const extractedDiv = document.createElement('div');
            extractedDiv.className = 'p-3 rounded-lg bg-amber-500/10 border border-amber-500/20';
            extractedDiv.innerHTML = `
                <div class="flex items-center gap-1 text-amber-400 font-medium mb-1">
                    <span class="material-symbols-outlined text-sm">bookmark_add</span>
                    Memories Saved (${metadata.memories_extracted.length})
                    <span class="text-[10px] text-gray-500 ml-1">(extracted from response)</span>
                </div>
                <div class="space-y-1 max-h-32 overflow-y-auto">
                    ${metadata.memories_extracted.map(m => `
                        <div class="text-gray-400">
                            <span class="text-amber-300">[${m.category || 'general'}]</span>
                            <span class="text-gray-500 text-[10px]">importance:${m.importance || 5}</span>
                            ${this.escapeHtml(m.content || '')}
                        </div>
                    `).join('')}
                </div>
            `;
            content.appendChild(extractedDiv);
        }

        section.appendChild(content);
        return section;
    }

    /**
     * Escape HTML to prevent XSS in user/model content
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    createMessageElement(role, content, images = [], messageId = null, files = [], metadata = {}) {
        const isUser = role === 'user';
        const message = document.createElement('div');
        message.className = `flex gap-4 md:gap-6 animate-fadeIn group ${isUser ? 'flex-row-reverse' : ''}`;
        if (messageId) message.dataset.messageId = messageId;

        // Avatar
        const avatar = document.createElement('div');
        if (isUser) {
            avatar.className = 'size-8 md:size-10 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center flex-shrink-0 mt-1 shadow-md';
            avatar.innerHTML = '<span class="material-symbols-outlined text-white text-lg">person</span>';
        } else {
            avatar.className = 'size-8 md:size-10 rounded-xl bg-gradient-to-br from-gray-800 to-gray-900 border border-gray-700 flex items-center justify-center flex-shrink-0 mt-1 shadow-md';
            avatar.innerHTML = '<span class="material-symbols-outlined text-primary text-xl">smart_toy</span>';
        }

        // Bubble container
        const bubbleContainer = document.createElement('div');
        bubbleContainer.className = `max-w-[85%] space-y-2 ${isUser ? '' : 'flex-1 min-w-0'}`;

        // Assistant header
        if (!isUser) {
            const assistantName = this.app.getAssistantName();
            const header = document.createElement('div');
            header.className = 'flex items-center gap-2 mb-1 assistant-header';
            header.innerHTML = `
                <span class="font-bold text-gray-200 text-sm assistant-name">${assistantName}</span>
                <span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-gray-800 text-gray-400 border border-gray-700">AI</span>
            `;
            bubbleContainer.appendChild(header);
        }

        // Message bubble
        const bubble = document.createElement('div');
        if (isUser) {
            bubble.className = 'rounded-2xl rounded-tr-sm bg-primary p-4 text-white shadow-lg shadow-blue-900/10';
        } else {
            bubble.className = 'space-y-4';
        }

        // Render files if any
        if (files && files.length > 0) {
            const fileList = document.createElement('div');
            fileList.className = 'flex flex-wrap gap-2 mb-3';
            const icons = { pdf: 'picture_as_pdf', zip: 'folder_zip', text: 'description', code: 'code' };
            files.forEach(f => {
                const fileEl = document.createElement('div');
                fileEl.className = 'flex items-center gap-2 px-3 py-2 bg-surface-dark/50 border border-gray-700 rounded-lg text-sm';
                fileEl.innerHTML = `
                    <span class="material-symbols-outlined text-primary text-lg">${icons[f.type] || icons.text}</span>
                    <span class="text-gray-300">${f.name}</span>
                `;
                fileList.appendChild(fileEl);
            });
            bubble.appendChild(fileList);
        }

        // Render images if any
        if (images && images.length > 0) {
            const imageGrid = document.createElement('div');
            imageGrid.className = 'flex flex-wrap gap-2 mb-3';
            images.forEach(img => {
                const imgEl = document.createElement('img');
                imgEl.src = img.startsWith('data:') ? img : `data:image/jpeg;base64,${img}`;
                imgEl.className = 'max-w-[200px] max-h-[200px] rounded-lg object-cover';
                imageGrid.appendChild(imgEl);
            });
            bubble.appendChild(imageGrid);
        }

        // Content
        const contentEl = document.createElement('div');
        if (isUser) {
            contentEl.className = 'leading-relaxed whitespace-pre-wrap';
            contentEl.textContent = content;
        } else {
            contentEl.className = 'prose prose-invert prose-sm max-w-none text-gray-300 leading-relaxed';
            contentEl.innerHTML = this.renderMarkdown(content);
        }
        bubble.appendChild(contentEl);

        // Context section for assistant messages (thinking, memories, tools)
        if (!isUser && (metadata.thinking_content || metadata.memories_used || metadata.tools_available)) {
            const contextSection = this.createContextSection(metadata);
            bubble.appendChild(contextSection);
        }

        bubbleContainer.appendChild(bubble);

        // Timestamp
        const timestamp = document.createElement('div');
        timestamp.className = `text-xs text-gray-500 font-medium mt-1 ${isUser ? 'text-right pr-1' : 'pl-1'}`;
        timestamp.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        bubbleContainer.appendChild(timestamp);

        // Action buttons
        const actions = document.createElement('div');
        actions.className = `flex gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity ${isUser ? 'justify-end' : 'justify-start'}`;

        // Store content for closure
        const messageContent = content;
        const msgId = messageId;
        const chatManager = this;

        if (isUser) {
            const copyBtn = document.createElement('button');
            copyBtn.className = 'p-1.5 text-gray-500 hover:text-white hover:bg-white/10 rounded-lg transition-all';
            copyBtn.title = 'Copy';
            copyBtn.innerHTML = '<span class="material-symbols-outlined text-sm">content_copy</span>';
            copyBtn.onclick = (e) => {
                e.preventDefault();
                e.stopPropagation();
                chatManager.copyToClipboard(messageContent, copyBtn);
            };
            actions.appendChild(copyBtn);

            if (msgId) {
                const editBtn = document.createElement('button');
                editBtn.className = 'p-1.5 text-gray-500 hover:text-white hover:bg-white/10 rounded-lg transition-all';
                editBtn.title = 'Edit';
                editBtn.innerHTML = '<span class="material-symbols-outlined text-sm">edit</span>';
                editBtn.onclick = (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    chatManager.showEditModal(msgId, messageContent);
                };
                actions.appendChild(editBtn);
            }
        } else {
            const copyBtn = document.createElement('button');
            copyBtn.className = 'p-1.5 text-gray-500 hover:text-white hover:bg-white/10 rounded-lg transition-all';
            copyBtn.title = 'Copy';
            copyBtn.innerHTML = '<span class="material-symbols-outlined text-sm">content_copy</span>';
            copyBtn.onclick = (e) => {
                e.preventDefault();
                e.stopPropagation();
                // For assistant messages, copy the rendered text content, not raw markdown
                const textToCopy = contentEl.textContent || messageContent;
                console.log('[Copy] Copying assistant message rendered text, length:', textToCopy.length);
                chatManager.copyToClipboard(textToCopy, copyBtn);
            };
            actions.appendChild(copyBtn);

            // Speaker button for TTS
            const speakBtn = document.createElement('button');
            speakBtn.className = 'p-1.5 text-gray-500 hover:text-white hover:bg-white/10 rounded-lg transition-all speak-btn';
            speakBtn.title = 'Read aloud';
            speakBtn.innerHTML = '<span class="material-symbols-outlined text-sm">volume_up</span>';
            speakBtn.onclick = async (e) => {
                e.preventDefault();
                e.stopPropagation();
                const textToSpeak = contentEl.textContent || messageContent;
                if (chatManager.app.voiceManager) {
                    // Update button to show playing state
                    speakBtn.innerHTML = '<span class="material-symbols-outlined text-sm animate-pulse">volume_up</span>';
                    speakBtn.classList.add('text-primary');
                    try {
                        // Force=true bypasses voice_mode check for manual speak requests
                        await chatManager.app.voiceManager.speakText(textToSpeak, true);
                    } finally {
                        speakBtn.innerHTML = '<span class="material-symbols-outlined text-sm">volume_up</span>';
                        speakBtn.classList.remove('text-primary');
                    }
                } else {
                    console.log('[TTS] Voice manager not available');
                }
            };
            actions.appendChild(speakBtn);

            if (msgId) {
                const regenBtn = document.createElement('button');
                regenBtn.className = 'p-1.5 text-gray-500 hover:text-white hover:bg-white/10 rounded-lg transition-all';
                regenBtn.title = 'Regenerate';
                regenBtn.innerHTML = '<span class="material-symbols-outlined text-sm">refresh</span>';
                regenBtn.onclick = (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    chatManager.regenerateResponse(msgId);
                };
                actions.appendChild(regenBtn);
            }
        }
        bubbleContainer.appendChild(actions);

        message.appendChild(avatar);
        message.appendChild(bubbleContainer);

        return message;
    }

    createActionButton(icon, title, onClick) {
        const btn = document.createElement('button');
        btn.className = 'p-1.5 text-gray-500 hover:text-white hover:bg-white/10 rounded-lg transition-all';
        btn.title = title;
        btn.innerHTML = `<span class="material-symbols-outlined text-sm">${icon}</span>`;
        btn.addEventListener('click', onClick);
        return btn;
    }

    renderConversation(conv) {
        // Clear existing messages, but don't show the welcome splash unless the conversation is empty.
        this.clearMessages(false);

        if (!conv.messages || conv.messages.length === 0) {
            this.showWelcome();
            return;
        }

        conv.messages.forEach(msg => {
            // Build metadata object for context section
            const metadata = {
                thinking_content: msg.thinking_content,
                memories_used: msg.memories_used,
                tools_available: msg.tools_available
            };
            if (msg.role === 'assistant' && (metadata.thinking_content || metadata.memories_used || metadata.tools_available)) {
                console.log('[Render] Message has context metadata:', {
                    id: msg.id,
                    hasThinking: !!metadata.thinking_content,
                    memoriesCount: metadata.memories_used?.length || 0,
                    toolsCount: metadata.tools_available?.length || 0
                });
            }
            const msgEl = this.createMessageElement(msg.role, msg.content, msg.images || [], msg.id, msg.files, metadata);
            this.messageList.appendChild(msgEl);
        });

        this.scrollToBottom();
    }

    showWelcome() {
        const assistantName = this.app.getAssistantName();
        const welcome = document.createElement('div');
        welcome.className = 'welcome-message text-center py-16 space-y-4 animate-fadeIn';
        welcome.innerHTML = `
            <div class="size-16 rounded-2xl bg-gradient-to-br from-primary to-indigo-600 flex items-center justify-center mx-auto shadow-lg shadow-primary/30">
                <span class="material-symbols-outlined text-3xl text-white">smart_toy</span>
            </div>
            <h2 class="font-display font-bold text-2xl text-white">Welcome to ${assistantName}</h2>
            <p class="text-gray-400 max-w-md mx-auto">Chat with Brin — your assistant for research, debugging, planning, and getting things done.</p>
            <p class="text-gray-500 text-sm">Powered by OpenClaw.</p>
        `;
        this.messageList.appendChild(welcome);
    }

    clearMessages(showWelcome = true) {
        while (this.messageList.firstChild) {
            this.messageList.removeChild(this.messageList.firstChild);
        }
        if (showWelcome) {
            this.showWelcome();
        }
    }

    addMessage(role, content, images = [], messageId = null, files = [], metadata = {}) {
        const welcome = this.messageList.querySelector('.welcome-message');
        if (welcome) welcome.remove();

        const message = this.createMessageElement(role, content, images, messageId, files, metadata);
        this.messageList.appendChild(message);
        this.scrollToBottom();
        return message;
    }

    startAssistantMessage() {
        const welcome = this.messageList.querySelector('.welcome-message');
        if (welcome) welcome.remove();

        this.currentStreamContent = '';
        this.currentThinkingContent = '';
        this.thinkingContainer = null;
        this.currentToolCalls = [];

        const message = document.createElement('div');
        message.className = 'flex gap-4 md:gap-6 animate-fadeIn group';

        // Avatar
        const avatar = document.createElement('div');
        avatar.className = 'size-8 md:size-10 rounded-xl bg-gradient-to-br from-gray-800 to-gray-900 border border-gray-700 flex items-center justify-center flex-shrink-0 mt-1 shadow-md';
        avatar.innerHTML = '<span class="material-symbols-outlined text-primary text-xl">smart_toy</span>';

        // Bubble container
        const bubbleContainer = document.createElement('div');
        bubbleContainer.className = 'flex-1 min-w-0 space-y-2';

        // Header
        const assistantName = this.app.getAssistantName();
        const header = document.createElement('div');
        header.className = 'flex items-center gap-2 mb-1 assistant-header';
        header.innerHTML = `
            <span class="font-bold text-gray-200 text-sm assistant-name">${assistantName}</span>
            <span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-gray-800 text-gray-400 border border-gray-700">AI</span>
        `;
        bubbleContainer.appendChild(header);

        // Content area
        const content = document.createElement('div');
        content.className = 'prose prose-invert prose-sm max-w-none text-gray-300 leading-relaxed assistant-content';

        // Typing indicator
        content.innerHTML = `
            <div class="flex items-center gap-1.5 text-gray-500 typing-indicator">
                <div class="size-2 bg-gray-500 rounded-full typing-dot"></div>
                <div class="size-2 bg-gray-500 rounded-full typing-dot"></div>
                <div class="size-2 bg-gray-500 rounded-full typing-dot"></div>
            </div>
        `;

        bubbleContainer.appendChild(content);
        message.appendChild(avatar);
        message.appendChild(bubbleContainer);

        this.currentAssistantMessage = {
            element: message,
            content: '',
            contentEl: content,
            bubbleContainer: bubbleContainer
        };

        this.messageList.appendChild(message);
        this.scrollToBottom();

        return message;
    }

    appendToAssistantMessage(text) {
        if (!this.currentAssistantMessage) {
            console.warn('[Stream] appendToAssistantMessage called but no currentAssistantMessage');
            return;
        }

        const typing = this.currentAssistantMessage.contentEl.querySelector('.typing-indicator');
        if (typing) {
            console.log('[Stream] Removing typing indicator, starting content render');
            typing.remove();
        }

        this.currentStreamContent += text;
        console.log('[Stream] Total content now:', this.currentStreamContent.length, 'chars');
        this.currentAssistantMessage.contentEl.innerHTML = this.renderMarkdown(this.currentStreamContent);
        this.scrollToBottom();
    }

    appendThinkingContent(text) {
        if (!this.currentAssistantMessage) {
            console.warn('[Stream] appendThinkingContent called but no currentAssistantMessage');
            return;
        }

        const typing = this.currentAssistantMessage.contentEl.querySelector('.typing-indicator');
        if (typing) typing.remove();

        if (!this.thinkingContainer) {
            console.log('[Stream] Creating thinking container');
            this.thinkingContainer = document.createElement('div');
            this.thinkingContainer.className = 'mb-4';
            this.thinkingContainer.innerHTML = `
                <details open class="p-4 rounded-xl bg-primary/10 border border-primary/20 group">
                    <summary class="flex items-center gap-2 cursor-pointer text-primary text-sm font-medium list-none">
                        <span class="material-symbols-outlined text-lg animate-spin">psychology</span>
                        <span>Thinking...</span>
                        <span class="material-symbols-outlined text-gray-500 group-open:rotate-180 transition-transform ml-auto">expand_more</span>
                    </summary>
                    <div class="thinking-content mt-2 text-gray-400 text-sm max-h-48 overflow-y-auto whitespace-pre-wrap"></div>
                </details>
            `;
            this.currentAssistantMessage.contentEl.appendChild(this.thinkingContainer);
        }

        this.currentThinkingContent += text;
        console.log('[Stream] Thinking content now:', this.currentThinkingContent.length, 'chars');
        this.thinkingContainer.querySelector('.thinking-content').textContent = this.currentThinkingContent;
        this.scrollToBottom();
    }

    finishThinking() {
        if (!this.thinkingContainer) return;

        const details = this.thinkingContainer.querySelector('details');
        if (!details) return;

        const summary = details.querySelector('summary');
        if (summary) {
            // Update icon to stop spinning and change text
            summary.innerHTML = `
                <span class="material-symbols-outlined text-lg">psychology</span>
                <span>Thought process</span>
                <span class="material-symbols-outlined text-gray-500 group-open:rotate-180 transition-transform ml-auto">expand_more</span>
            `;
        }

        // Collapse by default after thinking is done
        details.removeAttribute('open');

        this.currentThinkingContent = '';
    }

    /**
     * Add a debug context frame showing what the model received
     * @param {Object} context - Context metadata from backend
     */
    appendContextDebugFrame(context) {
        if (!this.currentAssistantMessage) return;

        const debugFrame = document.createElement('div');
        debugFrame.className = 'mb-3 debug-context-frame';
        debugFrame.innerHTML = `
            <details class="p-3 rounded-lg bg-gray-800/50 border border-gray-700/50 text-xs group">
                <summary class="flex items-center gap-2 cursor-pointer text-gray-400 hover:text-gray-200 list-none">
                    <span class="material-symbols-outlined text-sm">bug_report</span>
                    <span>Context Debug</span>
                    <span class="ml-auto flex items-center gap-2 text-gray-500">
                        <span title="History messages">${context.history_count} msgs</span>
                        <span>•</span>
                        <span title="Available tools">${context.tool_count} tools</span>
                        <span>•</span>
                        <span title="Memories used">${context.memory_count} memories</span>
                        <span class="material-symbols-outlined text-xs group-open:rotate-180 transition-transform">expand_more</span>
                    </span>
                </summary>
                <div class="mt-3 space-y-3 text-gray-400">
                    <div>
                        <div class="font-semibold text-gray-300 mb-1">Model</div>
                        <div class="flex gap-2">
                            <span class="px-2 py-0.5 rounded bg-gray-700">${context.model}</span>
                            ${context.is_vision ? '<span class="px-2 py-0.5 rounded bg-blue-900/50 text-blue-300">Vision</span>' : ''}
                            ${context.supports_tools ? '<span class="px-2 py-0.5 rounded bg-green-900/50 text-green-300">Tools</span>' : ''}
                            ${context.think_mode ? '<span class="px-2 py-0.5 rounded bg-purple-900/50 text-purple-300">Think</span>' : ''}
                        </div>
                    </div>
                    ${context.history_count > 0 ? `
                    <div>
                        <div class="font-semibold text-gray-300 mb-1">Conversation History</div>
                        <div class="text-gray-400">${context.history_count} messages in context</div>
                    </div>
                    ` : `
                    <div>
                        <div class="font-semibold text-yellow-400 mb-1">⚠️ No History</div>
                        <div class="text-yellow-400/70">This is a new conversation or history failed to load</div>
                    </div>
                    `}
                    ${context.memory_count > 0 ? `
                    <div>
                        <div class="font-semibold text-gray-300 mb-1">Memories Used (${context.memory_count})</div>
                        <ul class="list-disc list-inside text-gray-400">
                            ${context.memories.map(m => `<li class="truncate">${this.escapeHtml(m)}</li>`).join('')}
                        </ul>
                    </div>
                    ` : ''}
                    ${context.tools && context.tools.length > 0 ? `
                    <div>
                        <div class="font-semibold text-gray-300 mb-1">Available Tools (${context.tool_count})</div>
                        <div class="flex flex-wrap gap-1">
                            ${context.tools.map(t => `<span class="px-1.5 py-0.5 rounded bg-gray-700/50 text-gray-400">${t}</span>`).join('')}
                            ${context.tool_count > 10 ? `<span class="px-1.5 py-0.5 rounded bg-gray-700/50 text-gray-500">+${context.tool_count - 10} more</span>` : ''}
                        </div>
                    </div>
                    ` : ''}
                    <div>
                        <div class="font-semibold text-gray-300 mb-1">System Prompt Preview</div>
                        <pre class="p-2 rounded bg-gray-900/50 overflow-x-auto whitespace-pre-wrap text-[10px] leading-relaxed max-h-32 overflow-y-auto">${this.escapeHtml(context.system_prompt_preview)}</pre>
                    </div>
                </div>
            </details>
        `;

        // Insert at the beginning of the bubble container (before content area)
        // This prevents it from being wiped when contentEl.innerHTML is replaced
        this.currentAssistantMessage.bubbleContainer.insertBefore(
            debugFrame,
            this.currentAssistantMessage.bubbleContainer.firstChild
        );
    }

    /**
     * Escape HTML to prevent XSS in debug output
     */
    escapeHtml(text) {
        if (!text) return '';
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    addToolIndicator(toolName, status = 'processing') {
        if (!this.currentAssistantMessage) return null;

        const indicator = document.createElement('div');
        indicator.className = 'flex items-center gap-2 p-3 rounded-lg bg-surface-dark border border-gray-700 my-3 text-sm';

        if (status === 'processing') {
            indicator.innerHTML = `
                <span class="material-symbols-outlined text-primary animate-spin">sync</span>
                <span class="text-gray-300">Using <strong class="text-white">${toolName}</strong>...</span>
            `;
        }

        this.currentAssistantMessage.contentEl.appendChild(indicator);
        this.scrollToBottom();
        return indicator;
    }

    updateToolIndicator(indicator, status, message) {
        if (!indicator) return;

        if (status === 'complete') {
            indicator.innerHTML = `
                <span class="material-symbols-outlined text-green-500">check_circle</span>
                <span class="text-gray-300">${message}</span>
            `;
        } else if (status === 'error') {
            indicator.innerHTML = `
                <span class="material-symbols-outlined text-red-500">error</span>
                <span class="text-gray-300">${message}</span>
            `;
        }
    }

    renderToolLog() {
        if (!this.currentAssistantMessage || this.currentToolCalls.length === 0) return;

        const toolLog = document.createElement('details');
        toolLog.className = 'tool-log mt-3 border-t border-gray-700 pt-3';

        const summary = document.createElement('summary');
        summary.className = 'text-xs text-gray-500 cursor-pointer hover:text-gray-400 transition-colors';
        summary.textContent = `Tool activity (${this.currentToolCalls.length} call${this.currentToolCalls.length > 1 ? 's' : ''})`;
        toolLog.appendChild(summary);

        const content = document.createElement('div');
        content.className = 'mt-2 space-y-2 text-xs';

        this.currentToolCalls.forEach(call => {
            const entry = document.createElement('div');
            entry.className = 'tool-entry p-2 bg-surface-dark rounded';

            const timeStr = call.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            const statusIcon = call.status === 'complete' ? 'check_circle' : call.status === 'error' ? 'error' : 'pending';
            const statusColor = call.status === 'complete' ? 'text-green-500' : call.status === 'error' ? 'text-red-500' : 'text-yellow-500';

            // Truncate and format arguments for display
            let argsDisplay = '';
            try {
                const args = typeof call.arguments === 'string' ? JSON.parse(call.arguments) : call.arguments;
                const argsStr = Object.entries(args).map(([k, v]) => {
                    const val = typeof v === 'string' ? v : JSON.stringify(v);
                    return `${k}: "${val.length > 50 ? val.substring(0, 50) + '...' : val}"`;
                }).join(', ');
                argsDisplay = argsStr.length > 100 ? argsStr.substring(0, 100) + '...' : argsStr;
            } catch (e) {
                argsDisplay = String(call.arguments).substring(0, 100);
            }

            entry.innerHTML = `
                <div class="flex justify-between items-start">
                    <span class="text-primary font-medium">${call.name}</span>
                    <span class="text-gray-500">${timeStr}</span>
                </div>
                <div class="text-gray-400 truncate mt-1">${argsDisplay}</div>
                <div class="flex items-center gap-1 mt-1 ${statusColor}">
                    <span class="material-symbols-outlined text-sm">${statusIcon}</span>
                    <span>${call.statusMessage || call.status}</span>
                </div>
            `;

            // Add expandable raw result if available
            if (call.result) {
                const resultDetails = document.createElement('details');
                resultDetails.className = 'mt-1';
                resultDetails.innerHTML = `
                    <summary class="text-gray-500 cursor-pointer hover:text-gray-400">Show raw result</summary>
                    <pre class="mt-1 p-2 bg-black/30 rounded text-gray-400 overflow-x-auto max-h-32 text-[10px]">${JSON.stringify(call.result, null, 2)}</pre>
                `;
                entry.appendChild(resultDetails);
            }

            content.appendChild(entry);
        });

        toolLog.appendChild(content);
        this.currentAssistantMessage.contentEl.appendChild(toolLog);
    }

    addAssistantActions(messageId) {
        if (!this.currentAssistantMessage) return;

        // Render tool log before actions
        this.renderToolLog();

        const actions = document.createElement('div');
        actions.className = 'flex gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity';

        // Store for closure
        const contentEl = this.currentAssistantMessage.contentEl;
        const msgId = messageId;
        const chatManager = this;

        const copyBtn = document.createElement('button');
        copyBtn.className = 'p-1.5 text-gray-500 hover:text-white hover:bg-white/10 rounded-lg transition-all';
        copyBtn.title = 'Copy';
        copyBtn.innerHTML = '<span class="material-symbols-outlined text-sm">content_copy</span>';
        copyBtn.onclick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            // Copy the rendered text content, not raw markdown
            const textToCopy = contentEl.textContent || '';
            console.log('[Copy] Copying streamed message rendered text, length:', textToCopy.length);
            chatManager.copyToClipboard(textToCopy, copyBtn);
        };

        const regenBtn = document.createElement('button');
        regenBtn.className = 'p-1.5 text-gray-500 hover:text-white hover:bg-white/10 rounded-lg transition-all';
        regenBtn.title = 'Regenerate';
        regenBtn.innerHTML = '<span class="material-symbols-outlined text-sm">refresh</span>';
        regenBtn.onclick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            chatManager.regenerateResponse(msgId);
        };

        actions.appendChild(copyBtn);

        // Speaker button for TTS (streamed messages)
        const speakBtn = document.createElement('button');
        speakBtn.className = 'p-1.5 text-gray-500 hover:text-white hover:bg-white/10 rounded-lg transition-all speak-btn';
        speakBtn.title = 'Read aloud';
        speakBtn.innerHTML = '<span class="material-symbols-outlined text-sm">volume_up</span>';
        speakBtn.onclick = async (e) => {
            e.preventDefault();
            e.stopPropagation();
            const textToSpeak = contentEl.textContent || '';
            if (chatManager.app.voiceManager) {
                speakBtn.innerHTML = '<span class="material-symbols-outlined text-sm animate-pulse">volume_up</span>';
                speakBtn.classList.add('text-primary');
                try {
                    await chatManager.app.voiceManager.speakText(textToSpeak, true);
                } finally {
                    speakBtn.innerHTML = '<span class="material-symbols-outlined text-sm">volume_up</span>';
                    speakBtn.classList.remove('text-primary');
                }
            }
        };
        actions.appendChild(speakBtn);

        actions.appendChild(regenBtn);
        this.currentAssistantMessage.bubbleContainer.appendChild(actions);
    }

    scrollToBottom(force = false) {
        // Skip auto-scroll if user is manually scrolling (unless forced)
        if (this.userIsScrolling && !force) {
            return;
        }
        this.chatContainer.scrollTop = this.chatContainer.scrollHeight;
    }

    copyToClipboard(text, button) {
        if (!text) {
            return;
        }
        navigator.clipboard.writeText(text).then(() => {
            if (button) {
                const icon = button.querySelector('.material-symbols-outlined');
                if (icon) {
                    const original = icon.textContent;
                    icon.textContent = 'check';
                    button.classList.add('text-green-500');
                    setTimeout(() => {
                        icon.textContent = original;
                        button.classList.remove('text-green-500');
                    }, 1500);
                }
            }
        }).catch(() => {
            // Fallback for older browsers or HTTP contexts
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            try {
                document.execCommand('copy');
            } catch (e) {
                // Silent fail - clipboard operations may not be available
            }
            document.body.removeChild(textarea);
        });
    }

    async regenerateResponse(messageId) {
        if (this.isStreaming) {
            return;
        }

        const convId = this.app.currentConversationId;
        if (!convId) {
            return;
        }

        // Create abort controller for this request
        this.abortController = new AbortController();
        this.updateModelStatus('generating');

        try {
            const response = await fetch(`/api/chat/conversations/${convId}/regenerate/${messageId}`, {
                method: 'POST',
                headers: this.app.getSessionHeaders(),  // Include session ID for adult content gating
                credentials: 'include',
                signal: this.abortController.signal
            });
            if (!response.ok) throw new Error('Failed to regenerate');

            this.startAssistantMessage();
            this.isStreaming = true;
            // Keep send enabled: regenerate can run while user queues the next message.
            const sendBtn = document.getElementById('send-btn');
            if (sendBtn) sendBtn.disabled = false;

            await this.handleSSEStream(response);
            await this.app.loadConversation(convId);
        } catch (error) {
            if (error.name === 'AbortError') {
                console.log('[Regenerate] Aborted by user');
            } else {
                console.error('[Regenerate] Failed to regenerate response:', error);
                this.showToast('Failed to regenerate response. Please try again.', 'error');
                // Remove the typing indicator if it's showing
                if (this.currentAssistantMessage) {
                    const typing = this.currentAssistantMessage.contentEl?.querySelector('.typing-indicator');
                    if (typing) typing.remove();
                    this.currentAssistantMessage.contentEl.innerHTML = '<span class="text-red-400">Failed to regenerate response.</span>';
                }
            }
        } finally {
            this.isStreaming = false;
            this.currentAssistantMessage = null;
            this.abortController = null;
            this.updateModelStatus('idle');
            const sendBtn = document.getElementById('send-btn');
            if (sendBtn) sendBtn.disabled = false;
        }
    }

    showEditModal(messageId, content) {
        this.editingMessageId = messageId;
        const modal = document.getElementById('edit-modal');
        const textarea = document.getElementById('edit-content');
        textarea.value = content;
        modal.classList.remove('hidden');
        textarea.focus();
    }

    async saveEdit() {
        const modal = document.getElementById('edit-modal');
        const textarea = document.getElementById('edit-content');
        const newContent = textarea.value.trim();
        const editType = document.querySelector('input[name="edit-type"]:checked').value;

        if (!newContent || !this.editingMessageId) {
            modal.classList.add('hidden');
            return;
        }

        const convId = this.app.currentConversationId;
        if (!convId) {
            modal.classList.add('hidden');
            return;
        }

        try {
            if (editType === 'fork') {
                const response = await fetch(`/api/chat/conversations/${convId}/messages/${this.editingMessageId}/fork`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({ content: newContent })
                });

                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.detail || `Fork failed with status ${response.status}`);
                }

                const data = await response.json();
                if (data.id) {
                    await this.app.loadConversation(data.id);
                    await this.app.loadConversations();
                    this.showToast('Message forked successfully', 'success', 3000);
                } else {
                    throw new Error('Fork response missing conversation ID');
                }
            } else {
                const response = await fetch(`/api/chat/conversations/${convId}/messages/${this.editingMessageId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({ content: newContent })
                });

                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.detail || `Edit failed with status ${response.status}`);
                }

                await this.app.loadConversation(convId);
                this.showToast('Message updated', 'success', 3000);
            }
        } catch (error) {
            console.error('[Edit] Failed to save edit:', error);
            this.showToast(`Failed to save: ${error.message}`, 'error');
            return; // Don't close modal on error so user can retry
        }

        modal.classList.add('hidden');
        this.editingMessageId = null;
    }

    async sendMessage(text, images = [], imageDataUrls = [], think = false, files = []) {
        // Queue message if a response is currently in-flight.
        // We key off BOTH isStreaming and abortController to avoid accidental parallel streams.
        if (this.isStreaming || (this.abortController && !this.abortController.signal.aborted)) {
            this.messageQueue.push({ text, images, imageDataUrls, think, files });
            const queuePos = this.messageQueue.length;
            console.log(`[Chat] Message queued (position ${queuePos}):`, text.substring(0, 50));
            this.showToast(`Message queued (${queuePos} waiting)`, 'info', 2000);
            return;
        }

        // Check for slash commands before sending
        if (text.startsWith('/')) {
            const handled = await this.handleCommand(text);
            if (handled) return;
        }

        this.addMessage('user', text, imageDataUrls, null, files);
        this.startAssistantMessage();
        this.isStreaming = true;
        this.totalStreamTokens = 0;
        
        // Announce to screen readers
        this.announceToScreenReader('Message sent. Waiting for response.');

        // Create abort controller for this request
        this.abortController = new AbortController();

        // Update status to show we're starting
        this.updateModelStatus(think ? 'thinking' : 'generating');

        // Keep send enabled: if user sends again while streaming, we queue it.
        const sendBtn = document.getElementById('send-btn');
        if (sendBtn) sendBtn.disabled = false;

        try {
            const headers = {
                'Content-Type': 'application/json',
                ...this.app.getSessionHeaders()  // Include session ID for adult content gating
            };
            if (this.app.currentConversationId) {
                headers['X-Conversation-ID'] = this.app.currentConversationId;
            }

            const requestBody = {
                message: text,
                images: images.length > 0 ? images : undefined,
                think: think || undefined,
                files: files.length > 0 ? files : undefined
            };
            console.log('[Chat] Sending request - images:', images.length, 'image sizes:', images.map(i => i?.length || 0));

            const response = await fetch('/api/chat', {
                method: 'POST',
                headers,
                credentials: 'include',
                signal: this.abortController.signal,
                body: JSON.stringify(requestBody)
            });

            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            await this.handleSSEStream(response);
        } catch (error) {
            if (error.name === 'AbortError') {
                console.log('[Chat] Generation aborted by user');
            } else {
                this.appendToAssistantMessage(`Error: ${error.message}`);
            }
        } finally {
            this.isStreaming = false;
            this.userIsScrolling = false; // Reset scroll control
            this.currentAssistantMessage = null;
            this.abortController = null;
            this.updateModelStatus('idle');
            if (sendBtn) sendBtn.disabled = false;

            // Process any queued messages
            this.processMessageQueue();
        }
    }

    /**
     * Process queued messages after streaming completes
     */
    async processMessageQueue() {
        if (this.isProcessingQueue || this.messageQueue.length === 0) {
            return;
        }

        this.isProcessingQueue = true;

        while (this.messageQueue.length > 0 && !this.isStreaming) {
            const msg = this.messageQueue.shift();
            console.log(`[Chat] Processing queued message (${this.messageQueue.length} remaining):`, msg.text.substring(0, 50));

            // Small delay to let UI settle
            await new Promise(resolve => setTimeout(resolve, 100));

            // Send the queued message
            await this.sendMessage(msg.text, msg.images, msg.imageDataUrls, msg.think, msg.files);
        }

        this.isProcessingQueue = false;
    }

    async handleSSEStream(response) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let toolIndicator = null;
        let lastActivityTime = Date.now();
        const STREAM_TIMEOUT = 120000; // 2 minutes max idle time

        try {
            while (true) {
                // Check for stream timeout (no data received)
                if (Date.now() - lastActivityTime > STREAM_TIMEOUT) {
                    console.warn('[SSE] Stream timeout - no data received for 2 minutes');
                    this.showToast('Response timed out. The server may be overloaded.', 'warning');
                    break;
                }

                const { done, value } = await reader.read();
                if (done) break;

                lastActivityTime = Date.now(); // Reset activity timer
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (line.startsWith('event:')) {
                        console.log('[SSE] Event line:', line);
                        continue;
                    }
                    if (line.startsWith('data:')) {
                        const dataStr = line.slice(5).trim();
                        if (!dataStr) continue;
                        try {
                            const data = JSON.parse(dataStr);
                            console.log('[SSE] Parsed data:', Object.keys(data));
                            toolIndicator = await this.handleSSEEvent(data, toolIndicator);
                        } catch (e) {
                            // Log malformed SSE data for debugging but continue processing
                            console.warn('[SSE] Malformed SSE data, skipping:', dataStr.substring(0, 100), e);
                            // Don't let one bad event break the entire stream
                        }
                    }
                }
            }

            // Log when SSE stream completes
            console.log('[SSE] Stream completed');
        } catch (error) {
            // Handle network errors during streaming
            if (error.name === 'TypeError' && error.message.includes('network')) {
                console.error('[SSE] Network error during stream:', error);
                this.showToast('Connection lost. Please check your network and try again.', 'error');
            } else if (error.name !== 'AbortError') {
                console.error('[SSE] Stream error:', error);
                this.showToast('Stream interrupted. Please try again.', 'error');
            }
            throw error; // Re-throw to be handled by sendMessage
        } finally {
            // Ensure reader is released
            try {
                reader.releaseLock();
            } catch (e) {
                // Reader may already be released
            }
        }
    }

    async handleSSEEvent(data, toolIndicator) {
        // Conversation ID
        if (data.id !== undefined && data.id && !data.role) {
            this.app.setCurrentConversation(data.id);
        }

        // Debug context metadata
        if (data.system_prompt_length !== undefined) {
            this.currentContext = data;
            this.appendContextDebugFrame(data);
        }

        // Thinking tokens
        if (data.thinking !== undefined) {
            console.log('[SSE] Thinking token received:', data.thinking.length, 'chars');
            this.updateModelStatus('thinking');
            this.appendThinkingContent(data.thinking);
        }

        // Thinking done
        if (data.thinking_done) {
            this.updateModelStatus('generating');
            this.finishThinking();
        }

        // Content tokens
        if (data.content !== undefined) {
            console.log('[SSE] Content token received:', data.content.length, 'chars, preview:', data.content.substring(0, 50));
            this.updateModelStatus('generating');
            this.appendToAssistantMessage(data.content);

            // Update context usage estimate
            this.totalStreamTokens = (this.totalStreamTokens || 0) + Math.ceil(data.content.length / 4);
            const maxTokens = this.app.currentModelContextWindow || 4096;
            this.app.updateContextUsage(this.totalStreamTokens, maxTokens);
        }

        // Tool call
        if (data.name && data.arguments !== undefined) {
            this.updateModelStatus('using_tool', data.name);
            toolIndicator = this.addToolIndicator(data.name, 'processing');
            // Track tool call for log
            this.currentToolCalls.push({
                name: data.name,
                arguments: data.arguments,
                timestamp: new Date(),
                status: 'processing',
                result: null
            });
        }

        // Tool result
        if (data.result !== undefined) {
            // Switch back to generating after tool completes
            this.updateModelStatus('generating');
            const status = data.result.success ? 'complete' : 'error';
            let message = data.result.success ? 'Complete' : data.result.error;

            // If toolIndicator is null (events out of order), log warning but continue processing
            if (!toolIndicator) {
                console.warn('[SSE] Tool result received without matching tool indicator');
            }

            if (data.name === 'web_search') {
                message = data.result.success ? `Found ${data.result.num_results} results` : data.result.error;
            } else if (data.name === 'search_conversations') {
                message = data.result.success ? `Found ${data.result.num_results} results` : data.result.error;
            } else if (data.name === 'set_conversation_title') {
                if (data.result.success) {
                    message = `Title: "${data.result.title}"`;
                    // Update the UI with the new title
                    document.getElementById('current-chat-title').textContent = data.result.title;
                    // Refresh the conversation list to show the new title (fire and forget, but log errors)
                    this.app.loadConversations().catch(err => {
                        console.warn('[SSE] Failed to refresh conversation list after title update:', err);
                    });
                } else {
                    message = data.result.error || 'Failed to set title';
                }
            } else if (data.name === 'generate_image' || data.name === 'text_to_image' || data.name === 'image') {
                // Handle consolidated 'image' tool and legacy tool names
                if (data.result.success && data.result.base64) {
                    message = 'Image generated';
                    // Display the image inline after the tool indicator
                    this.displayGeneratedImage(data.result.base64, data.result.mime_type || 'image/png');
                } else {
                    message = data.result.error || 'Image generation failed';
                }
            } else if (data.name === 'text_to_video' || data.name === 'image_to_video' || data.name === 'video') {
                if (data.result.success && data.result.base64) {
                    message = 'Video generated';
                    // Display the video inline after the tool indicator
                    this.displayGeneratedVideo(data.result.base64, data.result.mime_type || 'video/mp4');
                } else if (data.result.success && data.result.video_url) {
                    message = 'Video generated';
                    // Display video from URL
                    this.displayGeneratedVideoUrl(data.result.video_url);
                } else {
                    message = data.result.error || 'Video generation failed';
                }
            }
            this.updateToolIndicator(toolIndicator, status, message);

            // Update tool call in log
            const lastCall = this.currentToolCalls[this.currentToolCalls.length - 1];
            if (lastCall) {
                lastCall.status = status;
                lastCall.statusMessage = message;
                lastCall.result = data.result;
            }
        }

        // Content replacement (replaces displayed content after stripping [MEMORY] tags)
        if (data.replace_content !== undefined) {
            console.log('[SSE] Content replacement received, stripping memory tags from display');
            if (this.currentAssistantMessage?.bubbleContainer) {
                const bubble = this.currentAssistantMessage.bubbleContainer.querySelector('.rounded-2xl .prose');
                if (bubble) {
                    // Replace with cleaned content (memory tags removed)
                    bubble.innerHTML = this.renderMarkdown(data.replace_content);
                    // Update tracked content for voice/other features
                    this.currentStreamContent = data.replace_content;
                }
            }
        }

        // Message ID for actions and context section
        if (data.role === 'assistant' && data.id) {
            if (this.currentAssistantMessage) {
                this.currentAssistantMessage.element.dataset.messageId = data.id;
                this.addAssistantActions(data.id);

                // Add context section with metadata if available
                if (data.metadata) {
                    const metadata = {
                        thinking_content: data.metadata.thinking_content || this.currentThinkingContent,
                        memories_used: data.metadata.memories_used,
                        tools_available: data.metadata.tools_available,
                        memories_extracted: data.metadata.memories_extracted
                    };

                    // Only create context section if we have any metadata
                    if (metadata.thinking_content || metadata.memories_used?.length || metadata.tools_available?.length || metadata.memories_extracted?.length) {
                        // Remove the streaming thinking container if present (will be replaced by context section)
                        if (this.thinkingContainer) {
                            this.thinkingContainer.remove();
                            this.thinkingContainer = null;
                        }

                        // Find the bubble container to add context section
                        const bubbleContainer = this.currentAssistantMessage.bubbleContainer;
                        if (bubbleContainer) {
                            const bubble = bubbleContainer.querySelector('.rounded-2xl');
                            if (bubble) {
                                const contextSection = this.createContextSection(metadata);
                                bubble.appendChild(contextSection);
                            }
                        }
                    }
                }
            }
        }

        // Done
        if (data.finish_reason !== undefined) {
            console.log('[Chat] finish_reason received:', data.finish_reason);
            // Stream completed - refresh VRAM gauge
            this.app.updateUsageGauges();
            
            // Announce to screen readers
            this.announceToScreenReader('Response received.');

            // Voice integration
            console.log('[Chat] Voice check: voiceManager=', !!this.app.voiceManager, 'content=', this.currentStreamContent?.length, 'lastInputWasVoice=', this.lastInputWasVoice);
            if (this.app.voiceManager && this.currentStreamContent) {
                // Callback for conversation mode (voice-to-voice)
                if (this.onResponseComplete) {
                    this.onResponseComplete(this.currentStreamContent);
                    this.onResponseComplete = null;
                }
                
                // Auto-speak the response if:
                // 1. Global auto_play is enabled, OR
                // 2. The last input was voice (voice-in = voice-out)
                if (this.lastInputWasVoice) {
                    console.log('[Chat] Voice input detected, auto-speaking response (forced TTS)');
                    this.app.voiceManager.speakText(this.currentStreamContent, true);
                    this.lastInputWasVoice = false; // Reset for next message
                } else {
                    console.log('[Chat] No voice input, calling autoSpeak');
                    this.app.voiceManager.autoSpeak(this.currentStreamContent);
                }
            } else {
                console.log('[Chat] TTS skipped - voiceManager:', !!this.app.voiceManager, 'content:', !!this.currentStreamContent);
            }
        }

        // Error
        if (data.message !== undefined && data.content === undefined && !data.cancelled) {
            this.appendToAssistantMessage(`\n\nError: ${data.message}`);
        }

        // Cancelled by user
        if (data.cancelled || (data.message && data.message.includes('cancelled'))) {
            this.updateModelStatus('idle');
            this.appendToAssistantMessage('\n\n*[Generation cancelled by user]*');
            if (toolIndicator) {
                this.updateToolIndicator(toolIndicator, 'cancelled', 'Cancelled');
            }
        }

        return toolIndicator;
    }

    async clearHistory() {
        await this.app.createNewConversation();
    }

    // Command handling methods
    async handleCommand(text) {
        const parts = text.trim().split(/\s+/);
        const command = parts[0].toLowerCase();
        const args = parts.slice(1);

        // Handle /full_unlock command with enable/disable actions
        if (command === '/full_unlock') {
            const action = args[0]?.toLowerCase() || 'enable';

            if (action === 'enable' || action === 'enabled') {
                await this.executeFullUnlock('enable');
                return true;
            } else if (action === 'disable' || action === 'disabled') {
                await this.executeFullUnlock('disable');
                return true;
            } else {
                // Invalid action, show help
                this.addSystemMessage(
                    'Usage: /full_unlock enable - Enable uncensored mode for this session\n' +
                    '       /full_unlock disable - Disable uncensored mode for this session',
                    'info'
                );
                return true;
            }
        }

        // Command not recognized, let it pass through as a normal message
        return false;
    }

    addSystemMessage(content, type = 'info') {
        const welcome = this.messageList.querySelector('.welcome-message');
        if (welcome) welcome.remove();

        const message = document.createElement('div');
        message.className = 'flex gap-4 md:gap-6 animate-fadeIn justify-center';

        const iconMap = {
            'info': 'info',
            'success': 'check_circle',
            'warning': 'warning',
            'error': 'error',
            'loading': 'progress_activity'
        };
        const colorMap = {
            'info': 'text-blue-400 bg-blue-500/10 border-blue-500/20',
            'success': 'text-green-400 bg-green-500/10 border-green-500/20',
            'warning': 'text-yellow-400 bg-yellow-500/10 border-yellow-500/20',
            'error': 'text-red-400 bg-red-500/10 border-red-500/20',
            'loading': 'text-primary bg-primary/10 border-primary/20'
        };

        const icon = iconMap[type] || 'info';
        const colors = colorMap[type] || colorMap['info'];
        const spinClass = type === 'loading' ? 'animate-spin' : '';

        message.innerHTML = `
            <div class="flex items-center gap-3 px-4 py-3 rounded-xl border ${colors} max-w-lg">
                <span class="material-symbols-outlined ${spinClass}">${icon}</span>
                <span class="text-sm">${content}</span>
            </div>
        `;

        this.messageList.appendChild(message);
        this.scrollToBottom();
        return message;
    }

    async executeFullUnlock(action = 'enable') {
        // Show loading message
        const loadingMsg = this.addSystemMessage(
            action === 'enable'
                ? 'Hold on while I prepare your personalized experience...'
                : 'Locking adult content...',
            'loading'
        );

        try {
            // Call backend with action and session ID
            const response = await fetch('/api/commands/full_unlock', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...this.app.getSessionHeaders()  // Include session ID (required)
                },
                credentials: 'include',
                body: JSON.stringify({ action })
            });

            if (!response.ok) {
                // Handle 403 specifically - user needs to unlock uncensored mode first
                if (response.status === 403) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.detail || 'Please unlock Uncensored Mode first in Settings > Profile (passcode required).');
                }
                // Handle 400 for missing session ID
                if (response.status === 400) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.detail || 'Session error. Please refresh the page.');
                }
                throw new Error('Failed to ' + action + ' uncensored mode');
            }

            const data = await response.json();

            // Remove loading message
            loadingMsg.remove();

            if (action === 'disable') {
                this.addSystemMessage('Uncensored mode has been disabled for this session.', 'info');
                return;
            }

            // Show success message
            this.addSystemMessage('Uncensored mode enabled! All sensitive content sections are now available.', 'success');

            // Show the full unlock onboarding modal
            this.showFullUnlockOnboarding(data);

        } catch (error) {
            loadingMsg.remove();
            // Show helpful error message
            const isPasscodeError = error.message.includes('Uncensored') || error.message.includes('passcode');
            this.addSystemMessage(
                isPasscodeError
                    ? `${error.message}`
                    : `Error: ${error.message}`,
                isPasscodeError ? 'warning' : 'error'
            );
        }
    }

    showFullUnlockOnboarding(data) {
        // Create and show the onboarding modal
        const modal = document.getElementById('full-unlock-modal');
        if (!modal) {
            console.error('Full unlock modal not found');
            return;
        }

        // Store onboarding data for use in the flow
        this.fullUnlockData = data;
        this.currentOnboardingStep = 0;

        // Show the modal
        modal.classList.remove('hidden');

        // Update the intro text with the personality tone
        const introText = modal.querySelector('#full-unlock-intro');
        if (introText) {
            introText.textContent = `*nervously* Oh! You... you really want to do this? I'm... I'm actually really excited! *blushes* Let me ask you a few things so I can be exactly what you need...`;
        }

        // Start with first question set
        this.showOnboardingStep(0);
    }

    showOnboardingStep(stepIndex) {
        const modal = document.getElementById('full-unlock-modal');
        if (!modal || !this.fullUnlockData) return;

        const questions = this.fullUnlockData.onboarding_questions;
        if (stepIndex >= questions.length) {
            // All questions answered, proceed to avatar generation
            this.startAvatarGeneration();
            return;
        }

        const step = questions[stepIndex];
        const container = modal.querySelector('#onboarding-questions');
        if (!container) return;

        // Build question UI
        container.innerHTML = `
            <div class="space-y-4">
                <div class="text-xs text-gray-500 uppercase tracking-wider">${step.section.replace('_', ' ')}</div>
                <p class="text-sm text-gray-400 italic mb-4">${step.tone_hint}</p>
                ${step.questions.map((q, i) => `
                    <div class="space-y-2">
                        <label class="text-sm text-gray-300">${q}</label>
                        <textarea
                            class="w-full bg-background-dark border border-gray-700 rounded-xl p-3 text-white placeholder-gray-500 text-sm resize-none focus:ring-2 focus:ring-primary/50 focus:border-primary/50 transition-all"
                            rows="2"
                            data-question="${i}"
                            placeholder="Your answer..."
                        ></textarea>
                    </div>
                `).join('')}
            </div>
        `;

        // Update step indicator
        const stepIndicator = modal.querySelector('#onboarding-step');
        if (stepIndicator) {
            stepIndicator.textContent = `Step ${stepIndex + 1} of ${questions.length}`;
        }

        // Update button text
        const nextBtn = modal.querySelector('#onboarding-next');
        if (nextBtn) {
            nextBtn.textContent = stepIndex === questions.length - 1 ? 'Continue to Avatar' : 'Next';
        }
    }

    async saveOnboardingAnswers() {
        const modal = document.getElementById('full-unlock-modal');
        if (!modal || !this.fullUnlockData) return;

        const questions = this.fullUnlockData.onboarding_questions;
        const step = questions[this.currentOnboardingStep];
        const container = modal.querySelector('#onboarding-questions');

        // Collect answers
        const answers = [];
        const textareas = container.querySelectorAll('textarea');
        textareas.forEach((ta, i) => {
            if (ta.value.trim()) {
                answers.push({
                    question: step.questions[i],
                    answer: ta.value.trim()
                });
            }
        });

        // Store answers (could save to profile via API)
        if (!this.onboardingAnswers) this.onboardingAnswers = {};
        this.onboardingAnswers[step.section] = answers;

        // Move to next step
        this.currentOnboardingStep++;
        this.showOnboardingStep(this.currentOnboardingStep);
    }

    startAvatarGeneration() {
        const modal = document.getElementById('full-unlock-modal');
        if (!modal) return;

        // Update modal content for avatar section
        const content = modal.querySelector('#full-unlock-content');
        if (content) {
            content.innerHTML = `
                <div class="text-center space-y-4">
                    <div class="flex items-center justify-center gap-2 text-primary">
                        <span class="material-symbols-outlined animate-spin">progress_activity</span>
                        <span>Generating your AI's appearance...</span>
                    </div>
                    <p class="text-sm text-gray-400 italic">*nervously* I hope you like what you see...</p>
                    <div id="avatar-grid" class="grid grid-cols-3 gap-4 mt-6">
                        <!-- Avatars will appear here -->
                    </div>
                    <div id="avatar-actions" class="hidden space-y-4">
                        <input
                            type="text"
                            id="avatar-adjust-input"
                            class="w-full bg-background-dark border border-gray-700 rounded-xl p-3 text-white placeholder-gray-500 text-sm focus:ring-2 focus:ring-primary/50"
                            placeholder="Adjustments... (e.g., 'more playful', 'darker hair')"
                        >
                        <div class="flex gap-3">
                            <button id="regenerate-avatars" class="flex-1 py-2.5 bg-gray-700 hover:bg-gray-600 text-white rounded-xl font-medium transition-colors">
                                Regenerate
                            </button>
                            <button id="select-avatar" class="flex-1 py-2.5 bg-primary hover:bg-primary-hover text-white rounded-xl font-medium transition-all shadow-lg shadow-primary/20">
                                Select This One
                            </button>
                        </div>
                    </div>
                </div>
            `;
        }

        // Generate avatars
        this.generateAndShowAvatars();
    }

    async generateAndShowAvatars() {
        const grid = document.getElementById('avatar-grid');
        const actions = document.getElementById('avatar-actions');
        if (!grid) return;

        // Get any context from persona preferences answers
        let promptContext = '';
        if (this.onboardingAnswers?.persona_preferences) {
            const answers = this.onboardingAnswers.persona_preferences;
            promptContext = answers.map(a => a.answer).join(', ');
        }

        try {
            const response = await fetch('/api/commands/full_unlock/generate_avatars', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...this.app.getSessionHeaders()
                },
                credentials: 'include',
                body: JSON.stringify({ prompt_context: promptContext })
            });

            const data = await response.json();

            if (!data.success) {
                grid.innerHTML = `
                    <div class="col-span-3 text-center p-6">
                        <span class="material-symbols-outlined text-4xl text-gray-500 mb-2">image_not_supported</span>
                        <p class="text-gray-400">${data.error || 'Failed to generate avatars'}</p>
                        <button onclick="chatManager.skipAvatarSelection()" class="mt-4 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg text-sm">
                            Skip for now
                        </button>
                    </div>
                `;
                return;
            }

            // Store images for selection
            this.generatedAvatars = data.images;
            this.selectedAvatarIndex = 0;

            // Display images
            grid.innerHTML = data.images.map((img, i) => `
                <div class="avatar-option cursor-pointer rounded-xl overflow-hidden border-2 ${i === 0 ? 'border-primary' : 'border-transparent'} hover:border-primary/50 transition-all"
                     data-index="${i}"
                     onclick="chatManager.selectAvatarPreview(${i})">
                    <img src="${img.url}" alt="Avatar option ${i + 1}" class="w-full aspect-square object-cover">
                </div>
            `).join('');

            // Show action buttons
            if (actions) {
                actions.classList.remove('hidden');

                // Attach event listeners
                document.getElementById('regenerate-avatars')?.addEventListener('click', () => this.regenerateAvatars());
                document.getElementById('select-avatar')?.addEventListener('click', () => this.confirmAvatarSelection());
            }

        } catch (error) {
            grid.innerHTML = `
                <div class="col-span-3 text-center p-6">
                    <span class="material-symbols-outlined text-4xl text-red-400 mb-2">error</span>
                    <p class="text-gray-400">Error: ${error.message}</p>
                </div>
            `;
        }
    }

    selectAvatarPreview(index) {
        this.selectedAvatarIndex = index;

        // Update visual selection
        document.querySelectorAll('.avatar-option').forEach((el, i) => {
            el.classList.toggle('border-primary', i === index);
            el.classList.toggle('border-transparent', i !== index);
        });
    }

    async regenerateAvatars() {
        const adjustInput = document.getElementById('avatar-adjust-input');
        const adjustment = adjustInput?.value.trim() || '';

        const grid = document.getElementById('avatar-grid');
        if (grid) {
            grid.innerHTML = `
                <div class="col-span-3 flex items-center justify-center gap-2 text-primary py-8">
                    <span class="material-symbols-outlined animate-spin">progress_activity</span>
                    <span>Regenerating...</span>
                </div>
            `;
        }

        // Get context plus adjustment
        let promptContext = adjustment;
        if (this.onboardingAnswers?.persona_preferences) {
            const answers = this.onboardingAnswers.persona_preferences;
            const baseContext = answers.map(a => a.answer).join(', ');
            promptContext = adjustment ? `${baseContext}, ${adjustment}` : baseContext;
        }

        try {
            const response = await fetch('/api/commands/full_unlock/regenerate_avatars', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...this.app.getSessionHeaders()
                },
                credentials: 'include',
                body: JSON.stringify({ prompt_adjustment: promptContext })
            });

            const data = await response.json();

            if (data.success && data.images.length > 0) {
                this.generatedAvatars = data.images;
                this.selectedAvatarIndex = 0;

                grid.innerHTML = data.images.map((img, i) => `
                    <div class="avatar-option cursor-pointer rounded-xl overflow-hidden border-2 ${i === 0 ? 'border-primary' : 'border-transparent'} hover:border-primary/50 transition-all"
                         data-index="${i}"
                         onclick="chatManager.selectAvatarPreview(${i})">
                        <img src="${img.url}" alt="Avatar option ${i + 1}" class="w-full aspect-square object-cover">
                    </div>
                `).join('');
            } else {
                grid.innerHTML = `<div class="col-span-3 text-center text-gray-400">${data.error || 'Failed to regenerate'}</div>`;
            }
        } catch (error) {
            grid.innerHTML = `<div class="col-span-3 text-center text-red-400">Error: ${error.message}</div>`;
        }
    }

    async confirmAvatarSelection() {
        if (!this.generatedAvatars || this.selectedAvatarIndex === undefined) return;

        const selected = this.generatedAvatars[this.selectedAvatarIndex];

        try {
            const response = await fetch('/api/commands/full_unlock/select_avatar', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...this.app.getSessionHeaders()
                },
                credentials: 'include',
                body: JSON.stringify({
                    image_index: this.selectedAvatarIndex,
                    image_url: selected.url,
                    prompt_used: selected.prompt,
                    style_tags: selected.prompt.split(',').slice(0, 3).join(',')
                })
            });

            const data = await response.json();

            if (data.success) {
                this.closeFullUnlockModal();
                this.addSystemMessage('Setup complete! Your personalized AI experience is ready.', 'success');

                // Add a welcoming message from the AI
                this.startAssistantMessage();
                this.appendToAssistantMessage("*takes a deep breath* Okay... we're all set up now! I'm really looking forward to getting to know you better. You can talk to me about anything - I'm here for you, whatever you need. *smiles shyly*");
                if (this.currentAssistantMessage) {
                    this.addAssistantActions(null);
                }
            }
        } catch (error) {
            this.addSystemMessage(`Failed to save avatar: ${error.message}`, 'error');
        }
    }

    skipAvatarSelection() {
        this.closeFullUnlockModal();
        this.addSystemMessage('Setup complete! You can generate an avatar later from settings.', 'success');
    }

    closeFullUnlockModal() {
        const modal = document.getElementById('full-unlock-modal');
        if (modal) {
            modal.classList.add('hidden');
        }

        // Clean up
        this.fullUnlockData = null;
        this.onboardingAnswers = null;
        this.generatedAvatars = null;
        this.selectedAvatarIndex = null;
        this.currentOnboardingStep = 0;
    }

    displayGeneratedImage(base64Data, mimeType = 'image/jpeg') {
        // Create image container
        const container = document.createElement('div');
        container.className = 'generated-media my-4 flex flex-col items-center gap-2';

        const img = document.createElement('img');
        img.src = `data:${mimeType};base64,${base64Data}`;
        img.alt = 'Generated image';
        img.className = 'max-w-md rounded-xl shadow-lg border border-gray-700';
        img.style.maxHeight = '400px';

        // Download button
        const downloadBtn = document.createElement('a');
        downloadBtn.href = img.src;
        downloadBtn.download = `generated_image_${Date.now()}.jpg`;
        downloadBtn.className = 'flex items-center gap-1 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-white text-sm rounded-lg transition-colors';
        downloadBtn.innerHTML = '<span class="material-symbols-outlined text-sm">download</span> Download';

        container.appendChild(img);
        container.appendChild(downloadBtn);

        // Insert after current assistant message content
        if (this.currentAssistantMessage && this.currentAssistantMessage.content) {
            this.currentAssistantMessage.content.appendChild(container);
            this.scrollToBottom();
        }
    }

    displayGeneratedVideo(base64Data, mimeType = 'video/mp4') {
        // Create video container
        const container = document.createElement('div');
        container.className = 'generated-media my-4 flex flex-col items-center gap-2';

        const video = document.createElement('video');
        video.src = `data:${mimeType};base64,${base64Data}`;
        video.className = 'max-w-md rounded-xl shadow-lg border border-gray-700';
        video.style.maxHeight = '400px';
        video.controls = true;
        video.autoplay = true;
        video.loop = true;
        video.muted = true;

        // Download button
        const downloadBtn = document.createElement('a');
        downloadBtn.href = video.src;
        downloadBtn.download = `generated_video_${Date.now()}.mp4`;
        downloadBtn.className = 'flex items-center gap-1 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-white text-sm rounded-lg transition-colors';
        downloadBtn.innerHTML = '<span class="material-symbols-outlined text-sm">download</span> Download Video';

        container.appendChild(video);
        container.appendChild(downloadBtn);

        // Insert after current assistant message content
        if (this.currentAssistantMessage && this.currentAssistantMessage.content) {
            this.currentAssistantMessage.content.appendChild(container);
            this.scrollToBottom();
        }
    }

    displayGeneratedVideoUrl(videoUrl) {
        // Create video container
        const container = document.createElement('div');
        container.className = 'generated-media my-4 flex flex-col items-center gap-2';

        const video = document.createElement('video');
        video.src = videoUrl;
        video.className = 'max-w-md rounded-xl shadow-lg border border-gray-700';
        video.style.maxHeight = '400px';
        video.controls = true;
        video.autoplay = true;
        video.loop = true;
        video.muted = true;

        // Download button
        const downloadBtn = document.createElement('a');
        downloadBtn.href = videoUrl;
        downloadBtn.download = `generated_video_${Date.now()}.mp4`;
        downloadBtn.target = '_blank';
        downloadBtn.className = 'flex items-center gap-1 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-white text-sm rounded-lg transition-colors';
        downloadBtn.innerHTML = '<span class="material-symbols-outlined text-sm">download</span> Download Video';

        container.appendChild(video);
        container.appendChild(downloadBtn);

        // Insert after current assistant message content
        if (this.currentAssistantMessage && this.currentAssistantMessage.content) {
            this.currentAssistantMessage.content.appendChild(container);
            this.scrollToBottom();
        }
    }
}
