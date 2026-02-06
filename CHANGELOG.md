# Changelog

All notable changes to BrinChat are documented in this file.

---

## [2026-01-31] - TTS Performance Optimization

### Performance Improvements
- **Edge TTS backend enabled**: Default TTS backend changed from local Qwen3-TTS to Microsoft Edge TTS
- **20x faster TTS**: Response time reduced from ~10 seconds to ~0.5 seconds
- **Edge TTS installed**: Added `edge-tts` package to venv for online TTS synthesis

### Configuration
- **Configurable TTS backend**: Users can switch between Edge TTS (fast, online) and Qwen3-TTS (high quality, offline) via `.env`:
  ```bash
  # Fast (default)
  TTS_BACKEND=edge
  TTS_MODEL=default

  # High quality (offline)
  TTS_BACKEND=openai
  TTS_MODEL=http://localhost:5002
  ```

### Code Quality
- **Updated PROJECT_STATUS.md**: Added TTS performance comparison table
- **Documented TTS options**: Clear guidance on switching backends

---

## [2026-01-31] - Accessibility & UX Improvements

### Accessibility (WCAG 2.1 Compliance)
- **Skip to content link**: Added hidden skip link for keyboard users to jump to chat input
- **Screen reader live region**: Added `sr-announcements` div for dynamic content announcements
- **ARIA labels added**: All interactive buttons now have proper `aria-label` attributes
- **ARIA roles**: Added `role="dialog"` to modals, `role="navigation"` to sidebar, `role="main"` to content
- **Form label associations**: All form inputs now have `for` attributes connecting labels
- **Conversation list roles**: Added `role="list"` and `role="listitem"` for better screen reader navigation
- **Voice input state**: Voice button now uses `aria-pressed` to indicate recording state
- **Tab panel roles**: Auth modal tabs now have proper `role="tablist"`, `role="tab"`, `role="tabpanel"`
- **Error announcements**: Auth errors now have `role="alert"` for immediate screen reader feedback

### User Experience
- **Loading state for conversations**: Added spinner while conversation list loads
- **Empty state for conversations**: Friendly message when no conversations exist
- **Screen reader announcements**: Key actions (message sent, response received, recording) now announced
- **Autocomplete attributes**: Login/register forms now have proper autocomplete hints
- **Password hints**: Screen reader accessible hints for password requirements

### Keyboard Shortcuts
- **Ctrl/Cmd + N**: New conversation
- **Ctrl/Cmd + K**: Focus conversation search
- **Ctrl/Cmd + /**: Toggle sidebar visibility
- **/**: Focus message input (when not already typing)

### Code Quality
- **HTML escaping**: Conversation titles now escaped to prevent XSS
- **Consistent helper methods**: `announceToScreenReader()` and `escapeHtml()` added to App and ChatManager

---

## [2026-01-31] - Security Hardening & Input Validation

### Security Improvements
- **Added chat rate limiting**: 30 messages per minute per user, 5-minute cooldown if exceeded
- **Added request payload limits**:
  - Message text: 100KB maximum
  - File content: 50MB maximum per file (base64)
  - Maximum 10 files per request
  - Maximum 5 images per request
  - File name length: 255 characters maximum
- **Empty message validation**: Properly reject messages with only whitespace

### Bug Fixes
- **Fixed TTS health check**: Now tries `/v1/models` then `/health` endpoints instead of failing on 404 root
- **Fixed import in rate_limiter**: Added `Optional` type hint for new chat limiter

### Technical Changes
- Added `get_chat_limiter()` to rate_limiter.py with 30 msg/min limit
- Updated ChatRequest schema with Pydantic Field validators
- Updated FileAttachment with content length validation
- TTS backend initialize() now more robust with fallback health checks

---

## [2026-01-31] - MCP Removal & Code Cleanup

### Removed (Dead Code Deletion)
- **Deleted `app/routers/mcp.py`** - Deprecated MCP router (was not included in main.py)
- **Deleted `app/services/mcp_client.py`** - Deprecated MCP client (~600 lines of unused code)
- **Removed `mcp_tools` parameter** from `get_tools_for_model()` function
- **Removed `check_mcp_tools_enabled()`** method from FeatureService

