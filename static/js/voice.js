/**
 * Voice Manager - ChatGPT-inspired voice UX
 * 
 * Mode 1: Transcription Mode (default)
 * - Click mic to record, click again to stop
 * - Auto-stop on silence detection
 * - Text appears in input for editing before send
 * 
 * Mode 2: Conversation Mode (toggle)
 * - Full duplex voice conversation
 * - Auto-send transcription, auto-play response
 * - Visual overlay with listening/speaking states
 */

export class VoiceManager {
    constructor(app) {
        this.app = app;
        
        // Settings
        this.settings = {
            voice_mode: 'disabled',     // disabled, transcribe_only, tts_only, conversation
            tts_voice: 'default',
            tts_speed: 1.0,
            auto_play: false,
            auto_send: true,            // Auto-send transcribed text (voice assistant mode)
            stt_language: 'en',
            voice_enabled: false
        };

        // Recording state
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.isRecording = false;
        this.recordingStream = null;
        this.silenceTimer = null;
        this.audioAnalyser = null;
        this.audioSourceNode = null;    // Track source node for cleanup
        this.silenceThreshold = 0.13;   // Audio level threshold for silence detection (0-1 scale, raised for noisy environments)
        this.silenceDelay = 1000;       // 1.0s of silence before auto-stop

        // VAD state tracking
        this.hasDetectedSpeech = false;  // Track if we've detected any speech
        this.speechStartTime = null;     // When speech started
        this.minRecordingTime = 500;     // Minimum recording time (ms) to avoid accidental triggers

        // Playback state
        this.audioContext = null;
        this.currentAudio = null;
        this.isPlaying = false;

        // Conversation mode state
        this.conversationMode = false;
        this.conversationOverlay = null;

        // Parallel conversation mode state
        this.isTranscribing = false;
        this.isWaitingForResponse = false;
        this.canInterrupt = false;  // True after response starts arriving

        // Animation frame for visualizer
        this.animationFrame = null;

        // Anti-hallucination settings
        this.minAudioDurationMs = 500;  // Reject recordings shorter than 500ms
        this.minAudioEnergy = 0.005;    // Reject near-silent recordings (RMS energy threshold)
        this.recordingStartedAt = null; // Timestamp when recording started
        this.recordingEnergySum = 0;    // Sum of audio energy samples during recording
        this.recordingEnergySamples = 0; // Number of energy samples taken

        // Known Whisper hallucination phrases (normalized to lowercase, trimmed)
        this.WHISPER_HALLUCINATIONS = new Set([
            'thank you.',
            'thank you',
            'thanks for watching.',
            'thanks for watching',
            'thanks for watching!',
            'thank you for watching.',
            'thank you for watching',
            'thank you for watching!',
            'subscribe',
            'please subscribe',
            'please subscribe.',
            'like and subscribe',
            'like and subscribe.',
            'thanks.',
            'thanks',
            'bye.',
            'bye',
            'you',
            'the end.',
            'the end',
            'so',
            'hmm',
            'uh',
            'oh',
        ]);

        // Streaming STT settings (experimental)
        this.useStreamingSTT = false;  // Toggle for experimental streaming mode
        this.streamingWS = null;
        this.streamingBuffer = '';
        this.streamingProcessor = null;
        this.streamingAudioContext = null;

        this.init();
    }

    async init() {
        try {
            await this.loadSettings();
            this.createConversationOverlay();
            this.setupEventListeners();
            this.updateMicButtonState();
            console.log('[Voice] Initialized:', this.settings.voice_enabled ? 'enabled' : 'disabled');
        } catch (error) {
            console.error('[Voice] Init failed:', error);
        }
    }

    async loadSettings() {
        try {
            const response = await fetch('/api/voice/settings', { credentials: 'include' });
            if (response.ok) {
                this.settings = await response.json();
            }
        } catch (error) {
            console.warn('[Voice] Failed to load settings:', error);
        }
    }

    // ==================== UI Setup ====================

