# BrinChat - Claude Code Project Guide

## Vision Documents

**Before making architectural decisions, read the vision documents:**

📋 **[~/clawd/BrinChat.md](/home/tech/clawd/BrinChat.md)** — Target architecture, session routing, trigger-based routing, Omega + Lexi stack, tool execution pattern.

📋 **[~/clawd/projects/brinchat-uncensored/FINE_TUNING.md](/home/tech/clawd/projects/brinchat-uncensored/FINE_TUNING.md)** — Omega model selection, fine-tuning plan, training data format.

This file (`CLAUDE.md`) documents the **current implementation**. The vision docs describe **where we're going**.

---

## Project Overview
BrinChat is a FastAPI-based AI chat application that integrates with OpenClaw (Claude) and Ollama (Lexi) for LLM interactions. It features authentication, knowledge base management, memory/conversation persistence, user profiles, and hybrid uncensored mode.

## Tech Stack
- **Backend:** Python 3, FastAPI
- **LLM:** Ollama (local)
- **Database:** SQLite (`peanutchat.db`)
- **Frontend:** Static HTML/JS served by FastAPI

## Project Structure
```
app/
├── main.py              # FastAPI app entry point
├── config.py            # Configuration settings
├── routers/             # API route handlers
│   ├── auth.py, chat.py, commands.py, knowledge.py
│   ├── mcp.py, memory.py, models.py, settings.py, user_profile.py
│   ├── voice.py         # TTS/STT API endpoints
│   └── admin.py         # Admin panel API endpoints
├── services/            # Business logic
│   ├── ollama.py, auth_service.py, conversation_store.py
│   ├── knowledge_base.py, memory_service.py, mcp_client.py
│   ├── memory_extractor.py, profile_extractor.py
│   ├── image_backends.py, video_backends.py, tool_executor.py
│   ├── tts_backends.py, stt_backends.py  # Voice model backends
│   ├── tts_service.py, stt_service.py    # Voice orchestration
│   ├── voice_settings_service.py         # Per-user voice settings
│   ├── admin_service.py                  # Admin operations
│   └── feature_service.py                # Feature flag management
├── models/              # Pydantic schemas
├── middleware/          # Request middleware
└── tools/               # Tool definitions for LLM
static/                  # Frontend HTML/JS/CSS
├── admin.html           # Admin portal UI
└── js/admin.js          # Admin panel JavaScript
scripts/
└── create_admin.py      # CLI script to create admin users
```

## Service Management (Passwordless Sudo)
The following commands are available without password for user `tech`:

```bash
# Service control
sudo systemctl start peanutchat
sudo systemctl stop peanutchat
sudo systemctl restart peanutchat
sudo systemctl status peanutchat
sudo systemctl cat peanutchat.service
sudo systemctl enable peanutchat
sudo systemctl disable peanutchat

# View logs
sudo journalctl -u peanutchat -f      # Follow live logs
sudo journalctl -u peanutchat -n 50   # Last 50 lines
```

## Running Locally
```bash
# Activate virtual environment
source venv/bin/activate

# Run directly
python run.py

# Or via start script
./start_peanutchat.sh
```

## Dependencies
- Ollama service must be running (`ollama.service`)
- Python virtual environment at `./venv/`
- Environment variables in `.env`

---

## Key Systems Architecture

### Memory System

The memory system provides persistent, semantic memory storage for user information.

**Files:**
- `app/services/memory_service.py` - Core business logic
- `app/services/memory_store.py` - Database persistence
- `app/services/memory_extractor.py` - Auto-extraction from responses
- `app/services/embedding_service.py` - Vector embeddings
- `app/routers/memory.py` - REST API endpoints

**Flow:**
```
User Message → Extract Search Terms (LLM) → Query Memories (Semantic Search)
                                                    ↓
                                         Inject into System Prompt
                                                    ↓
Model Response → Extract Memories (Auto) → Store with Embeddings
```

**Features:**
- Two-phase retrieval: LLM extracts search terms, then semantic similarity search
- Semantic duplicate detection (cosine similarity > 0.85)
- Source tagging: `explicit` (user asked) vs `inferred` (model proactive)
- Auto-extraction from model responses via `[MEMORY]` tags
- Categories: `personal`, `preference`, `topic`, `instruction`, `general`