### Technical Changes
- Cleaned up MCP references in `chat.py`, `models.py`, `tools/definitions.py`, `feature_service.py`
- Database migration for `mcp_servers` table preserved for backwards compatibility
- `mcp_tools` feature flag preserved in database for existing deployments

### Security Review Completed
- **Rate limiting**: Verified in place for `/login` (5 attempts/5min), `/register` (3/hour), `/refresh` (10/min)
- **Token blacklist**: Working correctly with LRU eviction and monotonic time
- **Database errors**: Properly wrapped with reference IDs, no SQL details leak to users
- **Conversation persistence**: Atomic file writes with temp+rename pattern
- **Auth middleware**: JWT decoding with blacklist check, is_active verification

---

## [2026-01-31] - Code Quality & Security Improvements

### Improvements
- **Cleaned up debug logging** - Removed excessive print() statements from claude_service.py; all debug output now uses proper logging framework
- **Added input validation for TTS** - Speed parameter now validated to 0.5-2.0 range using Pydantic Field
- **Added language code validation for STT** - Prevents malformed language codes in transcription requests
- **Added documentation for conversation cache** - Noted potential memory optimization for large deployments (LRU cache suggestion)

### Technical Changes
- ClaudeService now logs initialization info at INFO level instead of print statements
- Error responses from OpenClaw API now logged at ERROR level with truncated details
- Voice settings update endpoint now validates speed parameter range

---

## [2026-01-31] - BrinChat: Claude via OpenClaw Refactor

### Major Changes

**Renamed from PeanutChat to BrinChat**
- All references to PeanutChat updated throughout the codebase
- New branding reflects Claude-powered experience

**Replaced Ollama with OpenClaw (Claude)**
- New `claude_service.py` provides OpenAI-compatible interface to Claude via OpenClaw
- Chat completions now route through `http://localhost:8788/v1/chat/completions`
- Streaming responses fully supported
- Tool calling adapted to OpenAI function format