    createConversationOverlay() {
        // Create conversation mode overlay (Mode 2)
        const overlay = document.createElement('div');
        overlay.id = 'voice-conversation-overlay';
        overlay.className = 'fixed inset-0 bg-black/90 z-[100] hidden flex flex-col items-center justify-center';
        overlay.innerHTML = `
            <div class="text-center space-y-8">
                <!-- State indicator -->
                <div id="voice-state-indicator" class="text-6xl">
                    <div id="voice-listening" class="hidden">
                        <div class="relative">
                            <div class="size-32 rounded-full bg-primary/20 animate-ping absolute inset-0"></div>
                            <div class="size-32 rounded-full bg-primary/30 flex items-center justify-center relative">
                                <span class="material-symbols-outlined text-6xl text-primary">mic</span>
                            </div>
                        </div>
                        <p class="text-white text-xl mt-6">Listening...</p>
                    </div>
                    <div id="voice-processing" class="hidden">
                        <div class="size-32 rounded-full bg-yellow-500/30 flex items-center justify-center">
                            <span class="material-symbols-outlined text-6xl text-yellow-400 animate-pulse">sync</span>
                        </div>
                        <p class="text-white text-xl mt-6">Processing...</p>
                    </div>
                    <div id="voice-speaking" class="hidden">
                        <div class="size-32 rounded-full bg-green-500/30 flex items-center justify-center">
                            <span class="material-symbols-outlined text-6xl text-green-400">volume_up</span>
                        </div>
                        <p class="text-white text-xl mt-6">Speaking...</p>
                    </div>
                </div>
                
                <!-- Waveform visualizer -->
                <canvas id="voice-waveform" class="w-64 h-16 mx-auto"></canvas>
                
                <!-- Transcription preview -->
                <div id="voice-transcript" class="text-gray-300 text-lg max-w-md mx-auto min-h-[60px]"></div>
                
                <!-- Action buttons -->
                <div class="flex gap-4 mt-8">
                    <!-- Manual send button (tap to send current recording immediately) -->
                    <button id="send-voice-now" class="hidden px-6 py-3 bg-primary/20 hover:bg-primary/30 border border-primary/50 rounded-full text-primary transition-colors">
                        <span class="flex items-center gap-2">
                            <span class="material-symbols-outlined">send</span>
                            Send Now
                        </span>
                    </button>
                    
                    <!-- Exit button -->
                    <button id="exit-conversation-mode" class="px-6 py-3 bg-red-500/20 hover:bg-red-500/30 border border-red-500/50 rounded-full text-red-400 transition-colors">
                        <span class="flex items-center gap-2">
                            <span class="material-symbols-outlined">close</span>
                            End Conversation
                        </span>
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        this.conversationOverlay = overlay;
    }

    setupEventListeners() {
        // Unlock audio context on first user interaction anywhere on page.
        // This ensures TTS autoplay works even for typed messages when auto_play is on.
        const unlockOnce = () => {
            this.unlockAudio();
            document.removeEventListener('click', unlockOnce);
            document.removeEventListener('keydown', unlockOnce);
        };
        document.addEventListener('click', unlockOnce);
        document.addEventListener('keydown', unlockOnce);

        // Also unlock on send button click
        const sendBtn = document.getElementById('send-btn');
        if (sendBtn) {
            sendBtn.addEventListener('click', () => this.unlockAudio());
        }

        // Main mic button (Mode 1: Transcription)
        const micBtn = document.getElementById('voice-input-btn');
        if (micBtn) {
            // Single click for transcription mode
            micBtn.addEventListener('click', (e) => {
                e.preventDefault();
                this.handleMicClick();
            });
            
            // Long press (500ms) to enter conversation mode
            let pressTimer = null;
            micBtn.addEventListener('mousedown', () => {
                pressTimer = setTimeout(() => {
                    if (!this.isRecording) {
                        this.enterConversationMode();
                    }
                }, 500);
            });
            micBtn.addEventListener('mouseup', () => clearTimeout(pressTimer));
            micBtn.addEventListener('mouseleave', () => clearTimeout(pressTimer));
        }

        // Send voice now button (manual send in conversation mode)
        const sendVoiceBtn = document.getElementById('send-voice-now');
        if (sendVoiceBtn) {
            sendVoiceBtn.addEventListener('click', () => {
                if (this.conversationMode && this.isRecording) {
                    console.debug('[Voice] Manual send triggered');
                    this.stopRecording();
                }
            });
        }

        // Exit conversation mode button
        const exitBtn = document.getElementById('exit-conversation-mode');
        if (exitBtn) {
            exitBtn.addEventListener('click', () => this.exitConversationMode());
        }

        // ESC key to exit conversation mode
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.conversationMode) {
                this.exitConversationMode();
            }
        });
    }

    updateMicButtonState() {
        const micBtn = document.getElementById('voice-input-btn');
        if (!micBtn) return;

        const canRecord = this.settings.voice_enabled && 
            (this.settings.voice_mode === 'transcribe_only' || 
             this.settings.voice_mode === 'conversation');

        if (canRecord) {
            micBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            micBtn.title = 'Click to record (hold for conversation mode)';
        } else {
            micBtn.classList.add('opacity-50', 'cursor-not-allowed');
            micBtn.title = 'Voice input disabled';
        }
    }

    // ==================== Mode 1: Transcription ====================

    async handleMicClick() {
        // Unlock audio on user gesture so TTS autoplay works later
        await this.unlockAudio();

        if (!this.canRecord()) {
            this.showToast('Voice input is not enabled. Enable in Settings → Voice.', 'warning');
            return;
        }

        if (this.isRecording) {
            await this.stopRecording();
        } else {
            await this.startRecording();
        }
    }

    canRecord() {
        return this.settings.voice_enabled && 
            (this.settings.voice_mode === 'transcribe_only' || 
             this.settings.voice_mode === 'conversation');
    }

    async startRecording() {
        try {
            this.recordingStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    sampleRate: 16000,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                }
            });

            // Check if user interrupted response
            if (this.conversationMode && this.canInterrupt) {
                console.log('[Voice] User interrupted response - stopping TTS');
                this.stopCurrentAudio();
            }

            // Check if using streaming mode (experimental)
            if (this.useStreamingSTT) {
                console.log('[Voice] Using streaming STT mode');
                await this.startStreamingSTT();
                this.isRecording = true;
                this.updateRecordingUI(true);

                // Update voice button aria state
                const micBtn = document.getElementById('voice-input-btn');
                if (micBtn) micBtn.setAttribute('aria-pressed', 'true');
                this.announceToScreenReader('Recording started with streaming transcription.');
                return;
            }

            // Original batch mode
            // Set up audio analyser for silence detection

            // Disconnect previous source node if exists
            if (this.audioSourceNode) {
                try {
                    this.audioSourceNode.disconnect();
                    console.debug('[Voice] Disconnected previous audio source node');
                } catch (e) {
                    // Already disconnected, ignore
                }
            }

            // Reuse AudioContext if it exists, create new one if needed
            if (!this.audioContext || this.audioContext.state === 'closed') {
                this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
                console.debug('[Voice] Created new AudioContext for VAD');
            } else {
                console.debug('[Voice] Reusing existing AudioContext for VAD');
            }

            this.audioSourceNode = this.audioContext.createMediaStreamSource(this.recordingStream);
            this.audioAnalyser = this.audioContext.createAnalyser();
            this.audioAnalyser.fftSize = 256;
            this.audioSourceNode.connect(this.audioAnalyser);

            // Start recording
            this.audioChunks = [];
            this.mediaRecorder = new MediaRecorder(this.recordingStream, {
                mimeType: this.getSupportedMimeType()
            });

            this.mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) {
                    this.audioChunks.push(e.data);
                }
            };

            this.mediaRecorder.onstop = () => this.processRecording();

            this.mediaRecorder.start(100);
            this.isRecording = true;
            this.recordingStartedAt = Date.now();
            this.recordingEnergySum = 0;
            this.recordingEnergySamples = 0;
            this.updateRecordingUI(true);
            this.startSilenceDetection();

            // Update voice button aria state and announce to screen readers
            const micBtn = document.getElementById('voice-input-btn');
            if (micBtn) micBtn.setAttribute('aria-pressed', 'true');
            this.announceToScreenReader('Recording started. Click again or wait for silence to stop.');

            console.debug('[Voice] Recording started');
        } catch (error) {
            console.error('[Voice] Microphone access denied:', error);
            this.showToast('Microphone access denied. Please allow microphone access.', 'error');
        }
    }

    async stopRecording() {
        if (!this.isRecording) return;

        // Handle streaming mode
        if (this.useStreamingSTT && this.streamingWS) {
            this.stopStreamingSTT();
            this.isRecording = false;

            if (this.recordingStream) {
                this.recordingStream.getTracks().forEach(t => t.stop());
                this.recordingStream = null;
            }

            this.updateRecordingUI(false);

            // Update voice button aria state
            const micBtn = document.getElementById('voice-input-btn');
            if (micBtn) micBtn.setAttribute('aria-pressed', 'false');

            console.debug('[Voice] Streaming recording stopped');
            return;
        }

        // Original batch mode
        if (!this.mediaRecorder) return;

        this.stopSilenceDetection();
        this.mediaRecorder.stop();
        this.isRecording = false;

        if (this.recordingStream) {
            this.recordingStream.getTracks().forEach(t => t.stop());
            this.recordingStream = null;
        }

        // Disconnect audio source node (but keep AudioContext for reuse)
        if (this.audioSourceNode) {
            try {
                this.audioSourceNode.disconnect();
                this.audioSourceNode = null;
            } catch (e) {
                // Already disconnected, ignore
            }
        }

        this.updateRecordingUI(false);

        // Update voice button aria state
        const micBtn = document.getElementById('voice-input-btn');
        if (micBtn) micBtn.setAttribute('aria-pressed', 'false');

        console.debug('[Voice] Recording stopped');
    }

    startSilenceDetection() {
        const bufferLength = this.audioAnalyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);
        let silenceStart = null;
        let lastLogTime = 0;

        // Reset speech detection state
        this.hasDetectedSpeech = false;
        this.speechStartTime = Date.now();

        console.log(`[Voice VAD] Starting silence detection - threshold: ${this.silenceThreshold}, delay: ${this.silenceDelay}ms`);

        const checkSilence = () => {
            if (!this.isRecording) {
                console.debug('[Voice VAD] Stopping - not recording');
                return;
            }

            try {
                this.audioAnalyser.getByteFrequencyData(dataArray);
                const average = dataArray.reduce((a, b) => a + b) / bufferLength / 255;

                // Track energy for hallucination rejection
                this.recordingEnergySum += average;
                this.recordingEnergySamples++;

                // Log audio levels every 2 seconds for debugging
                const now = Date.now();
                if (now - lastLogTime > 2000) {
                    console.debug(`[Voice VAD] Audio level: ${average.toFixed(4)} (threshold: ${this.silenceThreshold}, speech detected: ${this.hasDetectedSpeech})`);
                    lastLogTime = now;
                }

                // Speech detection threshold - slightly lower than silence threshold
                const speechThreshold = this.silenceThreshold * 0.8;

                if (average >= speechThreshold) {
                    // Detected speech
                    if (!this.hasDetectedSpeech) {
                        this.hasDetectedSpeech = true;
                        console.debug('[Voice VAD] Speech detected!');
                    }
                    silenceStart = null;  // Reset silence timer when speaking
                } else if (average < this.silenceThreshold) {
                    // Silence detected
                    if (!silenceStart) {
                        silenceStart = Date.now();
                        if (this.hasDetectedSpeech) {
                            console.debug('[Voice VAD] Silence started');
                        }
                    } else {
                        const silenceDuration = Date.now() - silenceStart;
                        const recordingDuration = Date.now() - this.speechStartTime;

                        // Only auto-stop if:
                        // 1. We've detected speech at some point
                        // 2. We've recorded for at least minRecordingTime
                        // 3. Silence has lasted for silenceDelay
                        if (this.hasDetectedSpeech &&
                            recordingDuration > this.minRecordingTime &&
                            silenceDuration > this.silenceDelay) {
                            console.debug(`[Voice VAD] Auto-stopping: speech detected, ${silenceDuration}ms silence after ${recordingDuration}ms recording`);
                            this.stopRecording();
                            return;
                        }
                    }
                }
            } catch (error) {
                console.error('[Voice VAD] Error in checkSilence loop:', error);
                // Continue anyway
            }

            this.animationFrame = requestAnimationFrame(checkSilence);
        };

        checkSilence();
    }

    stopSilenceDetection() {
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
            this.animationFrame = null;
        }
    }

    /**
     * Check if a transcription is a known Whisper hallucination.
     * @param {string} text - The transcribed text
     * @returns {boolean} True if the text is a hallucination
     */
    isWhisperHallucination(text) {
        if (!text) return true;
        const normalized = text.toLowerCase().trim();

        // Empty or whitespace-only
        if (!normalized) return true;

        // Single punctuation mark or very short non-word
        if (normalized.length <= 2 && !/[a-z0-9]/i.test(normalized)) return true;

        // Check against known hallucination phrases
        if (this.WHISPER_HALLUCINATIONS.has(normalized)) {
            console.debug(`[Voice] Filtered Whisper hallucination: "${text}"`);
            return true;
        }

        // Also check without trailing punctuation
        const noPunct = normalized.replace(/[.!?,;:]+$/, '').trim();
        if (noPunct && this.WHISPER_HALLUCINATIONS.has(noPunct)) {
            console.debug(`[Voice] Filtered Whisper hallucination (depunct): "${text}"`);
            return true;
        }

        return false;
    }

    async processRecording() {
        if (this.audioChunks.length === 0) return;

        const audioBlob = new Blob(this.audioChunks, { type: this.getSupportedMimeType() });
        this.audioChunks = [];

        // --- Anti-hallucination checks (before sending to server) ---

        // 1. Minimum duration check
        const recordingDuration = this.recordingStartedAt ? Date.now() - this.recordingStartedAt : 0;
        if (recordingDuration < this.minAudioDurationMs) {
            console.debug(`[Voice] Recording too short (${recordingDuration}ms < ${this.minAudioDurationMs}ms), discarding`);
            this.updateRecordingUI(false, false);
            if (this.conversationMode) {
                // Silently restart listening in conversation mode
                this.startConversationListening();
            }
            return;
        }

        // 2. Audio energy check — reject near-silent recordings
        const avgEnergy = this.recordingEnergySamples > 0
            ? this.recordingEnergySum / this.recordingEnergySamples
            : 0;
        if (avgEnergy < this.minAudioEnergy) {
            console.debug(`[Voice] Recording too quiet (avgEnergy=${avgEnergy.toFixed(5)} < ${this.minAudioEnergy}), discarding`);
            this.updateRecordingUI(false, false);
            if (this.conversationMode) {
                // Silently restart listening in conversation mode
                this.startConversationListening();
            }
            return;
        }

        console.debug(`[Voice] Recording passed pre-checks: duration=${recordingDuration}ms, avgEnergy=${avgEnergy.toFixed(5)}`);

        // Show processing state
        this.updateRecordingUI(false, true);

        // Retry logic for network issues
        const maxRetries = 2;
        let lastError = null;

        for (let attempt = 0; attempt <= maxRetries; attempt++) {
            try {
                const formData = new FormData();
                formData.append('audio', audioBlob, 'recording.webm');
                formData.append('language', this.settings.stt_language);

                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 30000); // 30s timeout

                const response = await fetch('/api/voice/transcribe', {
                    method: 'POST',
                    credentials: 'include',
                    body: formData,
                    signal: controller.signal
                });

                clearTimeout(timeoutId);

                if (!response.ok) {
                    const errorText = await response.text().catch(() => 'Unknown error');
                    throw new Error(`Server error (${response.status}): ${errorText.slice(0, 100)}`);
                }

                const result = await response.json();
                const text = result.text?.trim();

                // 3. Post-transcription hallucination filter
                if (text && this.isWhisperHallucination(text)) {
                    console.debug(`[Voice] Whisper hallucination filtered: "${text}"`);
                    this.updateRecordingUI(false, false);
                    if (this.conversationMode) {
                        // Silently restart listening — don't show error toast
                        this.startConversationListening();
                    }
                    return;
                }

                if (text) {
                    if (this.conversationMode) {
                        // Mode 2: Auto-send in conversation mode
                        await this.handleConversationInput(text);
                    } else if (this.settings.auto_send) {
                        // Voice Assistant Mode: Auto-send transcription immediately
                        console.debug('[Voice] Auto-sending transcription:', text);
                        await this.autoSendTranscription(text);
                    } else {
                        // Legacy Mode: Insert into text input for manual editing/sending
                        this.insertTranscribedText(text);
                    }
                } else {
                    if (!this.conversationMode) {
                        this.showToast('No speech detected. Try speaking closer to the microphone.', 'warning');
                    } else {
                        // In conversation mode, silently restart
                        this.startConversationListening();
                    }
                }

                // Success - exit retry loop
                this.updateRecordingUI(false, false);
                return;

            } catch (error) {
                lastError = error;
                const isNetworkError = error.name === 'AbortError' || 
                                       error.name === 'TypeError' || 
                                       error.message.includes('fetch');
                
                if (attempt < maxRetries && isNetworkError) {
                    console.warn(`[Voice] Transcription attempt ${attempt + 1} failed, retrying...`, error);
                    await new Promise(r => setTimeout(r, 1000 * (attempt + 1))); // Exponential backoff
                    continue;
                }
                
                // Final attempt or non-retryable error
                console.error('[Voice] Transcription error:', error);
                
                if (error.name === 'AbortError') {
                    this.showToast('Transcription timed out. Please try again.', 'error');
                } else if (error.message.includes('fetch') || error.name === 'TypeError') {
                    this.showToast('Network error. Check your connection and try again.', 'error');
                } else {
                    this.showToast('Transcription failed. Please try again.', 'error');
                }
                break;
            }
        }

        this.updateRecordingUI(false, false);
    }

    /**
     * Auto-send transcribed text to the chat (voice assistant mode)
     * @param {string} text - The transcribed text to send
     */
    async autoSendTranscription(text) {
        if (!text || !this.app.chatManager) {
            console.warn('[Voice] Cannot auto-send: no text or chatManager');
            return;
        }

        // Check if chat is currently streaming (don't interrupt)
        if (this.app.chatManager.isStreaming) {
            console.debug('[Voice] Chat is streaming, inserting text instead');
            this.insertTranscribedText(text);
            return;
        }

        // Mark this as voice input so response will auto-speak
        this.app.chatManager.setVoiceInput(true);

        // Send the message directly
        try {
            await this.app.chatManager.sendMessage(text, [], [], false, []);
            console.debug('[Voice] Message sent successfully');
        } catch (error) {
            console.error('[Voice] Failed to send message:', error);
            // Fallback: insert into input field
            this.insertTranscribedText(text);
            this.showToast('Failed to send. Text added to input.', 'warning');
        }
    }

    insertTranscribedText(text) {
        const input = document.getElementById('message-input');
        if (!input) return;

        const currentText = input.value;
        const separator = currentText && !currentText.endsWith(' ') && !currentText.endsWith('\n') ? ' ' : '';
        input.value = currentText + separator + text;
        
        // Trigger input event for auto-resize
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.focus();
        
        // Move cursor to end
        input.setSelectionRange(input.value.length, input.value.length);
        
        console.debug('[Voice] Inserted transcription:', text);
    }

    // ==================== Streaming STT (Experimental) ====================

    async startStreamingSTT() {
        if (this.streamingWS) return;

        console.debug('[Voice] Starting streaming STT');

        // Connect to Whisper WebSocket
        try {
            this.streamingWS = new WebSocket('ws://10.10.10.124:5001/ws/transcribe');
        } catch (error) {
            console.error('[Voice] Failed to create WebSocket:', error);
            this.showToast('Streaming STT connection failed', 'error');
            return;
        }

        this.streamingWS.onopen = async () => {
            console.debug('[Voice] Streaming STT connected');

            // Create AudioContext for PCM conversion (16kHz for Whisper)
            this.streamingAudioContext = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: 16000
            });
            const source = this.streamingAudioContext.createMediaStreamSource(this.recordingStream);

            // Use ScriptProcessor to extract PCM (deprecated but widely supported)
            // TODO: Migrate to AudioWorklet for better performance
            this.streamingProcessor = this.streamingAudioContext.createScriptProcessor(4096, 1, 1);

            this.streamingProcessor.onaudioprocess = (e) => {
                if (!this.streamingWS || this.streamingWS.readyState !== WebSocket.OPEN) {
                    return;
                }

                // Convert float32 audio to int16 PCM
                const audioData = e.inputBuffer.getChannelData(0);
                const pcm16 = new Int16Array(audioData.length);
                for (let i = 0; i < audioData.length; i++) {
                    pcm16[i] = Math.max(-32768, Math.min(32767, audioData[i] * 32768));
                }

                // Send PCM chunk to server
                this.streamingWS.send(pcm16.buffer);
            };

            source.connect(this.streamingProcessor);
            this.streamingProcessor.connect(this.streamingAudioContext.destination);
        };

        this.streamingWS.onmessage = (event) => {
            const result = JSON.parse(event.data);

            if (result.type === 'partial') {
                // Show interim transcription
                this.showInterimTranscript(result.text);
            } else if (result.type === 'final') {
                // Use final transcription
                this.streamingBuffer = result.text;
                this.handleTranscription(result.text);
            }
        };

        this.streamingWS.onerror = (error) => {
            console.error('[Voice] Streaming STT error:', error);
            this.showToast('Streaming transcription error', 'error');
        };

        this.streamingWS.onclose = () => {
            console.debug('[Voice] Streaming STT disconnected');
            this.cleanupStreaming();
        };
    }

    stopStreamingSTT() {
        if (this.streamingWS) {
            try {
                this.streamingWS.send('stop');
            } catch (error) {
                console.warn('[Voice] Error sending stop signal:', error);
            }
            this.streamingWS.close();
            this.cleanupStreaming();
        }
    }

    cleanupStreaming() {
        if (this.streamingProcessor) {
            this.streamingProcessor.disconnect();
            this.streamingProcessor = null;
        }
        if (this.streamingAudioContext) {
            this.streamingAudioContext.close();
            this.streamingAudioContext = null;
        }
        this.streamingWS = null;
    }

    showInterimTranscript(text) {
        // Display partial transcription in conversation overlay
        const transcript = document.getElementById('voice-transcript');
        if (transcript && this.conversationMode) {
            transcript.textContent = text;
            transcript.style.fontStyle = 'italic';  // Visual indicator it's interim
        }
    }

    handleTranscription(text) {
        // Handle final transcription based on mode
        if (this.conversationMode) {
            this.handleConversationInput(text);
        } else if (this.settings.auto_send) {
            this.autoSendTranscription(text);
        } else {
            this.insertTranscribedText(text);
        }
    }

    updateRecordingUI(isRecording, isProcessing = false) {
        const micBtn = document.getElementById('voice-input-btn');
        const recordingRing = document.getElementById('recording-ring');
        if (!micBtn) return;

        const icon = micBtn.querySelector('.material-symbols-outlined');

        if (isProcessing) {
            icon.textContent = 'sync';
            icon.classList.add('animate-spin');
            micBtn.classList.remove('text-gray-400', 'text-red-500', 'recording-pulse');
            micBtn.classList.add('text-yellow-500');
            micBtn.title = 'Processing...';
            if (recordingRing) recordingRing.classList.add('hidden');
        } else if (isRecording) {
            icon.textContent = 'stop_circle';
            icon.classList.remove('animate-spin');
            micBtn.classList.remove('text-gray-400', 'text-yellow-500');
            micBtn.classList.add('text-red-500', 'recording-pulse');
            micBtn.title = 'Click to stop recording';
            if (recordingRing) recordingRing.classList.remove('hidden');
        } else {
            icon.textContent = 'mic';
            icon.classList.remove('animate-spin', 'recording-pulse');
            micBtn.classList.remove('text-red-500', 'text-yellow-500');
            micBtn.classList.add('text-gray-400');
            micBtn.title = 'Click to record (hold for conversation mode)';
            if (recordingRing) recordingRing.classList.add('hidden');
        }
    }

    // ==================== Mode 2: Conversation Mode ====================

    async enterConversationMode() {
        if (!this.canRecord()) {
            this.showToast('Voice features not enabled', 'warning');
            return;
        }

        console.log('[Voice] Entering conversation mode');

        // Unlock AudioContext for playback (must be called from user gesture)
        await this.unlockAudio();

        this.conversationMode = true;
        this.conversationOverlay.classList.remove('hidden');
        document.body.style.overflow = 'hidden';

        // Notify chat manager
        if (this.app.chatManager) {
            this.app.chatManager.setConversationMode(true);
        }

        // Start listening
        await this.startConversationListening();
    }

    async exitConversationMode() {
        console.log('[Voice] Exiting conversation mode');
        this.conversationMode = false;

        // Clear all parallel processing state flags
        this.isTranscribing = false;
        this.isWaitingForResponse = false;
        this.canInterrupt = false;

        // Notify chat manager
        if (this.app.chatManager) {
            this.app.chatManager.setConversationMode(false);
        }

        // Discard any audio chunks (don't send partial recordings)
        this.audioChunks = [];

        // Stop any active recording or playback
        if (this.mediaRecorder && this.isRecording) {
            this.stopSilenceDetection();
            this.isRecording = false;

            // Stop the recorder without triggering onstop handler
            this.mediaRecorder.onstop = null;
            this.mediaRecorder.stop();

            if (this.recordingStream) {
                this.recordingStream.getTracks().forEach(t => t.stop());
                this.recordingStream = null;
            }

            // Disconnect audio source node
            if (this.audioSourceNode) {
                try {
                    this.audioSourceNode.disconnect();
                    this.audioSourceNode = null;
                } catch (e) {
                    // Already disconnected, ignore
                }
            }
        }

        this.stopPlayback();

        this.conversationOverlay.classList.add('hidden');
        document.body.style.overflow = '';

        // Reset overlay state
        this.setConversationState('idle');
        this.updateRecordingUI(false);
    }

    setConversationState(state) {
        const listening = document.getElementById('voice-listening');
        const processing = document.getElementById('voice-processing');
        const speaking = document.getElementById('voice-speaking');
        const transcript = document.getElementById('voice-transcript');
        const sendVoiceBtn = document.getElementById('send-voice-now');

        [listening, processing, speaking].forEach(el => el?.classList.add('hidden'));

        switch (state) {
            case 'listening':
                listening?.classList.remove('hidden');
                if (sendVoiceBtn) sendVoiceBtn.classList.remove('hidden');
                break;
            case 'processing':
                processing?.classList.remove('hidden');
                if (sendVoiceBtn) sendVoiceBtn.classList.add('hidden');
                break;
            case 'speaking':
                speaking?.classList.remove('hidden');
                if (sendVoiceBtn) sendVoiceBtn.classList.add('hidden');
                break;
            case 'idle':
                if (transcript) transcript.textContent = '';
                if (sendVoiceBtn) sendVoiceBtn.classList.add('hidden');
                break;
        }
    }

    async startConversationListening() {
        if (!this.conversationMode) return;

        this.setConversationState('listening');
        await this.startRecording();
    }

    async handleConversationInput(text) {
        if (!text || !this.conversationMode) return;

        console.debug('[Voice] Processing input in parallel with listening');

        // Show transcript
        const transcript = document.getElementById('voice-transcript');
        if (transcript) {
            transcript.textContent = `"${text}"`;
        }

        this.setConversationState('processing');

        // Start listening immediately (don't await - parallel processing)
        this.startConversationListening();

        // Process transcription in background
        this.isWaitingForResponse = true;
        this.canInterrupt = false;

        try {
            // Send message (non-blocking from listening perspective)
            await this.sendMessageAndGetResponse(text);

            // Response is streaming now, allow interrupts
            this.canInterrupt = true;

        } catch (error) {
            console.error('[Voice] Conversation error:', error);
            this.showToast('Conversation interrupted. Listening again.', 'warning');

            // Always recover - restart listening if not already
            if (!this.isRecording) {
                await this.startConversationListening();
            }
        } finally {
            this.isWaitingForResponse = false;
        }
    }

    async sendMessageAndGetResponse(text) {
        // Use the chat manager to send message
        if (!this.app.chatManager) return null;

        return new Promise((resolve) => {
            // Store callback for when response is complete
            this.app.chatManager.onResponseComplete = (responseText) => {
                resolve(responseText);
            };

            // Mark this as voice input so response will auto-speak
            this.app.chatManager.setVoiceInput(true);

            // Send the message directly with the text parameter
            this.app.chatManager.sendMessage(text);
        });
    }

    // ==================== TTS Playback ====================

    async speakText(text, force = false) {
        console.debug('[Voice] speakText called - force:', force, 'canPlayTTS:', this.canPlayTTS(), 'voice_enabled:', this.settings.voice_enabled, 'voice_mode:', this.settings.voice_mode, 'auto_play:', this.settings.auto_play);
        
        // Check if TTS is enabled
        if (!force && !this.canPlayTTS()) {
            console.debug('[Voice] Skipped - not forced and canPlayTTS=false');
            return;
        }

        if (!this.settings.voice_enabled) {
            console.debug('[Voice] Voice not enabled on server - trying to reload settings...');
            // Settings might not have loaded yet - try reloading
            await this.loadSettings();
            if (!this.settings.voice_enabled) {
                console.debug('[Voice] Voice still not enabled after reload. Aborting.');
                return;
            }
            console.debug('[Voice] Settings reloaded successfully, voice_enabled:', this.settings.voice_enabled);
        }

        const cleanText = this.cleanTextForTTS(text);
        if (!cleanText) {
            console.log('[Voice] Skipped - cleanText is empty');
            return;
        }

        console.debug('[Voice] Speaking:', cleanText.substring(0, 50) + '...');
        
        // Ensure audio context is unlocked
        await this.unlockAudio();

        try {
            const response = await fetch('/api/voice/tts/stream?' + new URLSearchParams({
                text: cleanText,
                voice: this.settings.tts_voice,
                speed: this.settings.tts_speed.toString()
            }), { credentials: 'include' });

            if (!response.ok) throw new Error('TTS request failed');

            // Collect SSE chunks
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            const chunks = [];

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const text = decoder.decode(value);
                const lines = text.split('\n');

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(line.slice(6));
                            if (data.chunk) {
                                const bytes = Uint8Array.from(atob(data.chunk), c => c.charCodeAt(0));
                                chunks.push(bytes);
                            }
                        } catch (e) {
                            // Ignore parse errors
                        }
                    }
                }
            }

            if (chunks.length > 0) {
                await this.playAudioChunks(chunks);
            }
        } catch (error) {
            console.error('[Voice] TTS error:', error);
        }
    }

    /**
     * Unlock audio playback by creating/resuming an AudioContext.
     * Must be called from a user gesture (click/tap) handler to satisfy
     * browser autoplay policies. Subsequent Audio.play() calls will work
     * even outside a gesture as long as the context is running.
     */
    async unlockAudio() {
        if (!this._audioCtxForPlayback) {
            this._audioCtxForPlayback = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (this._audioCtxForPlayback.state === 'suspended') {
            await this._audioCtxForPlayback.resume();
            console.debug('[Voice] AudioContext unlocked for autoplay');
        }
    }

    async playAudioChunks(chunks) {
        const totalLength = chunks.reduce((sum, c) => sum + c.length, 0);
        const combined = new Uint8Array(totalLength);
        let offset = 0;
        for (const chunk of chunks) {
            combined.set(chunk, offset);
            offset += chunk.length;
        }

        const blob = new Blob([combined], { type: 'audio/wav' });
        const url = URL.createObjectURL(blob);

        return new Promise((resolve, reject) => {
            this.currentAudio = new Audio(url);
            this.isPlaying = true;

            this.currentAudio.onended = () => {
                this.isPlaying = false;
                URL.revokeObjectURL(url);
                resolve();
            };

            this.currentAudio.onerror = (e) => {
                console.error('[Voice] Audio playback error:', e);
                this.isPlaying = false;
                URL.revokeObjectURL(url);
                reject(e);
            };

            this.currentAudio.play().then(() => {
                console.debug('[Voice] Audio playback started successfully');
            }).catch((err) => {
                console.error('[Voice] Audio.play() rejected:', err.name, err.message);
                this.isPlaying = false;
                URL.revokeObjectURL(url);
                // If autoplay was blocked, show a toast so the user knows
                if (err.name === 'NotAllowedError') {
                    this.showToast('Browser blocked audio autoplay. Click anywhere on the page to enable.', 'warning');
                }
                reject(err);
            });
        });
    }

    stopPlayback() {
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio = null;
        }
        this.isPlaying = false;
    }

    stopCurrentAudio() {
        // Stop streaming audio player
        if (this.app.chatManager && this.app.chatManager.streamingAudioPlayer) {
            this.app.chatManager.streamingAudioPlayer.stop();
            this.app.chatManager.streamingAudioPlayer = null;
        }

        // Stop regular audio
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio = null;
        }

        this.isPlaying = false;
        console.log('[Voice] Audio interrupted by user');
    }

    canPlayTTS() {
        return this.settings.voice_enabled && 
            (this.settings.voice_mode === 'tts_only' || 
             this.settings.voice_mode === 'conversation');
    }

    cleanTextForTTS(text) {
        // Remove <details> blocks (collapsible sections are visual-only)
        text = text.replace(/<details[\s\S]*?<\/details>/gi, '');
        // Remove HTML tags that might remain
        text = text.replace(/<[^>]+>/g, '');
        // Remove code blocks
        text = text.replace(/```[\s\S]*?```/g, 'code block');
        // Remove inline code
        text = text.replace(/`[^`]+`/g, 'code');
        // Remove markdown links, keep text
        text = text.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
        // Remove markdown formatting
        text = text.replace(/[*_~]+/g, '');
        // Remove headers
        text = text.replace(/^#+\s*/gm, '');
        // Remove markdown tables
        text = text.replace(/^\|.*\|$/gm, '');
        // Remove horizontal rules
        text = text.replace(/^---+$/gm, '');
        // Collapse whitespace
        text = text.replace(/\s+/g, ' ');
        return text.trim();
    }

    // ==================== Auto-speak for chat responses ====================

    async autoSpeak(text) {
        if (this.settings.auto_play && this.canPlayTTS()) {
            await this.speakText(text);
        }
    }

    // ==================== Utilities ====================

    getSupportedMimeType() {
        const types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg', 'audio/wav'];
        for (const type of types) {
            if (MediaRecorder.isTypeSupported(type)) {
                return type;
            }
        }
        return 'audio/webm';
    }

    showToast(message, type = 'error') {
        if (this.app.chatManager?.showToast) {
            this.app.chatManager.showToast(message, type);
        } else {
            console.log(`[Toast ${type}]: ${message}`);
        }
    }

    /**
     * Announce a message to screen readers via the live region.
     * @param {string} message - Message to announce
     */
    announceToScreenReader(message) {
        const srAnnouncements = document.getElementById('sr-announcements');
        if (srAnnouncements) {
            srAnnouncements.textContent = message;
            setTimeout(() => { srAnnouncements.textContent = ''; }, 1000);
        }
    }

    cleanup() {
        this.stopRecording();
        this.stopPlayback();
        this.exitConversationMode();
    }
}

// ==================== Streaming Audio Player ====================

/**
 * StreamingAudioPlayer — Gapless playback of sequential audio chunks
 * using the Web Audio API.
 *
 * Chunks arrive as ArrayBuffers (WAV), are decoded into AudioBuffers,
 * and scheduled back-to-back on AudioBufferSourceNodes so there are
 * no micro-gaps between sentences.
 */
export class StreamingAudioPlayer {
    constructor(audioContext, onEnded = null) {
        this.ctx = audioContext;
        this.queue = [];          // { index, buffer } sorted by index
        this.nextIndex = 0;       // next index we expect to play
        this.nextStartTime = 0;   // Web Audio time for next node start
        this.playing = false;
        this.done = false;        // true once tts_done received
        this.activeNodes = [];
        this.onEnded = onEnded;   // Callback when all playback completes
    }

