// Main Application Controller
import { ChatManager } from './chat.js?v=5';
import { SettingsManager } from './settings.js?v=3';
import { initVoiceManager, voiceManager } from './voice.js?v=4';

class App {
    constructor() {
        this.currentConversationId = null;
        this.chatManager = new ChatManager(this);
        this.settingsManager = new SettingsManager(this);
        this.currentModel = null;
        this.pendingImages = [];
        this.pendingFiles = [];
        this.modelCapabilities = null;
        this.thinkEnabled = false;
        this.maxFileSize = 25 * 1024 * 1024; // 25 MB
        this.isMobileView = null;
        this.sidebarCollapsed = false;
        // Search state
        this.allConversations = [];
        this.searchQuery = '';
        this.deepSearchResults = null;
        // Auth state
        this.isAuthenticated = false;
        this.appInitStarted = false;
        // Session ID for adult content gating
        // CRITICAL: New sessions start locked. User must run /full_unlock enable each session.
        this.sessionId = this.generateSessionId();
    }

    /**
     * Generate a unique session ID for this browser session.
     * Session ID resets on browser refresh/tab close (critical child safety requirement).
     */
    generateSessionId() {
        // Check if we already have a session ID for this tab
        let sessionId = sessionStorage.getItem('brinchat_session_id');
        if (!sessionId) {
            // Generate a new UUID-like session ID (with fallback for non-secure contexts)
            const uuid = (typeof crypto.randomUUID === 'function')
                ? crypto.randomUUID()
                : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
                    const r = Math.random() * 16 | 0;
                    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
                });
            sessionId = 'sess_' + uuid;
            sessionStorage.setItem('brinchat_session_id', sessionId);
        }
        return sessionId;
    }

    /**
     * Get headers with session ID for API requests.
     * All requests should include this to enable session-scoped adult content.
     */
    getSessionHeaders() {
        return {
            'X-Session-ID': this.sessionId
        };
    }

    async init() {
        // Setup auth event listeners first (needed before login)
        this.setupAuthEventListeners();

        // Setup auth state change handler
        authManager.setOnAuthChange((user) => this.handleAuthChange(user));

        // Check if user is already authenticated
        const authResult = await authManager.init();

        if (!authResult.authenticated) {
            // If this is a new session (new tab), clear stored conversation
            // so user starts fresh after login
            if (authResult.isNewSession) {
                sessionStorage.removeItem('currentConversationId');
            }
            // Show auth modal and wait for login
            this.showAuthModal();
            return;
        }

        // User is authenticated, continue with normal init
        await this.initializeApp();
    }

    setupAuthEventListeners() {
        // Auth modal tabs
        document.getElementById('login-tab')?.addEventListener('click', () => {
            this.switchAuthTab('login');
        });
        document.getElementById('register-tab')?.addEventListener('click', () => {
            this.switchAuthTab('register');
        });

        // Login/Register buttons
        document.getElementById('login-btn')?.addEventListener('click', () => {
            this.handleLogin();
        });
        document.getElementById('register-btn')?.addEventListener('click', () => {
            this.handleRegister();
        });
        document.getElementById('close-auth')?.addEventListener('click', () => {
            if (this.isAuthenticated) {
                this.hideAuthModal();
            }
        });

        // Enter key for login/register forms
        document.getElementById('login-password')?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this.handleLogin();
        });
        document.getElementById('register-confirm')?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this.handleRegister();
        });

        // Logout button
        document.getElementById('logout-btn')?.addEventListener('click', () => {
            this.handleLogout();
        });
    }

    async initializeApp() {
        if (this.appInitStarted) return;  // Prevent double initialization
        this.appInitStarted = true;

        this.isAuthenticated = true;
        this.setupEventListeners();  // Attach UI handlers first
        this.updateUserDisplay();

        await this.loadModelCapabilities();
        await this.settingsManager.loadSettings();
        await this.loadModels();
        await this.loadConversations();
        await this.updateUsageGauges();

        // Initialize voice manager
        this.voiceManager = initVoiceManager(this);

        // Initialize profile to get assistant name
        if (typeof profileManager !== 'undefined') {
            await profileManager.init();
            this.updateAssistantName(profileManager.getAssistantName());
        }

        // Restore last active conversation on refresh (same tab).
        // New tabs are handled by authManager via localStorage session marker.
        const storedConvId = sessionStorage.getItem('currentConversationId') || localStorage.getItem('brinchat_last_conversation_id');
        if (storedConvId) {
            console.log('[Init] Restoring last conversation ID:', storedConvId);
            // loadConversation() will set currentConversationId and render messages
            await this.loadConversation(storedConvId);
        } else {
            console.log('[Init] No stored conversation ID; starting fresh');
            this.currentConversationId = null;
        }

        // Handle sidebar based on viewport
        this.handleViewportResize();
        window.addEventListener('resize', () => this.handleViewportResize());

        // Dynamic viewport height for mobile (handles keyboard, browser chrome)
        this.updateViewportHeight();
        window.addEventListener('resize', () => this.updateViewportHeight());
        // Use visualViewport API if available (better for mobile keyboard)
        if (window.visualViewport) {
            window.visualViewport.addEventListener('resize', () => this.updateViewportHeight());
        }

        console.log('App initialized');
    }

    handleAuthChange(user) {
        if (user) {
            this.isAuthenticated = true;
            this.hideAuthModal();
            this.updateUserDisplay();

            // If app wasn't initialized yet, do it now
            if (!this.currentModel) {
                this.initializeApp().catch(error => {
                    console.error('[App] Failed to initialize after auth change:', error);
                    window.chatManager?.showToast('Failed to initialize app. Please refresh.', 'error');
                });
            }
        } else {
            this.isAuthenticated = false;
            this.updateUserDisplay();
            // Clear local state
            this.currentConversationId = null;
            this.allConversations = [];
            sessionStorage.removeItem('currentConversationId');
            // Show login modal
            this.showAuthModal();
        }
    }

    updateUserDisplay() {
        const userInfo = document.getElementById('user-info');
        const userDisplayName = document.getElementById('user-display-name');

        if (authManager.isAuthenticated()) {
            const user = authManager.getUser();
            userInfo.classList.remove('hidden');
            userDisplayName.textContent = user.username;
        } else {
            userInfo.classList.add('hidden');
            userDisplayName.textContent = 'User';
        }
    }

    showAuthModal() {
        const modal = document.getElementById('auth-modal');
        modal.classList.remove('hidden');
        // Reset to login form
        this.switchAuthTab('login');
    }

    hideAuthModal() {
        const modal = document.getElementById('auth-modal');
        modal.classList.add('hidden');
        // Clear any errors
        document.getElementById('auth-error').classList.add('hidden');
        document.getElementById('register-error').classList.add('hidden');
    }

    switchAuthTab(tab) {
        const loginTab = document.getElementById('login-tab');
        const registerTab = document.getElementById('register-tab');
        const loginForm = document.getElementById('login-form');
        const registerForm = document.getElementById('register-form');
        const modalTitle = document.getElementById('auth-modal-title');

        if (tab === 'login') {
            loginTab.classList.add('text-primary', 'border-primary');
            loginTab.classList.remove('text-gray-400', 'border-transparent');
            registerTab.classList.remove('text-primary', 'border-primary');
            registerTab.classList.add('text-gray-400', 'border-transparent');
            loginForm.classList.remove('hidden');
            registerForm.classList.add('hidden');
            modalTitle.textContent = 'Sign In';
        } else {
            registerTab.classList.add('text-primary', 'border-primary');
            registerTab.classList.remove('text-gray-400', 'border-transparent');
            loginTab.classList.remove('text-primary', 'border-primary');
            loginTab.classList.add('text-gray-400', 'border-transparent');
            registerForm.classList.remove('hidden');
            loginForm.classList.add('hidden');
            modalTitle.textContent = 'Create Account';
        }

        // Clear errors when switching tabs
        document.getElementById('auth-error').classList.add('hidden');
        document.getElementById('register-error').classList.add('hidden');
    }

    async handleLogin() {
        const username = document.getElementById('login-username').value.trim();
        const password = document.getElementById('login-password').value;
        const errorDiv = document.getElementById('auth-error');

        if (!username || !password) {
            errorDiv.textContent = 'Please enter username and password';
            errorDiv.classList.remove('hidden');
            return;
        }

        try {
            await authManager.login(username, password);
            // Clear inputs
            document.getElementById('login-username').value = '';
            document.getElementById('login-password').value = '';
        } catch (error) {
            errorDiv.textContent = error.message;
            errorDiv.classList.remove('hidden');
        }
    }

    async handleRegister() {
        const username = document.getElementById('register-username').value.trim();
        const email = document.getElementById('register-email').value.trim();
        const password = document.getElementById('register-password').value;
        const confirm = document.getElementById('register-confirm').value;
        const errorDiv = document.getElementById('register-error');

        if (!username || !password) {
            errorDiv.textContent = 'Please enter username and password';
            errorDiv.classList.remove('hidden');
            return;
        }

        if (username.length < 3) {
            errorDiv.textContent = 'Username must be at least 3 characters';
            errorDiv.classList.remove('hidden');
            return;
        }

        // Password validation to match backend requirements
        if (password.length < 12) {
            errorDiv.textContent = 'Password must be at least 12 characters';
            errorDiv.classList.remove('hidden');
            return;
        }
        if (!/[A-Z]/.test(password)) {
            errorDiv.textContent = 'Password must contain at least one uppercase letter';
            errorDiv.classList.remove('hidden');
            return;
        }
        if (!/[a-z]/.test(password)) {
            errorDiv.textContent = 'Password must contain at least one lowercase letter';
            errorDiv.classList.remove('hidden');
            return;
        }
        if (!/\d/.test(password)) {
            errorDiv.textContent = 'Password must contain at least one digit';
            errorDiv.classList.remove('hidden');
            return;
        }
        if (!/[!@#$%^&*(),.?":{}|<>_\-+=\[\]\\;'`~]/.test(password)) {
            errorDiv.textContent = 'Password must contain at least one special character';
            errorDiv.classList.remove('hidden');
            return;
        }

        if (password !== confirm) {
            errorDiv.textContent = 'Passwords do not match';
            errorDiv.classList.remove('hidden');
            return;
        }

        try {
            await authManager.register(username, password, email || null);
            // Clear inputs
            document.getElementById('register-username').value = '';
            document.getElementById('register-email').value = '';
            document.getElementById('register-password').value = '';
            document.getElementById('register-confirm').value = '';
        } catch (error) {
            errorDiv.textContent = error.message;
            errorDiv.classList.remove('hidden');
        }
    }

    async handleLogout() {
        await authManager.logout();
        // Clear chat and reload
        this.chatManager.clearMessages();
        document.getElementById('conversation-list').innerHTML = '';
        const titleEl = document.getElementById('current-chat-title');
        if (titleEl) titleEl.textContent = 'New Chat';
    }

    showError(message) {
        const toast = document.createElement('div');
        toast.className = 'fixed bottom-4 right-4 bg-red-600 text-white px-4 py-2 rounded shadow-lg z-50';
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 5000);
    }

    updateViewportHeight() {
        // Use visualViewport if available (accounts for keyboard on mobile)
        const vh = window.visualViewport ? window.visualViewport.height : window.innerHeight;
        document.documentElement.style.setProperty('--app-height', `${vh}px`);
    }

    handleViewportResize() {
        const sidebar = document.getElementById('sidebar');
        const overlay = document.getElementById('sidebar-overlay');
        const menuButton = document.getElementById('sidebar-toggle');
        const wasMobile = this.isMobileView;
        const isMobile = window.innerWidth < 768; // md breakpoint

        // First run - initialize sidebar state
        if (wasMobile === null) {
            this.isMobileView = isMobile;
            this.sidebarCollapsed = false;

            if (isMobile) {
                sidebar.classList.add('-translate-x-full');
                this.showMenuButton();
            } else {
                sidebar.classList.remove('-translate-x-full');
                this.hideMenuButton();
            }
            return;
        }

        if (wasMobile === isMobile) return;
        this.isMobileView = isMobile;

        if (isMobile) {
            // Switching to mobile - hide sidebar, show menu button
            sidebar.classList.add('-translate-x-full');
            overlay.classList.add('hidden');
            this.showMenuButton();
        } else {
            // Switching to desktop - show sidebar unless user collapsed it
            if (!this.sidebarCollapsed) {
                sidebar.classList.remove('-translate-x-full');
                this.hideMenuButton();
            }
            overlay.classList.add('hidden');
        }
    }

    showMenuButton() {
        const btn = document.getElementById('sidebar-toggle');
        const sidebar = document.getElementById('sidebar');
        if (btn) {
            btn.classList.remove('hidden');
            btn.classList.add('flex');
        }
        // On desktop, collapse sidebar to hamburger button width (64px)
        if (sidebar && !this.isMobileView) {
            sidebar.style.width = '64px';
            sidebar.style.minWidth = '64px';
            sidebar.style.overflow = 'hidden';
        }
        // Expand content area when sidebar is collapsed
        this.expandContentArea();
    }

    hideMenuButton() {
        const btn = document.getElementById('sidebar-toggle');
        const sidebar = document.getElementById('sidebar');
        if (btn) {
            btn.classList.add('hidden');
            btn.classList.remove('flex');
        }
        // Restore sidebar width on desktop
        if (sidebar && !this.isMobileView) {
            sidebar.style.width = '';
            sidebar.style.minWidth = '';
            sidebar.style.overflow = '';
        }
        // Restore normal content width when sidebar is visible
        this.restoreContentArea();
    }

    expandContentArea() {
        const main = document.querySelector('main');
        const messageList = document.getElementById('message-list');
        const inputContainer = document.getElementById('input-container');
        // Add right padding to balance the 64px collapsed sidebar
        if (main && !this.isMobileView) {
            main.style.paddingRight = '64px';
        }
        // Remove max-width constraint or expand it
        if (messageList) {
            messageList.classList.remove('max-w-4xl');
            messageList.classList.add('max-w-6xl');
        }
        if (inputContainer) {
            inputContainer.classList.remove('max-w-4xl');
            inputContainer.classList.add('max-w-6xl');
        }
    }

    restoreContentArea() {
        const main = document.querySelector('main');
        const messageList = document.getElementById('message-list');
        const inputContainer = document.getElementById('input-container');
        // Remove right padding
        if (main) {
            main.style.paddingRight = '';
        }
        // Restore normal max-width
        if (messageList) {
            messageList.classList.add('max-w-4xl');
            messageList.classList.remove('max-w-6xl');
        }
        if (inputContainer) {
            inputContainer.classList.add('max-w-4xl');
            inputContainer.classList.remove('max-w-6xl');
        }
    }

    async loadModelCapabilities() {
        try {
            const response = await fetch('/api/models/capabilities', {
                credentials: 'include'
            });
            this.modelCapabilities = await response.json();
        } catch (error) {
            console.error('Failed to load model capabilities:', error);
            this.modelCapabilities = { supports_tools: false, tools: [] };
        }
    }

    friendlyModelName(modelId) {
        if (!modelId) return 'Brin (OpenClaw)';
        if (modelId === 'openclaw' || modelId.startsWith('openclaw:')) {
            return `Brin (${modelId})`;
        }
        return modelId;
    }

    updateModelBadge(modelId) {
        const modelBadge = document.getElementById('model-badge');
        if (!modelBadge) return;
        modelBadge.textContent = this.friendlyModelName(modelId);
    }

    bindModelBadge() {
        const badge = document.getElementById('model-badge');
        if (!badge) return;

        // Prevent double-binding
        if (badge.dataset.bound === '1') return;
        badge.dataset.bound = '1';

        badge.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();

            // Build a small dropdown menu anchored to the badge container (parent is `relative`)
            const container = badge.parentElement;
            if (!container) return;

            let menu = container.querySelector('#model-menu');
            if (!menu) {
                menu = document.createElement('div');
                menu.id = 'model-menu';
                menu.className = 'absolute right-0 top-full mt-2 w-64 bg-surface-dark border border-gray-700 rounded-xl shadow-2xl overflow-hidden z-50';
                container.appendChild(menu);

                // Close on outside click
                document.addEventListener('click', () => {
                    menu?.classList.add('hidden');
                });
            }

            // Populate options
            const models = (this.availableModels || []).slice();
            menu.innerHTML = `
                <div class="p-2 border-b border-gray-700 text-xs text-gray-400">Select model</div>
                <div class="p-2 space-y-1" id="model-menu-items"></div>
            `;

            const items = menu.querySelector('#model-menu-items');
            models.forEach((mid) => {
                const btn = document.createElement('button');
                btn.className = 'flex items-center justify-between w-full px-3 py-2 rounded-lg hover:bg-white/5 transition-colors text-left';
                btn.innerHTML = `
                    <span class="text-sm text-white truncate">${this.friendlyModelName(mid)}</span>
                    ${mid === this.currentModel ? '<span class="text-xs text-primary">active</span>' : ''}
                `;
                btn.addEventListener('click', async (evt) => {
                    evt.preventDefault();
                    evt.stopPropagation();
                    menu.classList.add('hidden');
                    await this.selectModel(mid);
                });
                items.appendChild(btn);
            });

            // Toggle
            menu.classList.toggle('hidden');
        });
    }

    async loadModels() {
        try {
            const response = await fetch('/api/models', {
                credentials: 'include'
            });
            const data = await response.json();

            this.currentModel = data.current || 'openclaw:main';
            this.availableModels = (data.models || []).map(m => m.name);
            this.modelsData = {};
            (data.models || []).forEach(m => { this.modelsData[m.name] = m; });

            this.updateModelBadge(this.currentModel);
            this.bindModelBadge();

            // Best-effort capability indicators
            this.updateCapabilityIndicators(this.modelsData[this.currentModel] || {});
        } catch (error) {
            console.error('Failed to load model info:', error);
            this.currentModel = 'openclaw:main';
            this.availableModels = ['openclaw:main'];
            this.updateModelBadge(this.currentModel);
        }
    }

    updateCapabilityIndicators(model) {
        // Update header capability icons (Claude supports all)
        const toolsIcon = document.getElementById('cap-tools');
        const visionIcon = document.getElementById('cap-vision');
        const thinkingIcon = document.getElementById('cap-thinking');

        // For Claude, always show all capabilities
        if (toolsIcon) {
            toolsIcon.classList.remove('hidden');
        }
        if (visionIcon) {
            visionIcon.classList.remove('hidden');
        }
        if (thinkingIcon) {
            thinkingIcon.classList.remove('hidden');
        }

        // Thinking mode is always available for Claude
        const thinkingMenuItem = document.getElementById('menu-thinking');
        if (thinkingMenuItem) {
            thinkingMenuItem.classList.remove('hidden');
        }

        // No tools warning - never show for Claude
        const noToolsWarning = document.getElementById('no-tools-warning');
        if (noToolsWarning) {
            noToolsWarning.classList.toggle('hidden', model?.supports_tools !== false);
        }
    }

    // Gauge update methods
    async updateUsageGauges() {
        try {
            const response = await fetch('/api/models/usage', { credentials: 'include' });
            if (!response.ok) return;

            const data = await response.json();

            // Update VRAM gauge
            if (data.vram?.available) {
                this.updateGauge('vram', data.vram.percent);
                document.getElementById('vram-gauge-container')?.classList.remove('hidden');
            }
        } catch (error) {
            // Silent fail - gauges are optional
        }
    }

    updateGauge(type, percent) {
        const gauge = document.getElementById(`${type}-gauge`);
        const label = document.getElementById(`${type}-label`);

        if (!gauge || !label) return;

        gauge.style.width = `${percent}%`;
        label.textContent = `${Math.round(percent)}%`;

        // Color coding based on usage
        gauge.classList.remove('bg-primary', 'bg-yellow-500', 'bg-red-500', 'bg-green-500');

        if (type === 'vram') {
            if (percent > 90) gauge.classList.add('bg-red-500');
            else if (percent > 75) gauge.classList.add('bg-yellow-500');
            else gauge.classList.add('bg-green-500');
        } else {
            if (percent > 90) gauge.classList.add('bg-red-500');
            else if (percent > 75) gauge.classList.add('bg-yellow-500');
            else gauge.classList.add('bg-primary');
        }
    }

    updateContextUsage(currentTokens, maxTokens) {
        const percent = maxTokens > 0 ? (currentTokens / maxTokens) * 100 : 0;
        this.updateGauge('context', Math.min(percent, 100));
    }

    async updateModelCapabilities(modelName) {
        try {
            const response = await fetch(`/api/models/capabilities/${encodeURIComponent(modelName)}`, {
                credentials: 'include'
            });
            if (!response.ok) return;

            const caps = await response.json();
            this.currentModelContextWindow = caps.context_window || 4096;

            // Update context slider max if settings panel exists
            const ctxSlider = document.getElementById('settings-context');
            if (ctxSlider && caps.context_window) {
                ctxSlider.max = caps.context_window;
                if (parseInt(ctxSlider.value) > caps.context_window) {
                    ctxSlider.value = caps.context_window;
                    const ctxValue = document.getElementById('context-value');
                    if (ctxValue) ctxValue.textContent = caps.context_window;
                }
            }
        } catch (error) {
            console.warn('Failed to get model capabilities:', error);
        }
    }

    async loadConversations() {
        try {
            const response = await fetch('/api/chat/conversations', {
                credentials: 'include'
            });
            const data = await response.json();
            this.allConversations = data.conversations || [];
            this.filterAndRenderConversations();
        } catch (error) {
            console.error('Failed to load conversations:', error);
        }
    }

    filterAndRenderConversations() {
        const query = this.searchQuery.toLowerCase().trim();
        let filtered = this.allConversations;

        if (query) {
            filtered = this.allConversations.filter(conv =>
                conv.title.toLowerCase().includes(query)
            );
        }

        this.renderConversationList(filtered);
        
        // Show hint about deep search if filtering
        const searchHint = document.getElementById('deep-search-hint');
        if (searchHint) {
            searchHint.classList.toggle('hidden', !query || this.deepSearchResults);
        }
    }
    
    async deepSearchMessages(query) {
        if (!query || query.length < 2) return;
        
        try {
            const response = await this.fetchWithAuth(`/api/chat/conversations/search?q=${encodeURIComponent(query)}&limit=50`);
            if (!response.ok) throw new Error('Search failed');
            
            const data = await response.json();
            this.deepSearchResults = data.results;
            this.showDeepSearchResults(data.results, query);
        } catch (error) {
            console.error('Deep search failed:', error);
        }
    }
    
    showDeepSearchResults(results, query) {
        const list = document.getElementById('conversation-list');
        const loadingEl = document.getElementById('conversations-loading');
        const emptyEl = document.getElementById('conversations-empty');
        
        if (loadingEl) loadingEl.classList.add('hidden');
        
        // Clear existing
        const existingItems = list.querySelectorAll('button, .space-y-1');
        existingItems.forEach(item => item.remove());
        
        if (results.length === 0) {
            if (emptyEl) {
                emptyEl.innerHTML = `
                    <span class="material-symbols-outlined text-4xl mb-2 opacity-50">search_off</span>
                    <p>No messages found matching "${query}"</p>
                    <p class="text-xs mt-1">Try a different search term</p>
                `;
                emptyEl.classList.remove('hidden');
            }
            return;
        }
        
        if (emptyEl) emptyEl.classList.add('hidden');
        
        // Group by conversation
        const byConv = {};
        results.forEach(r => {
            if (!byConv[r.conversation_id]) {
                byConv[r.conversation_id] = {
                    title: r.conversation_title,
                    id: r.conversation_id,
                    matches: []
                };
            }
            byConv[r.conversation_id].matches.push(r);
        });
        
        // Render search results header
        const header = document.createElement('div');
        header.className = 'px-2 py-1 text-xs text-primary font-medium flex items-center gap-1';
        header.innerHTML = `
            <span class="material-symbols-outlined text-sm">search</span>
            Found ${results.length} message${results.length !== 1 ? 's' : ''} in ${Object.keys(byConv).length} conversation${Object.keys(byConv).length !== 1 ? 's' : ''}
        `;
        list.appendChild(header);
        
        // Render each conversation with matches
        Object.values(byConv).forEach(conv => {
            const convEl = document.createElement('div');
            convEl.className = 'mb-3 rounded-lg bg-surface-dark/30 p-2';
            
            const convTitle = document.createElement('button');
            convTitle.className = 'w-full text-left text-sm font-medium text-white hover:text-primary transition-colors mb-1 truncate';
            convTitle.textContent = conv.title;
            convTitle.onclick = () => {
                this.loadConversation(conv.id);
                this.clearDeepSearch();
            };
            convEl.appendChild(convTitle);
            
            // Show up to 3 matching snippets
            conv.matches.slice(0, 3).forEach(match => {
                const matchEl = document.createElement('div');
                matchEl.className = 'text-xs text-gray-400 py-1 pl-2 border-l-2 border-gray-700 hover:border-primary cursor-pointer transition-colors';
                
                // Highlight query in snippet
                const snippet = match.snippet.replace(
                    new RegExp(`(${query})`, 'gi'),
                    '<span class="text-primary font-medium">$1</span>'
                );
                matchEl.innerHTML = `<span class="text-gray-500">${match.role}:</span> ${snippet}`;
                matchEl.onclick = () => {
                    this.loadConversation(conv.id);
                    this.clearDeepSearch();
                    // TODO: scroll to message
                };
                convEl.appendChild(matchEl);
            });
            
            if (conv.matches.length > 3) {
                const moreEl = document.createElement('div');
                moreEl.className = 'text-xs text-gray-500 pl-2 mt-1';
                moreEl.textContent = `+${conv.matches.length - 3} more matches`;
                convEl.appendChild(moreEl);
            }
            
            list.appendChild(convEl);
        });
    }
    
    clearDeepSearch() {
        this.deepSearchResults = null;
        const searchInput = document.getElementById('conversation-search');
        if (searchInput) {
            searchInput.value = '';
            this.searchQuery = '';
        }
        document.getElementById('clear-search')?.classList.add('hidden');
        document.getElementById('deep-search-hint')?.classList.add('hidden');
        
        // Reset empty state
        const emptyEl = document.getElementById('conversations-empty');
        if (emptyEl) {
            emptyEl.innerHTML = `
                <span class="material-symbols-outlined text-4xl mb-2 opacity-50">chat_bubble_outline</span>
                <p>No conversations yet</p>
                <p class="text-xs mt-1">Start a new chat to begin</p>
            `;
        }
        
        this.filterAndRenderConversations();
    }

    groupConversationsByDate(conversations) {
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);
        const weekAgo = new Date(today);
        weekAgo.setDate(weekAgo.getDate() - 7);

        const groups = {
            today: [],
            yesterday: [],
            week: [],
            older: []
        };

        conversations.forEach(conv => {
            const convDate = new Date(conv.updated_at);
            if (convDate >= today) {
                groups.today.push(conv);
            } else if (convDate >= yesterday) {
                groups.yesterday.push(conv);
            } else if (convDate >= weekAgo) {
                groups.week.push(conv);
            } else {
                groups.older.push(conv);
            }
        });

        return groups;
    }

    renderConversationList(conversations) {
        const list = document.getElementById('conversation-list');
        const loadingEl = document.getElementById('conversations-loading');
        const emptyEl = document.getElementById('conversations-empty');
        
        // Hide loading state
        if (loadingEl) loadingEl.classList.add('hidden');
        
        // Clear existing conversation items (but not loading/empty states)
        const existingItems = list.querySelectorAll('button, .space-y-1');
        existingItems.forEach(item => item.remove());

        if (conversations.length === 0) {
            if (emptyEl) emptyEl.classList.remove('hidden');
            // Announce to screen readers
            this.announceToScreenReader('No conversations found');
            return;
        }
        
        // Hide empty state when we have conversations
        if (emptyEl) emptyEl.classList.add('hidden');

        const groups = this.groupConversationsByDate(conversations);

        const renderGroup = (title, convs) => {
            if (convs.length === 0) return;

            const groupEl = document.createElement('div');
            groupEl.className = 'space-y-1 mb-4';
            groupEl.innerHTML = `
                <div class="px-3 text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">${title}</div>
            `;

            convs.forEach(conv => {
                const isActive = conv.id === this.currentConversationId;
                const convTitle = conv.title || 'New Chat';
                const item = document.createElement('button');
                item.className = `w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors truncate group relative ${
                    isActive
                        ? 'bg-surface-dark/80 border border-gray-700/50 text-white font-medium shadow-sm'
                        : 'text-gray-400 hover:bg-surface-dark/50 hover:text-gray-200'
                }`;
                item.dataset.id = conv.id;
                item.setAttribute('role', 'listitem');
                item.setAttribute('aria-label', `${convTitle}${isActive ? ', currently selected' : ''}`);
                item.setAttribute('aria-current', isActive ? 'true' : 'false');

                if (isActive) {
                    item.innerHTML = `
                        <span class="block truncate pr-12">${this.escapeHtml(convTitle)}</span>
                        <div class="absolute inset-y-0 left-0 w-1 bg-primary rounded-l-lg" aria-hidden="true"></div>
                        <div class="absolute right-2 top-1/2 -translate-y-1/2 hidden group-hover:flex gap-1">
                            <span class="conv-action p-1 hover:bg-white/10 rounded text-gray-400 hover:text-white" data-action="rename" title="Rename" role="button" aria-label="Rename conversation">
                                <span class="material-symbols-outlined text-sm" aria-hidden="true">edit</span>
                            </span>
                            <span class="conv-action p-1 hover:bg-white/10 rounded text-gray-400 hover:text-red-400" data-action="delete" title="Delete" role="button" aria-label="Delete conversation">
                                <span class="material-symbols-outlined text-sm" aria-hidden="true">delete</span>
                            </span>
                        </div>
                    `;
                } else {
                    item.innerHTML = `
                        <span class="block truncate pr-12">${this.escapeHtml(convTitle)}</span>
                        <div class="absolute right-2 top-1/2 -translate-y-1/2 hidden group-hover:flex gap-1">
                            <span class="conv-action p-1 hover:bg-white/10 rounded text-gray-400 hover:text-white" data-action="rename" title="Rename" role="button" aria-label="Rename conversation">
                                <span class="material-symbols-outlined text-sm" aria-hidden="true">edit</span>
                            </span>
                            <span class="conv-action p-1 hover:bg-white/10 rounded text-gray-400 hover:text-red-400" data-action="delete" title="Delete" role="button" aria-label="Delete conversation">
                                <span class="material-symbols-outlined text-sm" aria-hidden="true">delete</span>
                            </span>
                        </div>
                    `;
                }

                // Main click to load conversation
                item.addEventListener('click', (e) => {
                    if (e.target.closest('.conv-action')) return;
                    this.loadConversation(conv.id);
                    if (this.isMobileView) {
                        this.closeSidebar();
                    }
                });

                // Action buttons
                item.querySelectorAll('.conv-action').forEach(btn => {
                    btn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        const action = btn.dataset.action;
                        if (action === 'rename') {
                            this.showRenameModal(conv.id, conv.title);
                        } else if (action === 'delete') {
                            this.deleteConversation(conv.id);
                        }
                    });
                });

                groupEl.appendChild(item);
            });

            list.appendChild(groupEl);
        };

        renderGroup('Today', groups.today);
        renderGroup('Yesterday', groups.yesterday);
        renderGroup('Previous 7 Days', groups.week);
        renderGroup('Older', groups.older);
    }

    async loadConversation(convId) {
        try {
            const response = await fetch(`/api/chat/conversations/${convId}`, {
                credentials: 'include'
            });
            if (!response.ok) throw new Error('Conversation not found');

            const conv = await response.json();
            this.setCurrentConversation(convId);

            // Update model badge to reflect conversation-scoped model
            if (conv.model) {
                this.currentModel = conv.model;
                this.updateModelBadge(conv.model);
            }

            // Update chat title in header
            const chatTitle = document.getElementById('current-chat-title');
            if (chatTitle) chatTitle.textContent = conv.title || 'New Chat';

            // Update sidebar active state
            await this.loadConversations();

            // Render messages
            this.chatManager.renderConversation(conv);
        } catch (error) {
            console.error('Failed to load conversation:', error);
            sessionStorage.removeItem('currentConversationId');
        }
    }

    setCurrentConversation(convId) {
        this.currentConversationId = convId;
        if (convId) {
            sessionStorage.setItem('currentConversationId', convId);
            // Persist across mobile tab discards (fallback). This may restore the last convo in a new tab,
            // but user can always click "New Chat" to reset.
            localStorage.setItem('brinchat_last_conversation_id', convId);
        } else {
            sessionStorage.removeItem('currentConversationId');
            localStorage.removeItem('brinchat_last_conversation_id');
        }
    }

    async createNewConversation() {
        this.setCurrentConversation(null);
        const titleEl = document.getElementById('current-chat-title');
        if (titleEl) titleEl.textContent = 'New Chat';
        this.chatManager.clearMessages();
        await this.loadConversations();
    }

    async deleteConversation(convId) {
        if (!confirm('Delete this conversation?')) return;

        try {
            await fetch(`/api/chat/conversations/${convId}`, {
                method: 'DELETE',
                credentials: 'include'
            });
            if (convId === this.currentConversationId) {
                this.setCurrentConversation(null);
                const titleEl = document.getElementById('current-chat-title');
                if (titleEl) titleEl.textContent = 'New Chat';
                this.chatManager.clearMessages();
            }
            await this.loadConversations();
        } catch (error) {
            console.error('Failed to delete conversation:', error);
        }
    }

    showRenameModal(convId, currentTitle) {
        const modal = document.getElementById('rename-modal');
        const input = document.getElementById('rename-input');
        input.value = currentTitle || '';
        input.dataset.convId = convId;
        modal.classList.remove('hidden');
        input.focus();
    }

    async saveRename() {
        const input = document.getElementById('rename-input');
        const convId = input.dataset.convId;
        const newTitle = input.value.trim();

        if (!newTitle) return;

        try {
            await fetch(`/api/chat/conversations/${convId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ title: newTitle })
            });
            document.getElementById('rename-modal').classList.add('hidden');
            if (convId === this.currentConversationId) {
                const titleEl = document.getElementById('current-chat-title');
                if (titleEl) titleEl.textContent = newTitle;
            }
            await this.loadConversations();
        } catch (error) {
            console.error('Failed to rename conversation:', error);
        }
    }

    toggleSidebar() {
        const sidebar = document.getElementById('sidebar');
        const overlay = document.getElementById('sidebar-overlay');

        const isHidden = sidebar.classList.contains('-translate-x-full');

        if (isHidden) {
            // Show sidebar
            sidebar.classList.remove('-translate-x-full');
            if (this.isMobileView) {
                overlay.classList.remove('hidden');
            } else {
                this.sidebarCollapsed = false;
            }
            this.hideMenuButton();
        } else {
            // Hide sidebar
            sidebar.classList.add('-translate-x-full');
            overlay.classList.add('hidden');
            if (!this.isMobileView) {
                this.sidebarCollapsed = true;
            }
            this.showMenuButton();
        }
    }

    closeSidebar() {
        const sidebar = document.getElementById('sidebar');
        const overlay = document.getElementById('sidebar-overlay');

        sidebar.classList.add('-translate-x-full');
        overlay.classList.add('hidden');

        // Track collapsed state and show menu button
        if (!this.isMobileView) {
            this.sidebarCollapsed = true;
        }
        this.showMenuButton();
    }

    setupEventListeners() {
        const sidebar = document.getElementById('sidebar');
        const overlay = document.getElementById('sidebar-overlay');

        // Sidebar toggle (mobile)
        document.getElementById('sidebar-toggle')?.addEventListener('click', () => {
            this.toggleSidebar();
        });

        // Sidebar close button (mobile)
        document.getElementById('sidebar-close-btn')?.addEventListener('click', () => {
            this.closeSidebar();
        });

        // Sidebar collapse (desktop) - uses same toggle logic
        document.getElementById('sidebar-collapse-btn')?.addEventListener('click', () => {
            this.toggleSidebar();
        });

        // Overlay click closes sidebar
        overlay?.addEventListener('click', () => {
            this.closeSidebar();
        });

        // New chat button
        document.getElementById('new-chat-btn')?.addEventListener('click', () => {
            this.createNewConversation();
            if (this.isMobileView) {
                this.closeSidebar();
            }
        });

        // Conversation search
        const searchInput = document.getElementById('conversation-search');
        const clearSearchBtn = document.getElementById('clear-search');

        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                this.searchQuery = e.target.value;
                clearSearchBtn.classList.toggle('hidden', !this.searchQuery);
                this.filterAndRenderConversations();
            });

            clearSearchBtn.addEventListener('click', () => {
                searchInput.value = '';
                this.searchQuery = '';
                clearSearchBtn.classList.add('hidden');
                this.filterAndRenderConversations();
            });

            // Clear search on Escape, deep search on Enter
            searchInput.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    this.clearDeepSearch();
                    searchInput.blur();
                } else if (e.key === 'Enter' && this.searchQuery.trim()) {
                    e.preventDefault();
                    this.deepSearchMessages(this.searchQuery.trim());
                }
            });
        }

        // Model selection removed - Claude only mode

        // Settings button
        document.getElementById('settings-btn')?.addEventListener('click', () => {
            this.settingsManager.showModal();
        });

        // Tools menu toggle
        const toolsBtn = document.getElementById('tools-btn');
        const toolsMenu = document.getElementById('tools-menu');

        toolsBtn?.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            toolsMenu?.classList.toggle('hidden');
        });

        // Close tools menu when clicking outside
        document.addEventListener('click', (e) => {
            if (toolsMenu && !toolsMenu.classList.contains('hidden') && !e.target.closest('#tools-menu-container')) {
                toolsMenu.classList.add('hidden');
            }
        });

        // File upload
        document.getElementById('file-upload')?.addEventListener('change', (e) => {
            this.handleDroppedFiles(Array.from(e.target.files));
            e.target.value = '';
        });

        // Menu: Attach files
        document.getElementById('menu-attach-files')?.addEventListener('click', () => {
            toolsMenu?.classList.add('hidden');
            document.getElementById('file-upload')?.click();
        });

        // Menu: Thinking toggle
        const thinkingCheckbox = document.getElementById('thinking-checkbox');
        const modeIndicator = document.getElementById('mode-indicator');

        document.getElementById('menu-thinking')?.addEventListener('click', (e) => {
            if (e.target.type !== 'checkbox' && thinkingCheckbox) {
                thinkingCheckbox.checked = !thinkingCheckbox.checked;
            }
            this.thinkEnabled = thinkingCheckbox?.checked ?? false;
            modeIndicator?.classList.toggle('hidden', !this.thinkEnabled);
        });

        thinkingCheckbox?.addEventListener('change', () => {
            this.thinkEnabled = thinkingCheckbox.checked;
            modeIndicator?.classList.toggle('hidden', !this.thinkEnabled);
        });

        // Drag and drop
        const inputArea = document.getElementById('input-area');
        let dragCounter = 0;

        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            document.body.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
            });
        });

        document.body.addEventListener('dragenter', () => {
            dragCounter++;
            inputArea?.classList.add('ring-2', 'ring-primary');
        });

        document.body.addEventListener('dragleave', () => {
            dragCounter--;
            if (dragCounter === 0) {
                inputArea?.classList.remove('ring-2', 'ring-primary');
            }
        });

        document.body.addEventListener('drop', (e) => {
            dragCounter = 0;
            inputArea?.classList.remove('ring-2', 'ring-primary');
            const files = Array.from(e.dataTransfer.files);
            this.handleDroppedFiles(files);
        });

        // Message input
        const messageInput = document.getElementById('message-input');
        messageInput?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        messageInput?.addEventListener('input', () => {
            messageInput.style.height = 'auto';
            messageInput.style.height = Math.min(messageInput.scrollHeight, 192) + 'px';
        });

        // Clipboard paste for images (screenshots)
        messageInput?.addEventListener('paste', async (e) => {
            const items = e.clipboardData?.items;
            if (!items) return;

            for (const item of items) {
                if (item.type.startsWith('image/')) {
                    e.preventDefault();
                    const blob = item.getAsFile();

                    // Handle null blob
                    if (!blob) {
                        console.warn('[Paste] getAsFile() returned null for:', item.type);
                        window.chatManager?.showToast('Could not read clipboard image', 'error');
                        break;
                    }

                    // Size validation (10MB limit)
                    const MAX_PASTE_SIZE = 10 * 1024 * 1024;
                    if (blob.size > MAX_PASTE_SIZE) {
                        console.warn('[Paste] Image too large:', blob.size);
                        window.chatManager?.showToast('Image too large (max 10MB)', 'warning');
                        break;
                    }

                    console.log('[Paste] Processing image:', blob.type, blob.size, 'bytes');

                    const reader = new FileReader();

                    reader.onerror = () => {
                        console.error('[Paste] FileReader error:', reader.error);
                        window.chatManager?.showToast('Failed to process pasted image', 'error');
                    };

                    reader.onload = () => {
                        const dataUrl = reader.result;
                        if (!dataUrl || typeof dataUrl !== 'string') {
                            console.error('[Paste] Invalid dataUrl result');
                            window.chatManager?.showToast('Failed to read image data', 'error');
                            return;
                        }

                        const base64 = dataUrl.split(',')[1];
                        if (!base64) {
                            console.error('[Paste] Failed to extract base64 from dataUrl');
                            window.chatManager?.showToast('Failed to process image', 'error');
                            return;
                        }

                        this.pendingImages.push({
                            base64,
                            dataUrl,
                            name: `clipboard-${Date.now()}.png`
                        });
                        this.updateImagePreviews();
                        window.chatManager?.showToast('Image pasted from clipboard', 'success');
                        console.log('[Paste] Image added successfully');
                    };

                    reader.readAsDataURL(blob);
                    break;
                }
            }
        });

        // Send button
        document.getElementById('send-btn')?.addEventListener('click', () => {
            this.sendMessage();
        });

        // Rename modal
        document.getElementById('close-rename')?.addEventListener('click', () => {
            document.getElementById('rename-modal')?.classList.add('hidden');
        });
        document.getElementById('cancel-rename')?.addEventListener('click', () => {
            document.getElementById('rename-modal')?.classList.add('hidden');
        });
        document.getElementById('save-rename')?.addEventListener('click', () => {
            this.saveRename();
        });
        document.getElementById('rename-input')?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                this.saveRename();
            }
        });

        // Edit modal
        document.getElementById('close-edit')?.addEventListener('click', () => {
            document.getElementById('edit-modal')?.classList.add('hidden');
        });
        document.getElementById('cancel-edit')?.addEventListener('click', () => {
            document.getElementById('edit-modal')?.classList.add('hidden');
        });
        document.getElementById('save-edit')?.addEventListener('click', () => {
            this.chatManager.saveEdit();
        });

        // ESC key to close modals
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                const renameModal = document.getElementById('rename-modal');
                const editModal = document.getElementById('edit-modal');
                if (renameModal && !renameModal.classList.contains('hidden')) {
                    renameModal.classList.add('hidden');
                }
                if (editModal && !editModal.classList.contains('hidden')) {
                    editModal.classList.add('hidden');
                }
            }
        });

        // Global keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
            const cmdKey = isMac ? e.metaKey : e.ctrlKey;
            
            // Don't trigger shortcuts when typing in inputs
            const isTyping = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName);
            
            // Ctrl/Cmd + N: New chat
            if (cmdKey && e.key === 'n' && !e.shiftKey && !isTyping) {
                e.preventDefault();
                this.createNewConversation();
            }
            
            // Ctrl/Cmd + /: Toggle sidebar
            if (cmdKey && e.key === '/' && !e.shiftKey) {
                e.preventDefault();
                this.toggleSidebar();
            }
            
            // Ctrl/Cmd + K: Focus search
            if (cmdKey && e.key === 'k' && !e.shiftKey) {
                e.preventDefault();
                const searchInput = document.getElementById('conversation-search');
                if (searchInput) {
                    // Ensure sidebar is visible on mobile
                    if (this.isMobileView) {
                        this.toggleSidebar();
                    }
                    searchInput.focus();
                }
            }
            
            // / (forward slash): Focus message input (when not typing)
            if (e.key === '/' && !isTyping && !cmdKey) {
                e.preventDefault();
                document.getElementById('message-input')?.focus();
            }
        });

    }

    /**
     * Update all locations that display the assistant name
     * @param {string} name - The new assistant name
     */
    updateAssistantName(name) {
        const displayName = name || 'BrinChat';
        console.log('[AssistantName] Updating assistant name to:', displayName);

        // Update sidebar header
        const sidebarHeader = document.querySelector('#sidebar h2.font-display');
        if (sidebarHeader) {
            sidebarHeader.textContent = displayName;
        }

        // Update welcome message if visible
        const welcomeTitle = document.querySelector('.welcome-message h2');
        if (welcomeTitle) {
            welcomeTitle.textContent = `Welcome to ${displayName}`;
        }

        // Update input placeholder
        const messageInput = document.getElementById('message-input');
        if (messageInput) {
            messageInput.placeholder = `Message ${displayName}...`;
        }

        // Update all existing assistant message headers
        const assistantNameEls = document.querySelectorAll('.assistant-header .assistant-name');
        assistantNameEls.forEach(el => {
            el.textContent = displayName;
        });

        // Store for use by chat manager
        this.assistantName = displayName;
    }

    /**
     * Get the current assistant name
     */
    getAssistantName() {
        // Try to get from profileManager first, fall back to cached value or default
        if (typeof profileManager !== 'undefined' && profileManager.getAssistantName) {
            const name = profileManager.getAssistantName();
            console.log('[AssistantName] getAssistantName() from profile:', name);
            return name;
        }
        console.log('[AssistantName] getAssistantName() fallback:', this.assistantName || 'BrinChat');
        return this.assistantName || 'BrinChat';
    }

    async selectModel(model) {
        if (!model) return;
        try {
            const payload = {
                model,
                conversation_id: this.currentConversationId,
                apply_default: true
            };

            const res = await fetch('/api/models/select', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                const txt = await res.text();
                throw new Error(txt || 'Model select failed');
            }

            this.currentModel = model;
            this.updateModelBadge(model);

            // Refresh sidebar metadata (model field) and keep user in the same conversation
            await this.loadConversations();
        } catch (err) {
            console.error('[selectModel] Failed:', err);
            window.chatManager?.showToast?.('Failed to switch model', 'warning');
        }
    }

    handleDroppedFiles(files) {
        const imageFiles = [];
        const otherFiles = [];

        files.forEach(file => {
            if (file.type.startsWith('image/')) {
                imageFiles.push(file);
            } else {
                otherFiles.push(file);
            }
        });

        if (imageFiles.length > 0) {
            this.handleImageUpload(imageFiles);
        }
        if (otherFiles.length > 0) {
            this.handleFileUpload(otherFiles);
        }
    }

    handleImageUpload(files) {
        const MAX_IMAGE_SIZE = 10 * 1024 * 1024; // 10MB limit

        Array.from(files).forEach(file => {
            // Skip if file with same name already exists
            if (this.pendingImages.some(img => img.name === file.name)) {
                console.log('[ImageUpload] Skipping duplicate:', file.name);
                return;
            }

            // Size validation
            if (file.size > MAX_IMAGE_SIZE) {
                console.warn('[ImageUpload] Image too large:', file.name, file.size);
                window.chatManager?.showToast(`Image "${file.name}" too large (max 10MB)`, 'warning');
                return;
            }

            console.log('[ImageUpload] Processing:', file.name, file.type, file.size, 'bytes');

            const reader = new FileReader();

            reader.onerror = () => {
                console.error('[ImageUpload] FileReader error for', file.name, reader.error);
                window.chatManager?.showToast(`Failed to process image: ${file.name}`, 'error');
            };

            reader.onload = (e) => {
                const dataUrl = e.target.result;
                if (!dataUrl || typeof dataUrl !== 'string') {
                    console.error('[ImageUpload] Invalid dataUrl for', file.name);
                    window.chatManager?.showToast(`Failed to read image: ${file.name}`, 'error');
                    return;
                }

                const base64 = dataUrl.split(',')[1];
                if (!base64) {
                    console.error('[ImageUpload] Failed to extract base64 for', file.name);
                    window.chatManager?.showToast(`Failed to process image: ${file.name}`, 'error');
                    return;
                }

                this.pendingImages.push({
                    base64,
                    dataUrl,
                    name: file.name
                });
                this.updateImagePreviews();
                window.chatManager?.showToast(`Image "${file.name}" added`, 'success');
                console.log('[ImageUpload] Image added successfully:', file.name);
            };

            reader.readAsDataURL(file);
        });
    }

    updateImagePreviews() {
        const container = document.getElementById('image-previews');
        if (!container) return;
        container.innerHTML = '';

        this.pendingImages.forEach((img, index) => {
            const preview = document.createElement('div');
            preview.className = 'relative size-14 rounded-lg overflow-hidden bg-surface-dark border border-gray-700';
            preview.innerHTML = `
                <img src="${img.dataUrl}" alt="${this.escapeHtml(img.name)}" class="w-full h-full object-cover">
                <button class="absolute top-0.5 right-0.5 size-5 bg-red-500 hover:bg-red-600 rounded-full flex items-center justify-center text-white text-xs transition-colors">
                    <span class="material-symbols-outlined text-sm">close</span>
                </button>
            `;
            preview.querySelector('button').addEventListener('click', () => {
                this.pendingImages.splice(index, 1);
                this.updateImagePreviews();
            });
            container.appendChild(preview);
        });
    }

    handleFileUpload(files) {
        Array.from(files).forEach(file => {
            // Skip if file with same name already exists
            if (this.pendingFiles.some(f => f.name === file.name)) {
                console.log('[FileUpload] Skipping duplicate:', file.name);
                return;
            }

            if (file.size > this.maxFileSize) {
                console.warn('[FileUpload] File too large:', file.name, file.size);
                window.chatManager?.showToast(`File "${file.name}" exceeds the 25 MB limit`, 'warning');
                return;
            }

            const fileType = this.getFileType(file.name);
            const isText = this.isTextFile(file.name);

            console.log('[FileUpload] Processing:', file.name, fileType, file.size, 'bytes');

            const reader = new FileReader();

            reader.onerror = () => {
                console.error('[FileUpload] FileReader error for', file.name, reader.error);
                window.chatManager?.showToast(`Failed to process file: ${file.name}`, 'error');
            };

            reader.onload = (e) => {
                const result = e.target.result;
                if (!result) {
                    console.error('[FileUpload] Empty result for', file.name);
                    window.chatManager?.showToast(`Failed to read file: ${file.name}`, 'error');
                    return;
                }

                let content;
                if (isText) {
                    content = result;
                } else {
                    content = result.split(',')[1];
                    if (!content) {
                        console.error('[FileUpload] Failed to extract base64 for', file.name);
                        window.chatManager?.showToast(`Failed to process file: ${file.name}`, 'error');
                        return;
                    }
                }

                this.pendingFiles.push({
                    name: file.name,
                    type: fileType,
                    content: content,
                    size: file.size,
                    isBase64: !isText
                });
                this.updateFilePreviews();
                window.chatManager?.showToast(`File "${file.name}" added`, 'success');
                console.log('[FileUpload] File added successfully:', file.name);
            };

            if (isText) {
                reader.readAsText(file);
            } else {
                reader.readAsDataURL(file);
            }
        });
    }

    getFileType(filename) {
        const ext = filename.split('.').pop().toLowerCase();
        const typeMap = {
            'pdf': 'pdf', 'zip': 'zip', 'txt': 'text', 'md': 'text',
            'json': 'code', 'xml': 'code', 'csv': 'text', 'py': 'code',
            'js': 'code', 'ts': 'code', 'jsx': 'code', 'tsx': 'code',
            'html': 'code', 'css': 'code', 'java': 'code', 'c': 'code',
            'cpp': 'code', 'h': 'code', 'go': 'code', 'rs': 'code',
            'rb': 'code', 'php': 'code', 'sh': 'code', 'yaml': 'code',
            'yml': 'code', 'toml': 'code', 'ini': 'text', 'cfg': 'text', 'log': 'text'
        };
        return typeMap[ext] || 'text';
    }

    isTextFile(filename) {
        const ext = filename.split('.').pop().toLowerCase();
        const binaryExtensions = ['pdf', 'zip'];
        return !binaryExtensions.includes(ext);
    }

    formatFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    updateFilePreviews() {
        const container = document.getElementById('file-previews');
        container.innerHTML = '';

        const icons = {
            pdf: 'picture_as_pdf',
            zip: 'folder_zip',
            text: 'description',
            code: 'code'
        };

        this.pendingFiles.forEach((file, index) => {
            const preview = document.createElement('div');
            preview.className = 'flex items-center gap-2 px-3 py-2 bg-surface-dark border border-gray-700 rounded-lg text-sm';
            preview.innerHTML = `
                <span class="material-symbols-outlined text-primary text-lg">${icons[file.type] || icons.text}</span>
                <span class="text-gray-300 truncate max-w-[120px]" title="${file.name}">${file.name}</span>
                <span class="text-gray-500 text-xs">${this.formatFileSize(file.size)}</span>
                <button class="text-gray-400 hover:text-red-400 transition-colors ml-1">
                    <span class="material-symbols-outlined text-sm">close</span>
                </button>
            `;
            preview.querySelector('button').addEventListener('click', () => {
                this.pendingFiles.splice(index, 1);
                this.updateFilePreviews();
            });
            container.appendChild(preview);
        });
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
     * Escape HTML to prevent XSS in dynamic content.
     * @param {string} text - Text to escape
     * @returns {string} - Escaped HTML
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    async sendMessage() {
        const input = document.getElementById('message-input');
        const message = input.value.trim();

        if (!message && this.pendingImages.length === 0 && this.pendingFiles.length === 0) return;

        // Store message data BEFORE clearing UI state
        const images = this.pendingImages.map(img => img.base64);
        const imageDataUrls = this.pendingImages.map(img => img.dataUrl);
        const files = this.pendingFiles.map(f => ({
            name: f.name,
            type: f.type,
            content: f.content,
            is_base64: f.isBase64
        }));
        const think = this.thinkEnabled;

        // Store original values in case we need to restore on error
        const originalMessage = input.value;
        const originalImages = [...this.pendingImages];
        const originalFiles = [...this.pendingFiles];

        // Clear UI state optimistically for better UX
        input.value = '';
        input.style.height = 'auto';
        this.pendingImages = [];
        this.pendingFiles = [];
        this.updateImagePreviews();
        this.updateFilePreviews();

        try {
            await this.chatManager.sendMessage(message, images, imageDataUrls, think, files);
            // Only refresh conversations after successful send
            await this.loadConversations();
        } catch (error) {
            console.error('[SendMessage] Failed to send message:', error);
            // Restore input state on error so user doesn't lose their message
            input.value = originalMessage;
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 192) + 'px';
            this.pendingImages = originalImages;
            this.pendingFiles = originalFiles;
            this.updateImagePreviews();
            this.updateFilePreviews();
            // Error toast is already shown by chatManager.sendMessage
        }
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new App();
    // Expose chatManager globally for modal onclick handlers
    window.chatManager = window.app.chatManager;
    window.app.init();
});
