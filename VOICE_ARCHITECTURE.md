# BrinChat Voice Architecture - Conversation Mode

## Overview

This document describes the complete data flow and architecture of BrinChat's conversation mode, a parallel-processing voice-to-voice conversation system with real-time streaming TTS and interrupt capability.

## Complete Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ CONVERSATION MODE CYCLE (Parallel Processing)                               │
└─────────────────────────────────────────────────────────────────────────────┘

User Action: Press conversation mode button
    ↓
┌───────────────────────────────────────────────────────────────────────────┐
│ INITIALIZATION (voice.js:737)                                             │
│ • enterConversationMode()                                                 │
│ • chatManager.setConversationMode(true)  → Sets flag in chat.js          │
│ • Show overlay, unlock AudioContext                                       │
└───────────────────────────────────────────────────────────────────────────┘
    ↓

┌───────────────────────────────────────────────────────────────────────────┐
│ START LISTENING (voice.js:821)                                            │
│ • startRecording()                                                        │
│ • Data: MediaStream (raw audio from microphone)                          │
│ • MediaRecorder starts capturing                                          │
│ • VAD (Voice Activity Detection) monitors silence                         │
└───────────────────────────────────────────────────────────────────────────┘
    ↓
    │ [User speaks]
    ↓
┌───────────────────────────────────────────────────────────────────────────┐
│ RECORDING ACTIVE                                                           │
│ • Data: Audio chunks (Blob, WebM/Opus format)                            │
│ • Stored in: this.audioChunks[]                                           │
│ • Silence detection running (threshold: 0.13, delay: 1000ms)             │
└───────────────────────────────────────────────────────────────────────────┘
    ↓
    │ [Silence detected OR manual stop]
    ↓
┌───────────────────────────────────────────────────────────────────────────┐
│ STOP RECORDING (voice.js:329)                                             │
│ • stopRecording()                                                         │
│ • Triggers: mediaRecorder.onstop → processRecording()                    │
└───────────────────────────────────────────────────────────────────────────┘
    ↓