    /**
     * Enqueue an audio chunk for playback.
     * @param {ArrayBuffer} audioData - Raw audio bytes (WAV)
     * @param {number} index - Sequence number from server
     */
    async enqueue(audioData, index) {
        try {
            const buffer = await this.ctx.decodeAudioData(audioData);
            this.queue.push({ index, buffer });
            this.queue.sort((a, b) => a.index - b.index);
            console.debug(`[StreamPlayer] Enqueued chunk #${index} (${buffer.duration.toFixed(2)}s)`);
            this._scheduleNext();
        } catch (err) {
            console.error(`[StreamPlayer] Failed to decode chunk #${index}:`, err);
        }
    }

    /**
     * Signal that no more chunks will arrive.
     */
    markDone() {
        this.done = true;
        console.debug('[StreamPlayer] All chunks received');
    }

    /**
     * Stop all playback and clear the queue.
     */
    stop() {
        this.playing = false;
        this.done = true;
        this.queue = [];
        this.nextIndex = 0;
        this.nextStartTime = 0;
        for (const node of this.activeNodes) {
            try { node.stop(); } catch (_) { /* may already be stopped */ }
        }
        this.activeNodes = [];
    }

    /** @private Schedule queued buffers for gapless playback */
    _scheduleNext() {
        while (this.queue.length > 0 && this.queue[0].index === this.nextIndex) {
            const { buffer } = this.queue.shift();
            const source = this.ctx.createBufferSource();
            source.buffer = buffer;
            source.connect(this.ctx.destination);

            // Schedule start time for gapless playback
            const startAt = Math.max(this.ctx.currentTime, this.nextStartTime);
            source.start(startAt);
            this.nextStartTime = startAt + buffer.duration;
            this.nextIndex++;
            this.playing = true;

            this.activeNodes.push(source);

            source.onended = () => {
                const idx = this.activeNodes.indexOf(source);
                if (idx !== -1) this.activeNodes.splice(idx, 1);

                // If all nodes finished and no more chunks coming, we're done
                if (this.activeNodes.length === 0 && (this.done || this.queue.length === 0)) {
                    this.playing = false;
                    console.debug('[StreamPlayer] Playback complete');

                    // Call onEnded callback if provided
                    if (this.onEnded) {
                        this.onEnded();
                    }
                }
            };
        }
    }
}

// Global instance
export let voiceManager = null;

export function initVoiceManager(app) {
    voiceManager = new VoiceManager(app);
    return voiceManager;
}