**Configuration:**
- `KB_EMBEDDING_MODEL` - Embedding model (default: `nomic-embed-text`)
- Memory similarity threshold: 0.4 for retrieval, 0.85 for duplicates

---

### Profile System

User profiles store persistent preferences and personal information.

**Files:**
- `app/services/user_profile_service.py` - Business logic + mode security
- `app/services/user_profile_store.py` - Database persistence
- `app/services/profile_extractor.py` - Auto-extraction from responses
- `app/routers/user_profile.py` - REST API endpoints

**Features:**
- Auto-save with 2-second debounce
- Profile sections: identity, communication, technical, persona_preferences, etc.
- Auto-extraction via `[PROFILE]` tags for non-tool models

---

### Three-Tier Mode System

Content gating with session-scoped unlocks for safety.

**Tiers:**
1. **Normal Mode** - Default, SFW content only
2. **Uncensored Mode** (Tier 1) - Requires passcode, persists to database
3. **Full Unlock Mode** (Tier 2) - Requires `/full_unlock` command, session-only

**Security Features:**
- Rate limiting: 5 attempts per 5 minutes (PasscodeRateLimiter)
- Session-scoped unlocks (in-memory, cleared on restart)
- X-Session-ID validation for gated endpoints
- Automatic cleanup on mode disable

**Files:**
- `app/services/user_profile_service.py` - Mode logic + rate limiter
- `app/services/user_profile_store.py` - Persistence + session tracking
- `app/routers/user_profile.py` - API endpoints
- `app/routers/commands.py` - Avatar endpoints with session gating

---

### Chat Streaming System

SSE-based streaming with thinking mode support.

**Files:**
- `app/routers/chat.py` - Main chat endpoint
- `app/services/ollama.py` - Ollama API integration
- `static/js/chat.js` - Frontend SSE handling

**Features:**
- Real-time token streaming via Server-Sent Events
- Thinking mode with soft/hard limits (3000/30000 tokens)
- Tool execution with follow-up responses
- Context window compaction for long conversations
- Stream cleanup in finally blocks (prevents resource leaks)

**Context Metadata:**
Each message stores debugging context:
- `thinking_content` - Model's internal reasoning
- `memories_used` - Retrieved memories for this response
- `tools_available` - Tools the model had access to

Frontend displays this in an expandable "Context" section per message.

---

### System Prompt Builder

Assembles the full context sent to the model.

**File:** `app/services/system_prompt_builder.py`

**Assembly Order:**
1. Base identity (persona name, core behavior)
2. User greeting (if name known)
3. Memory context (retrieved memories)
4. Profile context (user preferences)
5. Tool instructions (when to use/not use tools)
6. Response guidelines (format, behavior)

**Sanitization:**
- Truncates content to prevent prompt injection
- Removes system-like markers (`[SYSTEM]`, `[INSTRUCTION]`)
- Blocks instruction override patterns
- Strips control characters

---

### UI-Backend Sync

Frontend state management and error handling.

**Files:**
- `static/js/app.js` - Main application controller
- `static/js/chat.js` - Chat UI management
- `static/js/profile.js` - Profile management
- `static/js/settings.js` - Settings modal

**Features:**
- Toast notifications for user feedback
- Race condition handling (restore state on error)
- Response validation for API calls
- Error boundaries in SSE handler

---

## Configuration Reference

Key environment variables in `.env`:

```bash
# Required
ADULT_PASSCODE=         # Passcode for uncensored mode
JWT_SECRET=             # JWT signing secret

# Optional - General
OLLAMA_BASE_URL=http://localhost:11434
KB_EMBEDDING_MODEL=nomic-embed-text
THINKING_TOKEN_LIMIT_INITIAL=3000
THINKING_TOKEN_LIMIT_FOLLOWUP=2000
THINKING_HARD_LIMIT_INITIAL=30000
THINKING_HARD_LIMIT_FOLLOWUP=20000
CHAT_REQUEST_TIMEOUT=300

# Adult Mode Models (Omega + Lexi Stack)
# Separate models for different tasks - see .env.template for details
LEXI_MODEL=taozhiyuai/llama-3-8b-lexi-uncensored:v1_q8_0
LEXI_BASE_URL=http://localhost:11434
OMEGA_TOOL_MODEL=ministral-3:latest       # Fast, for tool planning (JSON)
OMEGA_TOOL_BASE_URL=http://localhost:11434
OMEGA_VISION_MODEL=huihui_ai/qwen3-vl-abliterated:4b  # For image descriptions
OMEGA_VISION_BASE_URL=http://localhost:11434

# Optional - Voice (TTS/STT)
VOICE_ENABLED=false              # Enable voice features
TTS_BACKEND=edge                 # edge, piper, coqui, kokoro
TTS_MODEL=default                # Model name/path
STT_BACKEND=faster_whisper       # whisper, faster_whisper, vosk
STT_MODEL=small                  # Model size
```

---

### Voice System (TTS/STT)

Model-swappable voice integration with text-to-speech and speech-to-text.

**Files:**
- `app/services/tts_backends.py` - TTS model implementations (Edge, Piper, Coqui, Kokoro)
- `app/services/stt_backends.py` - STT model implementations (Whisper, Faster-Whisper, Vosk)
- `app/services/tts_service.py` - TTS orchestration service
- `app/services/stt_service.py` - STT orchestration service
- `app/services/voice_settings_service.py` - Per-user voice preferences
- `app/routers/voice.py` - REST API endpoints

**Architecture:**
```
Voice Router (/api/voice/*)
         │
    ┌────┴────┐
    ▼         ▼
TTS Service  STT Service
    │         │
    ▼         ▼
TTSBackend   STTBackend (abstract)
    │         │
┌───┼───┐  ┌──┼──┐
Edge Piper  Whisper Faster-Whisper
Coqui Kokoro  Vosk
```

**Voice Modes:**
| Mode | STT | TTS | Description |
|------|-----|-----|-------------|
| `disabled` | No | No | No voice features (default) |
| `transcribe_only` | Yes | No | Voice input, text responses |
| `tts_only` | No | Yes | Text input, voice responses |
| `conversation` | Yes | Yes | Full voice-to-voice chat |

**Configuration:**
```bash
VOICE_ENABLED=false          # Master toggle
TTS_BACKEND=edge             # edge, piper, coqui, kokoro
TTS_MODEL=default            # Model-specific
STT_BACKEND=faster_whisper   # whisper, faster_whisper, vosk
STT_MODEL=small              # tiny, base, small, medium, large
```

**Adding New Backends:**
1. Subclass `TTSBackend` or `STTBackend` in the respective backends file
2. Implement `initialize()`, `generate()`/`transcribe()`, and `cleanup()`
3. Register in `TTS_BACKENDS` or `STT_BACKENDS` dict
4. Use via environment variable: `TTS_BACKEND=my_new_backend`

#### Conversation Mode Architecture

Full-duplex voice conversation with parallel processing and interrupt capability.

**Key Improvements (2026-02-09):**
- **Parallel Processing**: User can speak again while previous response is being processed (~10s between exchanges → ~2s)
- **Page Visibility Resilience**: Conversation mode streams ignore tab switches (`isConversationMode` flag)
- **Interrupt Capability**: User can interrupt TTS by speaking (`canInterrupt` flag + `stopCurrentAudio()`)
- **TTS Completion Tracking**: Promise-based tracking with `StreamingAudioPlayer.onEnded` callback
- **Message Queue Fix**: `isStreaming` set to `false` on `text_complete` event
- **AudioContext Reuse**: Reuses `AudioContext` instead of creating new one, tracks and disconnects `audioSourceNode`

**State Flags:**
```javascript
// VoiceManager state
this.isTranscribing = false;        // STT in progress
this.isWaitingForResponse = false;  // Waiting for backend response
this.canInterrupt = false;          // True after response starts arriving
this.audioSourceNode = null;        // Track for cleanup/disconnect

// ChatManager state
this.isConversationMode = false;    // Set by voice.js
this.ttsCompletionPromise = null;   // Promise that resolves when TTS completes
this.ttsCompletionResolve = null;   // Resolver for TTS completion
this.streamingAudioPlayer = null;   // StreamingAudioPlayer instance
```