╔═══════════════════════════════════════════════════════════════════════════╗
║ PARALLEL PROCESSING BEGINS HERE (voice.js:845)                            ║
║ handleConversationInput() does NOT await completion                       ║
╚═══════════════════════════════════════════════════════════════════════════╝

    ┌─────────────────────────────────────┐
    │ BRANCH 1: RESTART LISTENING         │
    │ (IMMEDIATE - Non-blocking)          │
    └─────────────────────────────────────┘
         ↓
    ┌───────────────────────────────────────────────────────────────────────┐
    │ START LISTENING AGAIN (voice.js:859)                                  │
    │ • startConversationListening() called immediately                     │
    │ • User can speak while processing previous input                      │
    │ • State: canInterrupt = false initially                               │
    │ • Data: New MediaStream (ready for next input)                        │
    └───────────────────────────────────────────────────────────────────────┘
         ↓
    [Ready for next voice input within ~2 seconds]


    ┌─────────────────────────────────────┐
    │ BRANCH 2: PROCESS PREVIOUS INPUT   │
    │ (Background - Parallel)             │
    └─────────────────────────────────────┘
         ↓
    ┌───────────────────────────────────────────────────────────────────────┐
    │ TRANSCRIPTION (voice.js:447)                                          │
    │ • processRecording()                                                  │
    │ • Combine audio chunks into single Blob                               │
    │ • POST to /api/voice/transcribe                                       │
    │ • Input: Blob (audio/webm)                                            │
    │ • Output: JSON { text: "transcribed text" }                           │
    │ • State: isWaitingForResponse = true                                  │
    └───────────────────────────────────────────────────────────────────────┘
         ↓
    ┌───────────────────────────────────────────────────────────────────────┐
    │ SEND MESSAGE (voice.js:875)                                           │
    │ • chatManager.sendMessage(text)                                       │
    │ • Data: String (transcribed text)                                     │
    │ • Sets: lastInputWasVoice = true                                      │
    │ • Opens: SSE stream to /api/chat/stream                               │
    └───────────────────────────────────────────────────────────────────────┘
         ↓

    ╔═══════════════════════════════════════════════════════════════════════╗
    ║ SSE STREAM HANDLING (chat.js:1491-1930)                               ║
    ║ Server-Sent Events - Real-time streaming response                     ║
    ╚═══════════════════════════════════════════════════════════════════════╝
         ↓
    ┌───────────────────────────────────────────────────────────────────────┐
    │ STREAM: TOKENS (chat.js:1670-1690)                                    │
    │ • Event: data                                                         │
    │ • Data: JSON chunks { delta: "word", role: "assistant" }             │
    │ • Accumulated in: currentStreamContent (String)                       │
    │ • Display: Real-time markdown rendering                               │
    └───────────────────────────────────────────────────────────────────────┘
         ↓ (parallel with text streaming)

    ┌───────────────────────────────────────────────────────────────────────┐
    │ STREAM: TTS CHUNKS (chat.js:1756-1800)                               │
    │ • Event: data with tts_audio_url + tts_index                         │
    │ • Data format:                                                        │
    │   - tts_audio_url: String (URL to fetch)                             │
    │   - tts_index: Number (sequence number)                              │
    │ • Action: preFetchTTSAudio() - non-blocking fetch                    │
    └───────────────────────────────────────────────────────────────────────┘
         ↓
    ┌───────────────────────────────────────────────────────────────────────┐
    │ TTS PRE-FETCH (chat.js:56-88)                                         │
    │ • Parallel fetch of audio chunks                                      │
    │ • Input: URL (String)                                                 │
    │ • Fetch: ArrayBuffer (WAV audio data)                                 │
    │ • Store: ttsFetchedChunks Map<index, ArrayBuffer>                     │
    │ • Enqueue: streamingAudioPlayer.enqueue(arrayBuffer, index)           │
    └───────────────────────────────────────────────────────────────────────┘
         ↓
    ┌───────────────────────────────────────────────────────────────────────┐
    │ STREAMING AUDIO PLAYER (voice.js:1111-1191)                          │
    │ • StreamingAudioPlayer class                                          │
    │ • Input: ArrayBuffer (WAV)                                            │
    │ • Decode: AudioBuffer (Web Audio API)                                 │
    │ • Schedule: AudioBufferSourceNode (gapless playback)                  │
    │ • Queue: Sorted by index for correct order                            │
    │ • State: canInterrupt = true (after first chunk plays)               │
    └───────────────────────────────────────────────────────────────────────┘
         ↓ (audio playing in real-time)

    ┌───────────────────────────────────────────────────────────────────────┐
    │ USER CAN INTERRUPT HERE                                               │
    │ • If user starts speaking: startRecording() detects canInterrupt      │
    │ • Calls: stopCurrentAudio() → stops StreamingAudioPlayer              │
    │ • New cycle begins with new user input                                │
    └───────────────────────────────────────────────────────────────────────┘
         ↓ (if not interrupted)

    ┌───────────────────────────────────────────────────────────────────────┐
    │ STREAM: TEXT_COMPLETE (chat.js:1806-1812)                            │
    │ • Event: data with text_complete: true                               │
    │ • Response text is done (TTS still playing)                           │
    │ • Status: Change to 'idle' (hide "Generating..." banner)             │
    │ • Action: isStreaming = false (allow new text messages)              │
    └───────────────────────────────────────────────────────────────────────┘
         ↓
    ┌───────────────────────────────────────────────────────────────────────┐
    │ STREAM: TTS_DONE (chat.js:1814-1820)                                 │
    │ • Event: data with tts_done: true                                    │
    │ • All TTS chunks sent (playback still in progress)                    │
    │ • Action: streamingAudioPlayer.markDone()                             │
    └───────────────────────────────────────────────────────────────────────┘
         ↓
    ┌───────────────────────────────────────────────────────────────────────┐
    │ STREAM: FINISH_REASON (chat.js:1861-1912)                            │
    │ • Event: data with finish_reason: "stop"                             │
    │ • Stream complete                                                     │
    │ • Check: if ttsCompletionPromise exists, wait for it                  │
    │ • Callback: onResponseComplete() - called after TTS finishes          │
    └───────────────────────────────────────────────────────────────────────┘
         ↓
    ┌───────────────────────────────────────────────────────────────────────┐
    │ TTS PLAYBACK COMPLETE (voice.js:1179-1189)                           │
    │ • StreamingAudioPlayer detects all nodes finished                     │
    │ • Calls: onEnded callback                                             │
    │ • Resolves: ttsCompletionPromise in chat.js                           │
    │ • Triggers: onResponseComplete(responseText) in voice.js              │
    └───────────────────────────────────────────────────────────────────────┘
         ↓
    ┌───────────────────────────────────────────────────────────────────────┐
    │ CYCLE COMPLETE                                                         │
    │ • User is already listening (Branch 1 started ~8-10 seconds ago)      │
    │ • Ready for next input immediately                                    │
    │ • State: isWaitingForResponse = false, canInterrupt = false           │
    └───────────────────────────────────────────────────────────────────────┘
         ↓
    [Loop back to RECORDING ACTIVE]


