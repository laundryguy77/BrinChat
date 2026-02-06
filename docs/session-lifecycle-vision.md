# Session Lifecycle Vision

> **Version:** 1.0.0  
> **Status:** TARGET STATE (not yet implemented)  
> **Last Updated:** 2026-01-31  
> **See Also:** [VISION.md](VISION.md) for full architecture, [session-lifecycle.md](session-lifecycle.md) for current implementation

This document describes the TARGET session lifecycle for BrinChat with Option B Full Hybrid architecture.

---

## Overview

The key change from current state: **BrinChat must handle its own tools locally** instead of waiting for OpenClaw to return `tool_calls` (which it never does).

For Lexi (adult) mode, a new **hybrid handoff** component detects tool needs from response text and executes them.

---

## Request Flow Summary

```
User Message
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 1: INTAKE & ROUTING                                       │
│                                                                  │
│  • User lookup (user_id, username)                              │
│  • Primary user detection (Joel → agent:main:main)              │
│  • Mode detection (adult_status + session_unlock)               │
│                                                                  │
│  Route decision:                                                 │
│    use_lexi = adult_mode AND session_unlocked                   │
└─────────────────────────────────────────────────────────────────┘
     │
     ├──────────────────────────────────────────┐
     │                                          │
     ▼                                          ▼
┌─────────────────────────┐          ┌─────────────────────────┐
│ PHASE 2A: CLAUDE PATH   │          │ PHASE 2B: LEXI PATH     │
│                         │          │                         │
│ POST OpenClaw :18789    │          │ POST Ollama :11434      │
│                         │          │                         │
│ • Context assembly      │          │ • Lexi persona          │
│ • Claude inference      │          │ • Uncensored model      │
│ • Internal tool exec    │          │ • Stream response       │
│ • Stream final response │          │                         │
│                         │          │ Then → Phase 3          │
│ (No tool_calls returned)│          │                         │
└────────────┬────────────┘          └────────────┬────────────┘
             │                                     │
             │                                     ▼
             │                       ┌─────────────────────────┐
             │                       │ PHASE 3: HYBRID HANDOFF │
             │                       │ (lexi_file_gen.py)      │
             │                       │                         │
             │                       │ Stage 1: Pattern match  │
             │                       │   └─► Regex (~1ms)      │
             │                       │                         │
             │                       │ Stage 2: LLM classify   │
             │                       │   └─► qwen2.5:3b        │
             │                       │   └─► Extract params    │
             │                       │                         │
             │                       │ Stage 3: Tool execution │
             │                       │   ├─► Joel → Brin/DALL-E│
             │                       │   └─► Other → fal.ai    │
             │                       │                         │
             │                       │ Stage 4: Result inject  │
             │                       │   └─► Modify/append     │
             │                       └────────────┬────────────┘
             │                                     │
             └──────────────────┬──────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 4: RESPONSE DELIVERY                                      │
│                                                                  │
│  • Stream to frontend (SSE)                                     │
│  • Voice synthesis (if enabled)                                 │
│      ├─► Normal: Edge TTS (~0.5s)                               │
│      └─► Adult: Qwen3-TTS + Lexi voice (~10s)                   │
│  • Persist conversation                                         │
│                                                                  │
│  Post-response (non-Joel only):                                 │
│    └─► Task extraction → Nextcloud Deck                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Phase Details

### Phase 1: Intake & Routing

**Location:** `app/routers/chat.py`

```python
# Current code (unchanged)
adult_status = await profile_service.get_adult_mode_status(user.id)
session_unlock_status = await profile_service.get_session_unlock_status(user.id, session_id)
use_lexi = adult_status.get("enabled") and session_unlock_status.get("enabled")
```

**Routing logic:**

| User | Mode | Route To |
|------|------|----------|
| Joel | Normal | OpenClaw `agent:main:main` |
| Joel | Adult | Lexi → Hybrid → Brin |
| Other | Normal | OpenClaw `brinchat:{username}` |
| Other | Adult | Lexi → Hybrid → BrinChat tools |

---

### Phase 2A: Claude Path (Normal Mode)

**No changes needed.** OpenClaw handles everything internally.

- Joel gets full tool access (exec, browser, files, DALL-E)
- Non-Joel gets isolated session with limited tools
- BrinChat receives final response, no `tool_calls`

---

### Phase 2B: Lexi Path (Adult Mode)

**Location:** `app/services/lexi_service.py`

Current flow works — Lexi generates uncensored response via Ollama.

**Change:** After stream completes, pass response to Phase 3.

---

### Phase 3: Hybrid Handoff (NEW)

**Location:** `app/services/lexi_file_gen.py` (TO BE CREATED)

#### Stage 1: Pattern Matching

Fast regex scan for tool-related phrases:

```python
TOOL_PATTERNS = {
    "image": [
        r"(?:generate|create|make|draw)\s+(?:an?\s+)?(?:image|picture|photo)",
        r"(?:here'?s|let me show)\s+(?:the\s+)?(?:image|picture)",
    ],
    "video": [
        r"(?:generate|create|make)\s+(?:a\s+)?video",
        r"(?:animate|turn into video)",
    ],
    "search": [
        r"(?:search|look up)\s+(?:the\s+)?(?:web|internet)",
    ],
}
```

- **Match:** Proceed to Stage 2
- **No match:** Return response as-is

#### Stage 2: LLM Classification

Use small model to confirm intent and extract parameters:

```python
async def classify_tool_need(response: str, user_message: str) -> ToolRequest | None:
    prompt = f"""Does this AI response promise to generate a file or search?
    
User: {user_message[:200]}
AI: {response[:500]}

Extract: {{"tool": "image|video|search|null", "prompt": "...", "confidence": 0.0-1.0}}"""
    
    result = await ollama_chat(model="qwen2.5-coder:3b", ...)
    # Return ToolRequest if confidence >= 0.7