**Flow:**
```
1. User speaks → STT transcribes
2. handleConversationInput() called with transcription:
   - Shows transcript in overlay
   - Starts listening immediately (parallel processing)
   - Sets isWaitingForResponse = true, canInterrupt = false
   - Sends message via sendMessageAndGetResponse()
   - Sets canInterrupt = true after send starts
3. Response streams back:
   - text_complete event → isStreaming = false (allow new messages)
   - TTS chunks play via StreamingAudioPlayer
   - onResponseComplete callback waits for ttsCompletionPromise
4. TTS completes → StreamingAudioPlayer.onEnded → resolves ttsCompletionPromise
5. onResponseComplete callback fires → loops back to listening
```

**Interrupt Handling:**
- When user starts recording during playback (`canInterrupt = true`):
  - `startRecording()` detects interrupt condition
  - Calls `stopCurrentAudio()` to stop StreamingAudioPlayer
  - Recording continues normally
- Prevents accidental interrupts during STT processing (`canInterrupt = false`)

**Page Visibility:**
- `chat.js` checks `isConversationMode` flag before aborting streams on tab switch
- Conversation mode streams continue in background (no recovery needed)
- Non-conversation streams abort after 2s hidden (triggers recovery)

**AudioContext Management:**
- Single `AudioContext` reused across recordings for VAD
- `audioSourceNode` tracked and disconnected between recordings
- Prevents memory leaks from accumulated source nodes
- Lazy initialization: created on first use, persists until cleanup

**Timing Improvements:**
- Previous: ~10s between exchanges (sequential processing)
- Current: ~2s between exchanges (parallel processing)
- User can interrupt TTS immediately by speaking
- No waiting for response to finish before listening again

**Files:**
- `/home/brin/projects/BrinChat/static/js/voice.js` - VoiceManager class (lines 889-927: handleConversationInput, 1092-1107: stopCurrentAudio)
- `/home/brin/projects/BrinChat/static/js/chat.js` - ChatManager integration (lines 1770-1890: TTS completion tracking)

---

### Admin System

Administrative portal for user and feature management.

**Files:**
- `app/routers/admin.py` - Admin API endpoints
- `app/services/admin_service.py` - User CRUD, stats, audit log
- `app/services/feature_service.py` - Feature flag management
- `static/admin.html` - Admin portal UI
- `static/js/admin.js` - Admin panel JavaScript
- `scripts/create_admin.py` - CLI for creating admin users

**Features:**
- User management (create, edit, delete, password reset)
- Mode restrictions (lock users to specific content modes)
- Feature flags (global defaults + per-user overrides)
- Audit logging (all admin actions logged)
- Dashboard statistics

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/users` | List users (paginated) |
| POST | `/api/admin/users` | Create user |
| PATCH | `/api/admin/users/{id}` | Update user |
| DELETE | `/api/admin/users/{id}` | Delete user |
| POST | `/api/admin/users/{id}/reset-password` | Reset password |
| GET | `/api/admin/features` | List feature flags |
| PATCH | `/api/admin/features/{key}` | Update global default |
| PUT | `/api/admin/users/{id}/features/{key}` | Set user override |
| GET | `/api/admin/audit-log` | View audit log |
| GET | `/api/admin/dashboard` | System statistics |

**Creating Admin Users:**
```bash
# Interactive
python scripts/create_admin.py

# Command line
python scripts/create_admin.py <username> <password>

# Promote existing user
python scripts/create_admin.py --promote
```

**Database Tables:**
- `feature_flags` - Global feature defaults
- `user_feature_overrides` - Per-user feature settings
- `admin_audit_log` - Admin action history

---

## MCP Tool Documentation
Reference implementations for MCP tools are in:
- `mcp_tool_documentation/image_gen/` - Image generation MCP server
- `mcp_tool_documentation/video_gen/` - Video generation MCP server