┌─────────────────────────────────────────────────────────────────────────────┐
│ EXIT CONVERSATION MODE (voice.js:756)                                       │
│ • exitConversationMode()                                                    │
│ • Stop recording, stop playback, clear state flags                          │
│ • chatManager.setConversationMode(false)                                    │
│ • Hide overlay                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Type Reference

| Stage | Data Type | Format | Size | Location |
|-------|-----------|--------|------|----------|
| **Microphone Input** | MediaStream | Raw audio stream | N/A | Navigator API |
| **Recording Chunks** | Blob[] | audio/webm or audio/ogg | ~10KB/100ms | voice.js:audioChunks |
| **Combined Recording** | Blob | audio/webm or audio/ogg | ~100-500KB | FormData for upload |
| **Transcription Request** | FormData | multipart/form-data | ~100-500KB | POST /api/voice/transcribe |
| **Transcription Response** | JSON | `{ text: string }` | ~100 bytes | HTTP response |
| **Chat Message** | String | Plain text | ~100 bytes | chat.js:sendMessage() |
| **SSE Stream** | EventStream | text/event-stream | Streaming | /api/chat/stream |
| **Token Delta** | JSON | `{ delta: string }` | ~10-50 bytes | Per token |
| **TTS Chunk Metadata** | JSON | `{ tts_audio_url, tts_index }` | ~100 bytes | Per sentence |
| **TTS Chunk Fetch** | ArrayBuffer | WAV audio (16kHz mono) | ~50-200KB | Per sentence |
| **Decoded Audio** | AudioBuffer | PCM float32 samples | ~50-200KB | Web Audio API |
| **Audio Playback** | AudioBufferSourceNode | Scheduled PCM | N/A | Web Audio API |

## State Flags Timeline

```
Time →

User clicks conversation mode button
├─ conversationMode = true
├─ isConversationMode = true (chat.js)
└─ isRecording = true

User speaks
└─ [Recording...]

Silence detected
├─ isRecording = false
├─ isTranscribing = true
└─ isWaitingForResponse = true

┌─ START LISTENING AGAIN (Branch 1)
│  └─ isRecording = true (NEW CYCLE)
│
└─ [Parallel processing continues in Branch 2...]

Response starts streaming
├─ canInterrupt = true
└─ [User can interrupt now by speaking]

TTS chunks arrive & play
└─ [Audio playing while user can speak]

Text complete (text_complete received)
├─ isStreaming = false (new messages allowed)
└─ [TTS still playing, but user can type]

Response complete (finish_reason received)
├─ isWaitingForResponse = false
├─ canInterrupt = false
└─ [Cycle ready to repeat]

User exits conversation mode
├─ conversationMode = false
├─ isConversationMode = false
├─ isTranscribing = false
├─ isWaitingForResponse = false
└─ canInterrupt = false
```

## Critical Timing Improvements

| Event | Old (Sequential) | New (Parallel) | Improvement |
|-------|-----------------|----------------|-------------|
| **Silence → Listening Again** | ~10s (transcribe→response→TTS→2s delay) | ~2s (immediate) | **5x faster** |
| **Transcription** | 2-3s (blocks) | 2-3s (background) | Non-blocking |
| **Response Start** | +5-7s | +5-7s (but listening already active) | Parallel |
| **TTS Playback** | +3-5s (blocks) | +3-5s (interruptible) | Can be interrupted |
| **Ready for Next Input** | ~10s total | ~2s total | **5x faster** |

## Key Architectural Features

### 1. Parallel Processing
- **Problem Solved**: Sequential processing made conversation feel sluggish (10s between exchanges)
- **Solution**: Start listening immediately while processing previous input in background
- **Implementation**: `handleConversationInput()` calls `startConversationListening()` without awaiting processing
- **Files**: `voice.js:845-878`

### 2. Page Visibility Resilience
- **Problem Solved**: Switching browser tabs during conversation broke the stream and locked up
- **Solution**: Conversation mode streams ignore page visibility changes (regular chat still recovers)
- **Implementation**: `isConversationMode` flag prevents stream abort in visibility handler
- **Files**: `chat.js:142-153`, `voice.js:750-762`

### 3. Interrupt Capability
- **Problem Solved**: Users had to wait for TTS to finish before speaking again
- **Solution**: Detect when user starts speaking during playback and stop TTS
- **Implementation**: `canInterrupt` flag + `stopCurrentAudio()` in `startRecording()`
- **Files**: `voice.js:276-280`, `voice.js:1023-1042`