```

#### Stage 3: Tool Execution

```python
async def execute_hybrid_tool(request: ToolRequest, user_id: int, is_primary: bool):
    if is_primary:  # Joel
        return await call_brin_for_tool(request)  # Uses OpenClaw
    else:
        if request.tool == "image":
            return await fal_generate_image(request.prompt)
        elif request.tool == "video":
            return await fal_generate_video(request.prompt)
        elif request.tool == "search":
            return await brave_search(request.prompt)
```

#### Stage 4: Result Injection

- **Success:** Upload to Nextcloud, inject URL into response
- **Failure:** Append error message, log to Nextcloud

---

### Phase 4: Response Delivery

**Voice provider selection (NEW):**

```python
if use_lexi:
    tts_provider = "qwen3"
    voice_profile = "lexi"
else:
    tts_provider = "edge"
    voice = user_preference or "en-US-AriaNeural"
```

---

## Implementation Phases

### Phase 1: Core Hybrid Handoff
- [ ] Create `lexi_file_gen.py`
- [ ] Implement pattern matching (Stage 1)
- [ ] Implement LLM classification (Stage 2)
- [ ] Basic tool execution (Stage 3) — image only
- [ ] Result injection (Stage 4)
- [ ] Wire into `chat.py` after Lexi stream

### Phase 2: Full Tool Support
- [ ] Add video generation
- [ ] Add web search
- [ ] Nextcloud upload helper
- [ ] Error handling and logging

### Phase 3: Voice Integration
- [ ] Voice provider switching
- [ ] Lexi voice profile (Qwen3-TTS)
- [ ] Test end-to-end latency

### Phase 4: Polish
- [ ] Non-Joel tool detection (for normal mode)
- [ ] Task extraction refinement
- [ ] Performance optimization

---

## File Changes Required

| File | Change |
|------|--------|
| `app/services/lexi_file_gen.py` | **CREATE** — Hybrid handoff logic |
| `app/routers/chat.py` | **MODIFY** — Call lexi_file_gen after Lexi stream |
| `app/services/claude_service.py` | No change |
| `app/services/lexi_service.py` | No change |
| `app/routers/voice.py` | **MODIFY** — Provider switching |

---

## Testing Checklist

- [ ] Joel + Adult Mode: Request image → Brin generates via DALL-E
- [ ] Non-Joel + Adult Mode: Request image → fal.ai generates
- [ ] Pattern matching doesn't false-positive on normal conversation
- [ ] Low-confidence classifications return response as-is
- [ ] Nextcloud upload works
- [ ] Voice uses correct provider per mode

---

*This document will be updated as implementation progresses.*
