// Settings Manager - Handles settings modal and persistence
export class SettingsManager {
    constructor(app) {
        this.app = app;
        this.modal = document.getElementById('settings-modal');
        this.settings = {};
        this.currentTheme = localStorage.getItem('theme') || 'dark';
        this.applyTheme(this.currentTheme);
        this.setupEventListeners();
    }

    setupEventListeners() {
        // Close button
        document.getElementById('close-settings').addEventListener('click', () => {
            this.hideModal();
        });

        // Click outside to close
        this.modal.addEventListener('click', (e) => {
            if (e.target === this.modal) {
                this.hideModal();
            }
        });

        // ESC key to close
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !this.modal.classList.contains('hidden')) {
                this.hideModal();
            }
        });

        // Save button
        document.getElementById('save-settings').addEventListener('click', () => {
            this.saveSettings();
        });

        // Theme buttons
        document.querySelectorAll('.theme-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const theme = btn.dataset.theme;
                this.setTheme(theme);
            });
        });

        // Range input value displays
        this.setupRangeInput('temperature', 'temp-value');
        this.setupRangeInput('top-p', 'topp-value');
        this.setupRangeInput('top-k', 'topk-value');
        this.setupRangeInput('num-ctx', 'ctx-value');
        this.setupRangeInput('repeat-penalty', 'repeat-value');

        // Compaction settings
        this.setupRangeInput('compaction-buffer', 'buffer-value', '%');
        this.setupRangeInput('compaction-threshold', 'threshold-value', '%');
        this.setupRangeInput('compaction-protected', 'protected-value');

        // Compaction enable toggle
        const compactionEnabled = document.getElementById('compaction-enabled');
        const compactionSettings = document.getElementById('compaction-settings');
        if (compactionEnabled && compactionSettings) {
            compactionEnabled.addEventListener('change', () => {
                compactionSettings.style.opacity = compactionEnabled.checked ? '1' : '0.5';
                compactionSettings.style.pointerEvents = compactionEnabled.checked ? 'auto' : 'none';
            });
        }

        // Voice settings
        this.setupRangeInput('tts-speed', 'tts-speed-value', 'x');
        this.setupVoiceSettingsListeners();
    }

    setupVoiceSettingsListeners() {
        const voiceMode = document.getElementById('voice-mode');
        const ttsVoice = document.getElementById('tts-voice');
        const ttsSpeedContainer = document.getElementById('tts-speed-container');
        const sttLanguageContainer = document.getElementById('stt-language-container');

        if (voiceMode) {
            voiceMode.addEventListener('change', () => {
                this.updateVoiceUIState();
            });
        }
        
        // Save voice settings when voice selection changes
        if (ttsVoice) {
            ttsVoice.addEventListener('change', () => {
                this.saveVoiceSettings();
            });
        }
    }

    updateVoiceUIState() {
        const voiceMode = document.getElementById('voice-mode');
        const ttsVoiceContainer = document.getElementById('tts-voice-container');
        const ttsSpeedContainer = document.getElementById('tts-speed-container');
        const sttLanguageContainer = document.getElementById('stt-language-container');
        const autoPlayContainer = document.getElementById('voice-auto-play')?.closest('.flex');
        const autoSendContainer = document.getElementById('auto-send-container');

        if (!voiceMode) return;

        const mode = voiceMode.value;
        const showTTS = mode === 'tts_only' || mode === 'conversation';
        const showSTT = mode === 'transcribe_only' || mode === 'conversation';

        if (ttsVoiceContainer) {
            ttsVoiceContainer.style.opacity = showTTS ? '1' : '0.5';
            ttsVoiceContainer.style.pointerEvents = showTTS ? 'auto' : 'none';
        }
        if (ttsSpeedContainer) {
            ttsSpeedContainer.style.opacity = showTTS ? '1' : '0.5';
            ttsSpeedContainer.style.pointerEvents = showTTS ? 'auto' : 'none';
        }
        if (autoPlayContainer) {
            autoPlayContainer.style.opacity = showTTS ? '1' : '0.5';
            autoPlayContainer.style.pointerEvents = showTTS ? 'auto' : 'none';
        }
        if (sttLanguageContainer) {
            sttLanguageContainer.style.opacity = showSTT ? '1' : '0.5';
            sttLanguageContainer.style.pointerEvents = showSTT ? 'auto' : 'none';
        }
        // Auto-send (voice assistant mode) only visible when STT is enabled
        if (autoSendContainer) {
            autoSendContainer.style.opacity = showSTT ? '1' : '0.5';
            autoSendContainer.style.pointerEvents = showSTT ? 'auto' : 'none';
        }
    }

    setTheme(theme) {
        this.currentTheme = theme;
        this.applyTheme(theme);
        localStorage.setItem('theme', theme);
        this.updateThemeButtons();
    }

    applyTheme(theme) {
        // Set data-theme attribute for CSS variable switching
        if (theme === 'dark') {
            document.documentElement.removeAttribute('data-theme');
        } else {
            document.documentElement.setAttribute('data-theme', theme);
        }

        // Toggle dark class for Tailwind
        if (theme === 'light') {
            document.documentElement.classList.remove('dark');
        } else {
            document.documentElement.classList.add('dark');
        }

        // Apply theme colors to key elements using CSS variables
        const body = document.body;
        const sidebar = document.getElementById('sidebar');
        const main = document.querySelector('main');
        const header = document.querySelector('header');
        const inputArea = document.getElementById('input-area');
        const chatContainer = document.getElementById('chat-container');
        const menuButton = document.getElementById('sidebar-toggle');

        // Apply background colors from CSS variables
        body.style.backgroundColor = 'var(--bg-primary)';
        body.style.color = 'var(--text-primary)';

        if (sidebar) sidebar.style.backgroundColor = 'var(--bg-sidebar)';
        if (main) main.style.backgroundColor = 'var(--bg-primary)';
        if (header) header.style.backgroundColor = 'var(--bg-primary)';
        if (inputArea) inputArea.style.backgroundColor = 'var(--bg-primary)';
        if (chatContainer) chatContainer.style.backgroundColor = 'var(--bg-primary)';
        if (menuButton) menuButton.style.backgroundColor = 'var(--bg-sidebar)';
    }

    updateThemeButtons() {
        document.querySelectorAll('.theme-btn').forEach(btn => {
            const isActive = btn.dataset.theme === this.currentTheme;
            if (isActive) {
                btn.classList.remove('border-gray-700');
                btn.classList.add('border-primary', 'bg-primary/10');
            } else {
                btn.classList.add('border-gray-700');
                btn.classList.remove('border-primary', 'bg-primary/10');
            }
        });
    }

    setupRangeInput(inputId, displayId, suffix = '') {
        const input = document.getElementById(inputId);
        const display = document.getElementById(displayId);

        if (!input || !display) return;  // Skip if elements don't exist

        input.addEventListener('input', () => {
            display.textContent = input.value + suffix;
        });
    }

    async loadSettings() {
        try {
            const response = await fetch('/api/settings');
            this.settings = await response.json();
            this.updateUI();

            // Also load voice settings
            await this.loadVoiceSettings();
        } catch (error) {
            console.error('Failed to load settings:', error);
        }
    }

    async loadVoiceSettings() {
        try {
            const response = await fetch('/api/voice/settings', { credentials: 'include' });
            if (response.ok) {
                const voiceSettings = await response.json();
                // Load available voices first, then update UI with current selection
                await this.loadAvailableVoices();
                this.updateVoiceUI(voiceSettings);
            }
        } catch (error) {
            console.warn('Failed to load voice settings:', error);
        }
    }

    async loadAvailableVoices() {
        const voiceSelect = document.getElementById('tts-voice');
        const voiceHint = document.getElementById('tts-voice-hint');
        
        if (!voiceSelect) return;
        
        try {
            const response = await fetch('/api/voice/voices', { credentials: 'include' });
            if (response.ok) {
                const data = await response.json();
                
                // Clear existing options except default
                voiceSelect.innerHTML = '<option value="default">Default</option>';
                
                if (data.success && data.voices && data.voices.length > 0) {
                    data.voices.forEach(voice => {
                        const option = document.createElement('option');
                        option.value = voice.id;
                        option.textContent = `${voice.id} - ${voice.language}${voice.gender ? ` (${voice.gender})` : ''}`;
                        option.title = voice.name;
                        voiceSelect.appendChild(option);
                    });
                    
                    if (voiceHint) {
                        voiceHint.textContent = `${data.voices.length} voices available (${data.backend})`;
                    }
                } else {
                    if (voiceHint) {
                        voiceHint.textContent = 'Using default voice';
                    }
                }
            } else {
                if (voiceHint) {
                    voiceHint.textContent = 'Voice selection unavailable';
                }
            }
        } catch (error) {
            console.warn('Failed to load available voices:', error);
            if (voiceHint) {
                voiceHint.textContent = 'Failed to load voices';
            }
        }
    }

    updateVoiceUI(settings) {
        const voiceMode = document.getElementById('voice-mode');
        const ttsVoice = document.getElementById('tts-voice');
        const ttsSpeed = document.getElementById('tts-speed');
        const ttsSpeedValue = document.getElementById('tts-speed-value');
        const autoPlay = document.getElementById('voice-auto-play');
        const autoSend = document.getElementById('voice-auto-send');
        const sttLanguage = document.getElementById('stt-language');
        const statusText = document.getElementById('voice-status-text');
        const voiceSection = document.getElementById('voice-settings-section');

        if (voiceMode) voiceMode.value = settings.voice_mode || 'disabled';
        if (ttsVoice) ttsVoice.value = settings.tts_voice || 'default';
        if (ttsSpeed) {
            ttsSpeed.value = settings.tts_speed || 1.0;
            if (ttsSpeedValue) ttsSpeedValue.textContent = `${settings.tts_speed || 1.0}x`;
        }
        if (autoPlay) autoPlay.checked = settings.auto_play || false;
        // auto_send defaults to true for voice assistant behavior
        if (autoSend) autoSend.checked = settings.auto_send !== false;
        if (sttLanguage) sttLanguage.value = settings.stt_language || 'en';

        // Update status text
        if (statusText) {
            if (settings.voice_enabled) {
                statusText.textContent = 'Voice features are available';
                statusText.classList.remove('text-gray-500');
                statusText.classList.add('text-green-400');
            } else {
                statusText.textContent = 'Voice features disabled on server (VOICE_ENABLED=false)';
            }
        }

        // Show/hide section based on server status
        if (voiceSection && !settings.voice_enabled) {
            // Keep section visible but show disabled state
        }

        this.updateVoiceUIState();
    }

    updateUI() {
        // Persona
        const personaInput = document.getElementById('persona-input');
        if (personaInput) {
            personaInput.value = this.settings.persona || '';
        }

        // Model parameters
        this.setRangeValue('temperature', 'temp-value', this.settings.temperature || 0.7);
        this.setRangeValue('top-p', 'topp-value', this.settings.top_p || 0.9);
        this.setRangeValue('top-k', 'topk-value', this.settings.top_k || 40);
        this.setRangeValue('num-ctx', 'ctx-value', this.settings.num_ctx || 4096);
        this.setRangeValue('repeat-penalty', 'repeat-value', this.settings.repeat_penalty || 1.1);

        // Compaction settings
        const compactionEnabled = document.getElementById('compaction-enabled');
        const compactionSettings = document.getElementById('compaction-settings');
        if (compactionEnabled) {
            compactionEnabled.checked = this.settings.compaction_enabled !== false;
            if (compactionSettings) {
                compactionSettings.style.opacity = compactionEnabled.checked ? '1' : '0.5';
                compactionSettings.style.pointerEvents = compactionEnabled.checked ? 'auto' : 'none';
            }
        }
        this.setRangeValue('compaction-buffer', 'buffer-value', this.settings.compaction_buffer_percent || 15, '%');
        this.setRangeValue('compaction-threshold', 'threshold-value', this.settings.compaction_threshold_percent || 70, '%');
        this.setRangeValue('compaction-protected', 'protected-value', this.settings.compaction_protected_messages || 6);
    }

    setRangeValue(inputId, displayId, value, suffix = '') {
        const input = document.getElementById(inputId);
        const display = document.getElementById(displayId);
        if (!input || !display) return;
        input.value = value;
        display.textContent = value + suffix;
    }

    async saveSettings() {
        const personaEl = document.getElementById('persona-input');
        const compactionEnabledEl = document.getElementById('compaction-enabled');
        const newSettings = {
            persona: personaEl ? personaEl.value || null : null,
            temperature: parseFloat(document.getElementById('temperature').value),
            top_p: parseFloat(document.getElementById('top-p').value),
            top_k: parseInt(document.getElementById('top-k').value),
            num_ctx: parseInt(document.getElementById('num-ctx').value),
            repeat_penalty: parseFloat(document.getElementById('repeat-penalty').value),
            // Compaction settings
            compaction_enabled: compactionEnabledEl ? compactionEnabledEl.checked : true,
            compaction_buffer_percent: parseInt(document.getElementById('compaction-buffer')?.value || 15),
            compaction_threshold_percent: parseInt(document.getElementById('compaction-threshold')?.value || 70),
            compaction_protected_messages: parseInt(document.getElementById('compaction-protected')?.value || 6)
        };

        try {
            // Save profile first if available and has changes
            if (typeof profileManager !== 'undefined' && profileManager.isDirty) {
                await profileManager.saveProfile();
                profileManager.markClean();
            }

            const response = await fetch('/api/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(newSettings)
            });

            if (response.ok) {
                this.settings = await response.json();

                // Save voice settings
                await this.saveVoiceSettings();

                this.hideModal();
                console.log('Settings saved:', this.settings);
            } else {
                throw new Error('Failed to save settings');
            }
        } catch (error) {
            console.error('Failed to save settings:', error);
            alert('Failed to save settings. Please try again.');
        }
    }

    async saveVoiceSettings() {
        const voiceMode = document.getElementById('voice-mode');
        const ttsVoice = document.getElementById('tts-voice');
        const ttsSpeed = document.getElementById('tts-speed');
        const autoPlay = document.getElementById('voice-auto-play');
        const autoSend = document.getElementById('voice-auto-send');
        const sttLanguage = document.getElementById('stt-language');

        const voiceSettings = {
            voice_mode: voiceMode?.value || 'disabled',
            tts_speed: parseFloat(ttsSpeed?.value || 1.0),
            auto_play: autoPlay?.checked || false,
            auto_send: autoSend?.checked !== false,  // Default to true
            stt_language: sttLanguage?.value || 'en',
            tts_voice: ttsVoice?.value || 'default'
        };

        try {
            const response = await fetch('/api/voice/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify(voiceSettings)
            });

            if (response.ok) {
                // Update voice manager if available
                if (this.app.voiceManager) {
                    await this.app.voiceManager.loadSettings();
                }
            }
        } catch (error) {
            console.warn('Failed to save voice settings:', error);
        }
    }

    showModal() {
        this.loadSettings().then(() => {
            this.updateThemeButtons();
            this.modal.classList.remove('hidden');

            // Initialize knowledge base manager
            knowledgeManager.init();

            // Initialize profile manager with force reload to get latest data
            if (typeof profileManager !== 'undefined') {
                profileManager.init(true);  // Force reload from server
            }
        });
    }

    hideModal() {
        this.modal.classList.add('hidden');
    }
}