### 4. Proper TTS Completion Tracking
- **Problem Solved**: Hardcoded 2-second delay didn't track actual TTS duration
- **Solution**: Promise-based completion tracking with `onEnded` callback from StreamingAudioPlayer
- **Implementation**: `ttsCompletionPromise` resolves when audio actually finishes
- **Files**: `chat.js:1767-1790`, `voice.js:1112 (constructor)`, `voice.js:1179-1191 (_scheduleNext)`

### 5. Message Queue Unblocking
- **Problem Solved**: Text messages queued even after response was complete
- **Solution**: Set `isStreaming = false` when `text_complete` received (TTS can finish in background)
- **Implementation**: Allow new messages once text generation done
- **Files**: `chat.js:1806-1812`

## Key Files & Functions

| File | Key Functions | Purpose |
|------|--------------|---------|
| **voice.js** | `enterConversationMode()`, `handleConversationInput()`, `startRecording()`, `stopCurrentAudio()` | Main conversation orchestration |
| **chat.js** | `setConversationMode()`, `preFetchTTSAudio()`, `handleSSEMessage()` | Stream handling, TTS coordination |
| **voice.js** | `StreamingAudioPlayer` class | Gapless audio playback with completion tracking |
| **Backend** | `/api/voice/transcribe` (POST) | Whisper STT transcription |
| **Backend** | `/api/chat/stream` (SSE) | OpenClaw streaming response + TTS URLs |

## State Management

### Conversation Mode State Flags

```javascript
// voice.js - VoiceManager class
this.conversationMode = false;        // Overall conversation mode active
this.isTranscribing = false;          // Transcription in progress
this.isWaitingForResponse = false;    // Waiting for LLM response
this.canInterrupt = false;            // User can interrupt TTS
this.audioSourceNode = null;          // Current MediaStreamAudioSourceNode (reused across recordings)

// chat.js - ChatManager class
this.isConversationMode = false;      // Conversation mode tracking (set by voice.js)
this.isStreaming = false;             // Response streaming active
this.ttsCompletionPromise = null;     // Promise for TTS completion
this.streamingAudioPlayer = null;     // Audio player instance
```

### State Transitions

```
enterConversationMode()
  └─> conversationMode = true
  └─> chatManager.setConversationMode(true)

startRecording()
  └─> isRecording = true

stopRecording() → processRecording() → handleConversationInput()
  └─> isRecording = false
  └─> isTranscribing = true
  └─> isWaitingForResponse = true
  └─> startConversationListening() [parallel]
      └─> isRecording = true [NEW CYCLE]

sendMessage() → SSE stream starts
  └─> isStreaming = true

First TTS chunk received
  └─> canInterrupt = true

text_complete received
  └─> isStreaming = false

Response complete (finish_reason)
  └─> isWaitingForResponse = false

TTS playback complete (onEnded callback)
  └─> canInterrupt = false
  └─> onResponseComplete() called

exitConversationMode()
  └─> conversationMode = false
  └─> isTranscribing = false
  └─> isWaitingForResponse = false
  └─> canInterrupt = false
  └─> chatManager.setConversationMode(false)
```

## Debugging & Monitoring

### Console Log Messages

Monitor these logs to understand conversation flow:

```javascript
// Mode transitions
'[Chat] Conversation mode enabled'
'[Chat] Conversation mode disabled'

// Parallel processing
'[Voice] Processing input in parallel with listening'

// Interrupt detection
'[Voice] User interrupted response - stopping TTS'

// TTS completion tracking
'[Chat] Waiting for TTS to complete before calling onResponseComplete'
'[StreamPlayer] Playback complete'

// Message queueing
'[Chat] Message queued (position N): ...'
'[Chat] Text complete — hiding generating banner, allowing new messages'

// State transitions
'[Voice] Entering conversation mode'
'[Voice] Recording started'
'[Voice] Recording stopped'
'[Voice] Transcription complete'
'[Chat] finish_reason received: stop'
```

### Testing Checklist

- [ ] **Parallel Processing**: Speak, then speak again within 2 seconds (should work)
- [ ] **Page Visibility**: Switch tabs during conversation (should not break)
- [ ] **Interrupt**: Speak while TTS is playing (TTS should stop immediately)
- [ ] **TTS Completion**: Verify no hardcoded delays between exchanges
- [ ] **Message Queue**: Send text message after response completes (should not queue)
- [ ] **State Cleanup**: Exit conversation mode cleanly (no stuck states)
- [ ] **Long Responses**: Test with multi-sentence responses (gapless playback)
- [ ] **Error Recovery**: Test with network interruption (graceful degradation)