**Removed Features**
- Model selection dropdown (Claude is the only model)
- MCP (Model Context Protocol) server management
- Avatar generation (was MCP-dependent)
- Uncensored mode filtering (not applicable for Claude)
- Ollama-specific code paths
- `web_search` tool (use OpenClaw's built-in web_search)
- `browse_website` tool (use OpenClaw's built-in web_fetch)
- Brave Search API integration (no longer needed)

**Kept Features**
- Knowledge base (document ingestion + semantic search via Ollama embeddings)
- Memory system (user profiles, preferences)
- Image/video generation tools
- Web search and browse website tools
- Theme system
- Conversation history
- Voice features (TTS/STT)

**Configuration Changes**
- New env vars: `OPENCLAW_API_URL`, `OPENCLAW_API_KEY`, `CLAUDE_MODEL`
- `OLLAMA_BASE_URL` retained for embedding service only
- Default model changed to `claude-sonnet-4-20250514`
- Voice backends changed to `openai` (uses local OpenAI-compatible APIs)

**Voice Integration (KEY FEATURE) - ChatGPT-Inspired UX**

Two voice modes, inspired by ChatGPT's approach:

**Mode 1: Transcription Mode (Default)**
- Click mic button → records audio
- Click again OR wait for silence → stops recording
- Text appears in input box for editing
- User can modify text before sending
- Speaker button on messages for manual TTS playback

**Mode 2: Conversation Mode (Hold mic)**
- Hold mic button for 500ms → enters conversation mode
- Full-screen overlay with visual states (listening/processing/speaking)
- Voice in → auto-transcribe → auto-send → auto-play response
- Continuous conversation until user exits
- ESC or "End Conversation" button to exit

Visual feedback:
- Recording: Red pulsing mic with ring animation
- Processing: Yellow spinning sync icon
- Speaking: Green volume icon (in conversation mode)

Voice services (local OpenAI-compatible APIs):
- **STT:** POST http://localhost:5001/v1/audio/transcriptions (Whisper large-v3)
- **TTS:** POST http://localhost:5002/v1/audio/speech (Qwen TTS)

Configuration in `.env`:
```
VOICE_ENABLED=true
TTS_BACKEND=openai
TTS_MODEL=http://localhost:5002
STT_BACKEND=openai
STT_MODEL=http://localhost:5001
```

**UI Updates**
- Model selector replaced with "Claude" badge
- All capability indicators always shown (Claude supports tools, vision, thinking)
- MCP section removed from settings modal
- Welcome message updated

---

## [2026-01-25] - Voice Integration & Admin Portal

### Voice System (TTS/STT)

**Model-Swappable Architecture**
- Added abstract `TTSBackend` and `STTBackend` base classes
- TTS backends: Edge-TTS, Piper, Coqui, Kokoro
- STT backends: Whisper, Faster-Whisper, Vosk
- Backend selection via environment variables (`TTS_BACKEND`, `STT_BACKEND`)
- Lazy model loading to conserve GPU memory

**Voice Services**
- `tts_service.py` - TTS orchestration with streaming support
- `stt_service.py` - STT orchestration with language detection
- `voice_settings_service.py` - Per-user voice preferences
- Voice modes: disabled, transcribe_only, tts_only, conversation

**Voice API Endpoints**
- `POST /api/voice/tts/stream` - Stream TTS audio (SSE)
- `POST /api/voice/transcribe` - Transcribe audio to text
- `GET /api/voice/tts/voices` - List available voices
- `GET/PUT /api/voice/settings` - User voice settings
- `GET /api/voice/capabilities` - Backend capabilities

### Admin Portal

**User Management**
- Create, edit, delete users via web UI
- Password reset functionality
- Activate/deactivate user accounts
- Mode restrictions (normal_only, no_full_unlock)
- Per-user voice enable/disable

**Feature Flag System**
- Global feature defaults in `feature_flags` table
- Per-user overrides in `user_feature_overrides` table
- Features: web_search, memory, tts, stt, image_gen, video_gen, knowledge_base

**Dashboard & Audit**
- System statistics (users, conversations, messages)
- Admin audit log for all administrative actions
- Filterable by admin, action type, date range

**Admin API Endpoints**
- `GET/POST /api/admin/users` - List/create users
- `PATCH/DELETE /api/admin/users/{id}` - Update/delete user
- `POST /api/admin/users/{id}/reset-password` - Reset password
- `GET/PATCH /api/admin/features` - Feature flag management
- `PUT /api/admin/users/{id}/features/{key}` - User feature override
- `GET /api/admin/audit-log` - View audit log
- `GET /api/admin/dashboard` - System statistics

**Admin Setup**
- `scripts/create_admin.py` - CLI script to create admin users
- Interactive and command-line modes
- Promote existing users to admin

### Database Migrations

- Migration 011: Voice settings (`voice_enabled` column on users)
- Migration 012: Admin features (`is_admin`, `is_active`, `mode_restriction` columns; feature tables)

### New Files

**Voice System**
- `app/services/tts_backends.py` - TTS backend implementations
- `app/services/stt_backends.py` - STT backend implementations
- `app/services/tts_service.py` - TTS orchestration
- `app/services/stt_service.py` - STT orchestration
- `app/services/voice_settings_service.py` - Voice settings
- `app/routers/voice.py` - Voice API router

**Admin System**
- `app/services/admin_service.py` - Admin business logic
- `app/services/feature_service.py` - Feature flag management
- `app/routers/admin.py` - Admin API router
- `static/admin.html` - Admin portal UI
- `static/js/admin.js` - Admin panel JavaScript
- `scripts/create_admin.py` - Admin user creation script

### Configuration Changes

New environment variables:
```bash
# Voice Features
VOICE_ENABLED=false
TTS_BACKEND=edge
TTS_MODEL=default
STT_BACKEND=faster_whisper
STT_MODEL=small
```

---

## [2026-01-24] - UI/Backend Sync & System Improvements

### UI-Backend Sync Fixes

**Error Handling & User Feedback**
- Added `showToast()` method to `chat.js` for visual error/success notifications
- Fixed silent error handling in `regenerateResponse()` and `saveEdit()` - now shows user feedback
- Added response validation for fork/edit API calls
- Added error boundaries in SSE handler for malformed JSON

**Race Condition Fixes**
- Fixed race condition in `sendMessage()` - stores original message/images before clearing
- Restores input state on error so users don't lose their message

**Stream Cleanup**
- Fixed stream cleanup in regenerate endpoint - stream now properly tracked and closed
- Added `regen_stream` variable with try/finally cleanup pattern
- Matches cleanup pattern used in main chat endpoint

### Profile Persistence Fixes

- Added `setupFormEventListeners()` for change detection on all profile inputs
- Added visible Save button that appears when changes detected
- Implemented auto-save with 2-second debounce
- Fixed in-memory cache updates for all profile fields (was only updating some)
- Added `forceReload` parameter to `init()` for fresh data on modal open
- Settings modal now forces profile reload when opened

### Mode System Security

**Rate Limiting**
- Added `PasscodeRateLimiter` class (5 attempts / 5 minute lockout)
- Prevents brute-force attacks on adult mode passcode

**Session Security**
- Fixed `disable_adult_mode()` to clear all session unlocks
- Added X-Session-ID header validation to `enable_section` endpoint
- Added session verification to avatar endpoints (generate, select, regenerate)
- Avatar operations now require both Tier 1 and Tier 2 unlock

### Thinking Mode Improvements

**Soft/Hard Limit System**
- Changed from immediate break at soft limit to warning + continue
- Soft limits (warning only): 3000 tokens initial, 2000 tokens followup
- Hard limits (break stream): 30000 tokens initial, 20000 tokens followup
- Model can now complete extended thinking for complex problems
- Added configurable environment variables for all limits

### System Prompt Improvements

**Tool Instructions**
- Added "When NOT to Use Tools" section to prevent unnecessary tool calls
- Added "Never mix tool syntax into responses" rule
- Clearer guidance on when to respond directly vs use tools

**Response Guidelines**
- Streamlined PROFILE_INSTRUCTIONS from 15 to 6 lines
- Added Format section (default 1-3 paragraphs, expand if needed)
- Added Behavior section (stay in character, proactive)
- Updated base identity to emphasize conciseness

### Memory System Improvements

**Source Tagging**
- Added `source` parameter to `add_memory` tool definition
- Model can now specify `explicit` when user directly asked to remember
- Tool executor uses provided source, defaults to `inferred`
- Enables filtering user-requested vs proactively stored memories

**Semantic Duplicate Detection**
- Replaced exact string match with cosine similarity check
- Threshold 0.85 catches semantically similar memories
- Returns existing memory content in duplicate error for transparency
- Prevents memory bloat from similar entries

**Automatic Memory Extraction**
- New `memory_extractor.py` service (similar to profile_extractor.py)
- Parses `[MEMORY]` structured tags from model responses
- Parses `[REMEMBER]` simple tags
- Implicit extraction for non-tool models (name/preference acknowledgments)
- Integrated into chat flow for both regular and followup responses

### Context Debugging

**Backend Changes**
- Message SSE events now include full metadata object
- Metadata contains: `thinking_content`, `memories_used`, `tools_available`
- Applied to all endpoints: regular chat, followup, and regenerate

**Frontend Changes**
- Context section created immediately when message completes (during streaming)
- Replaces streaming thinking container with unified context section
- Context section expanded by default for easier debugging
- Shows three panels:
  - Model Reasoning (pink) - thinking/reasoning tokens
  - Memories Used (purple) - retrieved memories with categories
  - Tools Available (green) - tools the model had access to
- Persists with each message for historical debugging

---

## File Changes Summary

### Modified Files
- `app/config.py` - Added thinking hard limits
- `app/routers/chat.py` - Stream cleanup, metadata events, memory extraction
- `app/routers/commands.py` - Session validation for avatars
- `app/routers/user_profile.py` - Session header validation
- `app/services/memory_service.py` - Semantic duplicate detection
- `app/services/system_prompt_builder.py` - Improved prompts
- `app/services/tool_executor.py` - Source parameter support
- `app/services/user_profile_service.py` - Rate limiter, session cleanup
- `app/services/user_profile_store.py` - Architecture documentation
- `app/tools/definitions.py` - Source parameter in add_memory tool
- `static/js/app.js` - Race condition fix
- `static/js/chat.js` - Toast notifications, context section, SSE handling
- `static/js/profile.js` - Auto-save, change detection, Save button
- `static/js/settings.js` - Force reload profile on modal open

### New Files
- `app/services/memory_extractor.py` - Automatic memory extraction from responses

---

## Configuration Changes

New environment variables (optional):
```bash
THINKING_HARD_LIMIT_INITIAL=30000
THINKING_HARD_LIMIT_FOLLOWUP=20000
```

---

## Migration Notes

No database migrations required. All changes are backwards compatible.
