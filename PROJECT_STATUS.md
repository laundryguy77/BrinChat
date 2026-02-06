# BrinChat Project Status

**Last Updated:** 2026-01-31

## Current State: Production-Ready

BrinChat is a web-based chat interface for Claude via OpenClaw, with optional Lexi (uncensored) mode support. The codebase is clean and well-maintained.

---

## Recent Work Completed (2026-01-31)

### Accessibility Improvements (WCAG 2.1)
- ✅ **Skip to content link**: Keyboard users can jump to chat input
- ✅ **Screen reader announcements**: Live region for dynamic content updates
- ✅ **ARIA labels**: All interactive elements properly labeled
- ✅ **Proper modal roles**: `role="dialog"` and `aria-modal="true"` on all modals
- ✅ **Form accessibility**: Labels connected via `for` attributes, autocomplete hints
- ✅ **Conversation list**: `role="list"` and `role="listitem"` for navigation
- ✅ **Voice input state**: `aria-pressed` indicates recording state
- ✅ **Loading/empty states**: Conversation list shows spinner while loading, friendly empty message

### Security Hardening & Input Validation
- ✅ **Chat rate limiting added**: 30 messages/minute per user
- ✅ **Request payload limits added**:
  - Message: 100KB max
  - File content: 50MB max (base64)
  - Max 10 files, 5 images per request
  - File name: 255 chars max
- ✅ **Empty message validation**: Rejects whitespace-only messages
- ✅ **TTS health check fix**: Now uses proper endpoints

### Previous Sessions
- ✅ WebSocket 403 fix
- ✅ Debug logging cleanup + input validation
- ✅ Dead MCP code removed (~800 lines)
- ✅ Security review passed (XSS, CSRF, rate limiting, thread safety)
- ✅ Frontend XSS hardening added (DOMPurify)

---

## Architecture Overview

```
BrinChat (port 8081)
├── Frontend: HTML + Tailwind CSS + Vanilla JS
├── Backend: FastAPI + SQLite
├── Chat: OpenClaw API (Claude) or Ollama (Lexi)
└── Voice: Local Whisper STT (5001) + Qwen TTS (5002)
```

---

## Testing Status

### ✅ Verified Working
1. **Basic Chat Flow** - Messages send/receive correctly
2. **Rate Limiting** - Login (5/5min), Register (3/hour), Refresh (10/min), Chat (30/min)
3. **WebSocket** - Proper connection handling with idle timeout
4. **Conversation History** - Load, switch, rename, delete all work
5. **File Upload** - PDF, ZIP, code files processed correctly
6. **Security Headers** - XSS, clickjacking, MIME-sniffing protection
7. **Accessibility** - ARIA labels, screen reader support, keyboard navigation

### ⌨️ Keyboard Shortcuts
- **Ctrl/Cmd + N**: New conversation
- **Ctrl/Cmd + K**: Focus search
- **Ctrl/Cmd + /**: Toggle sidebar
- **/**: Focus message input (when not typing)
- **Enter**: Send message
- **Shift + Enter**: New line in message
- **Escape**: Close modals

### 🔧 Voice Services Status
- **Whisper STT (port 5001)**: ✅ Running (local, GPU-ready)
- **Qwen TTS (port 5002)**: ✅ Running (local, high quality)
- **Edge TTS**: ✅ Installed & configured as default (~0.5s latency)
- **BrinChat Voice Integration**: ✅ Working with Edge TTS backend

### 🔍 TTS Performance Options
| Backend | Latency | Quality | Requires Internet | GPU |
|---------|---------|---------|-------------------|-----|
| **Edge TTS** (default) | ~0.5s | Good | ✅ Yes | No |
| **Qwen3-TTS** (local) | ~10s | Excellent | No | Recommended |

**To switch TTS backend**, edit `.env`:
```bash
# Fast (default): Microsoft Edge TTS
TTS_BACKEND=edge
TTS_MODEL=default

# High quality (offline): Local Qwen3-TTS
TTS_BACKEND=openai
TTS_MODEL=http://localhost:5002
```

### 🔍 Known Limitations
1. **Context window**: Uses truncation for long conversations (compaction available but optional)
2. **Large files**: Base64 encoding doubles file size in transit

---

## Stress Test Analysis

### Rate Limiting
- **Chat**: 30 messages/minute per user - prevents rapid-fire abuse
- **Files**: 10 files max, 50MB each - prevents memory exhaustion
- **Message length**: 100KB - prevents massive payload attacks

### Concurrent Access
- **Conversation Store**: Thread-safe with async + sync locks
- **Rate Limiter**: Thread-safe with threading.RLock
- **LRU Eviction**: Prevents memory exhaustion from too many tracked users

### Recommendations for High Load
1. Consider Redis-backed rate limiting for horizontal scaling
2. Add LRU cache to conversation store for large deployments
3. Consider streaming file uploads instead of base64 for large files

---

## Mobile Responsiveness

### ✅ Features
- Dynamic viewport height (`100dvh`) - handles keyboard/browser chrome
- Responsive sidebar with slide-out on mobile
- Touch-friendly buttons (44px+ touch targets)
- Proper input scaling for mobile keyboards

### ✅ Tested Breakpoints
- Mobile: < 768px (sidebar hidden by default)
- Desktop: ≥ 768px (sidebar visible)

---

## Configuration Options

### Exposed in Settings UI
- ✅ Theme (Dark, Light, Midnight, Forest)
- ✅ Persona (custom AI personality)
- ✅ Model Parameters (temperature, top_p, top_k, context length, repeat penalty)
- ✅ Voice Settings (mode, TTS voice, speed, auto-play, STT language)
- ✅ Context Compaction settings

### Environment Variables Only
- `JWT_SECRET` - Required, ≥32 chars
- `ADULT_PASSCODE` - Required for Lexi mode
- `BRAVE_SEARCH_API_KEY` - Optional, enables web search
- `HF_TOKEN` - Optional, enables video generation
- `VOICE_ENABLED` - Must be "true" to enable voice

---

## File Structure

```
BrinChat/
├── app/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Configuration management
│   ├── routers/             # API endpoints
│   │   ├── chat.py          # Main chat endpoint
│   │   ├── voice.py         # TTS/STT endpoints
│   │   └── ...
│   ├── services/            # Business logic
│   │   ├── claude_service.py
│   │   ├── lexi_service.py
│   │   ├── tts_backends.py
│   │   ├── stt_backends.py
│   │   └── ...
│   └── models/              # Pydantic schemas
├── static/
│   ├── index.html           # Main UI
│   └── js/
│       ├── app.js           # Main controller
│       ├── chat.js          # Message handling
│       ├── voice.js         # Voice UI
│       └── ...
└── conversations/           # Persistent storage
```

---

## Next Steps (Optional)

### Low Priority
1. Add loading indicator for voice operations (already has recording pulse)
2. Consider WebSocket for chat instead of SSE (would require refactoring)
3. Add message search within conversations

### Future Considerations
1. Add conversation export (JSON/Markdown)
2. Add conversation import

### Completed (2026-01-31)
- ✅ Keyboard shortcuts added (Ctrl+N, Ctrl+K, Ctrl+/, etc.)
- ✅ README.md created with full documentation
- ✅ Orphaned files cleaned up (2206 lines removed)
- ✅ Session routing verified for Joel (user ID 4)