## Performance Characteristics

### Latency Budget

```
User stops speaking (silence detected)
  +0ms:     Stop recording
  +50ms:    Process audio chunks
  +100ms:   Start new listening cycle (parallel)
  +200ms:   POST transcription request
  +2000ms:  Transcription complete
  +2500ms:  LLM starts responding
  +3000ms:  First token received
  +4000ms:  First TTS chunk fetched
  +4500ms:  TTS playback starts

TOTAL TIME TO LISTENING AGAIN: ~100ms (immediate)
TOTAL TIME TO RESPONSE AUDIO: ~4.5s
```

### Resource Usage

- **Memory**: ~10-20MB for audio buffers during playback
- **Network**: ~50-200KB per TTS chunk (parallel fetch)
- **CPU**: ~5-10% during audio encoding/decoding
- **Audio Context**: Single persistent context, unlocked on first user gesture

## Bug Fixes

### AudioContext Reuse Fix (2026-02-09)

**Bug**: Voice Activity Detection (VAD) failed on second and subsequent recording rounds in conversation mode. Audio level was stuck at 0.0000, preventing silence detection from working properly.

**Root Cause**: `startRecording()` was creating a fresh AudioContext on every call, but wasn't properly disconnecting the previous `MediaStreamAudioSourceNode`. This left orphaned audio nodes that interfered with new recordings.

**Solution Implemented**:
1. **Added state tracking**: `this.audioSourceNode = null` in constructor to track the current audio source node
2. **Reuse AudioContext**: Only create new AudioContext if it doesn't exist or is closed
3. **Disconnect previous node**: Before creating new audioSourceNode, disconnect the previous one if it exists
4. **Cleanup on stop**: Disconnect audioSourceNode in `stopRecording()` and `exitConversationMode()`

**Code Changes**:
```javascript
// voice.js - VoiceManager constructor
this.audioSourceNode = null;

// voice.js - startRecording()
// Reuse existing AudioContext if available
if (!this.audioContext || this.audioContext.state === 'closed') {
    this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
}

// Disconnect previous audio source node if it exists
if (this.audioSourceNode) {
    this.audioSourceNode.disconnect();
}

// Create new source node for this recording
this.audioSourceNode = this.audioContext.createMediaStreamSource(stream);

// voice.js - stopRecording()
if (this.audioSourceNode) {
    this.audioSourceNode.disconnect();
    this.audioSourceNode = null;
}

// voice.js - exitConversationMode()
if (this.audioSourceNode) {
    this.audioSourceNode.disconnect();
    this.audioSourceNode = null;
}
```

**Files Modified**:
- `/home/brin/projects/BrinChat/static/js/voice.js`

**Impact**: VAD now works reliably across multiple recording cycles in conversation mode. Users can have extended back-and-forth voice conversations without the silence detection breaking after the first exchange.

**Testing**: Verified that audio levels are properly detected in conversation mode for 10+ consecutive exchanges.

---

## Future Improvements

### Potential Enhancements

1. **Streaming STT**: Real-time transcription with partial results (experimental code present, disabled by default)
2. **Voice Fine-tuning**: Bake Maya voice into model weights (see MEMORY.md)
3. **Adaptive Silence Threshold**: Auto-adjust based on ambient noise
4. **Multi-turn Context**: Maintain conversation history for better responses
5. **Local TTS Models**: Reduce latency with on-device TTS (Piper, Kokoro)
6. **WebRTC Audio**: Lower latency audio capture vs MediaRecorder
7. **Audio Worklet**: Replace ScriptProcessor for streaming STT

### Known Limitations

1. **V100 Concurrency**: Qwen3-TTS can only process one sentence at a time
2. **First Request Latency**: ~21s for first TTS request (x-vector extraction)
3. **Browser Compatibility**: Requires modern browser with Web Audio API support
4. **Mobile Safari**: AudioContext may require additional unlock gestures
5. **Background Tab**: Some browsers throttle timers (affects VAD)

## Related Documentation

- **Main Project**: `/home/brin/projects/BrinChat/CLAUDE.md`
- **Voice Setup**: Memory system "Voice Setup Status" section
- **OpenClaw Config**: Memory system "OpenClaw Config Lessons" section
- **Security**: Memory system "Security Audit" section
- **Fine-tuning Plans**: `~/clawd/projects/brinchat-uncensored/FINE_TUNING.md`

---

**Last Updated**: 2026-02-09
**Authors**: Jarvus (Claude Code), Joel
**Status**: Production (parallel processing deployed, AudioContext reuse bug fixed)
