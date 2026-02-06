# BrinChat Adult Mode Implementation Plan

## Overview

Implement the Omega + Lexi stack for adult mode with trigger-based routing, tool execution, and vision capabilities.

**Vision Documents:**
- `/home/tech/clawd/BrinChat.md` — Main architecture
- `/home/tech/clawd/projects/brinchat-uncensored/FINE_TUNING.md` — Omega training plan
- `/home/tech/clawd/projects/brinchat-uncensored/ARCHITECTURE.md` — Flow diagrams
- `/home/tech/clawd/projects/brinchat-uncensored/VRAM_BUDGET.md` — V100 constraints

**Implementation Location:** `/home/tech/projects/BrinChat/`

---

## Architecture Summary

```
Adult Mode Message Flow:

Message → Trigger Scan (regex, ~1ms)
              │
      ┌───────┴───────┐
      │               │
   No Match         Match
      │               │
      ▼               ▼
    Lexi          OpenClaw
  (direct)        Classifies
   ~2-4s          "Tool needed?"
                      │
                 ┌────┴────┐
                 │         │
            False Pos   Real Tool
                 │         │
                 ▼         ▼
               Lexi      Omega (plan)
                           │
                           ▼
                      BrinChat (execute fal.ai)
                           │
                           ▼
                      Omega (vision)
                           │
                           ▼
                      Lexi (respond)
```

---

## Phase 1: Trigger Scanner

**Goal:** Fast regex-based detection to skip Omega for pure conversation.

**New File:** `app/services/trigger_scanner.py`

```python
# Key patterns from vision doc
TOOL_TRIGGERS = [
    r"(?:generate|create|make|draw|show)\b.*(?:image|picture|photo|pic)",
    r"\b(?:image|picture|photo|pic)\b.*(?:of|with|showing)",
    r"(?:generate|create|make)\b.*video",
    r"\b(?:animate|animation)\b",
    r"(?:search|look up|find|google)\b",
]
```

**Implementation:**
1. Create `TriggerScanner` class with `has_tool_triggers(message: str) -> bool`
2. Return True on ANY match (prefer false positives)
3. Add `get_matched_triggers(message: str) -> List[str]` for debugging

**Files to modify:**
- `app/services/trigger_scanner.py` (NEW)
- `app/config.py` — Add `TOOL_TRIGGER_ENABLED` config

---

## Phase 2: Omega Service

**Goal:** Ollama wrapper for tool planning and vision.

**New File:** `app/services/omega_service.py`

**Tool Definition Format (in prompt):**
```
generate_image:
  endpoint: https://fal.ai/v1/...
  auth: ${FAL_KEY}
  method: POST
  body: {"prompt": <prompt>}
```

**Omega sees variable names, BrinChat substitutes actual values.**

**Implementation:**
1. `OmegaService` class wrapping Ollama API
2. `plan_tool_call(message: str, tool_definitions: str) -> OmegaToolCall`
3. `describe_image(image_url: str) -> str` — Vision capability
4. Tool definitions loaded from `app/tools/omega_definitions.py`

**Schema:**
```python
class OmegaToolCall(BaseModel):
    tool: Optional[str]  # "image" | "video" | "websearch" | None
    prompt: str
    style: Optional[str]
    safe_search: Optional[bool]
    reason: Optional[str]
```

**Files to modify:**
- `app/services/omega_service.py` (NEW)
- `app/tools/omega_definitions.py` (NEW) — Tool schemas with ${ENV_VAR} placeholders
- `app/models/schemas.py` — Add OmegaToolCall
- `app/config.py` — Add OMEGA_MODEL, OMEGA_BASE_URL

---

## Phase 3: Adult Orchestrator

**Goal:** Full pipeline coordination for adult mode.

**New File:** `app/services/adult_orchestrator.py`

**Flow:**
1. Receive message + user context
2. Call trigger scanner
3. If no triggers → call Lexi directly
4. If triggers → call OpenClaw for classification
5. If no tool needed → call Lexi
6. If tool needed:
   - Call Omega for tool planning
   - Parse JSON, substitute env vars
   - Execute tool (fal.ai, Brave)
   - Call Omega for vision description
   - Inject context to Lexi
   - Call Lexi for response

**Implementation:**
```python
class AdultOrchestrator:
    async def process_message(
        self,
        message: str,
        user_id: int,
        conversation_id: str,
        images: List[str] = None
    ) -> AsyncGenerator[dict, None]:
        # Trigger scan
        if not self.trigger_scanner.has_tool_triggers(message):
            async for chunk in self.lexi_service.chat_stream(...):
                yield chunk
            return

        # OpenClaw classification
        needs_tool = await self.classify_tool_need(message)
        if not needs_tool:
            async for chunk in self.lexi_service.chat_stream(...):
                yield chunk
            return

        # Omega pipeline
        tool_call = await self.omega_service.plan_tool_call(message)
        result = await self.execute_tool(tool_call)
        description = await self.omega_service.describe_image(result.url)

        # Inject to Lexi
        context = f"You posted an image: {result.url}\nDescription: {description}"
        async for chunk in self.lexi_service.chat_stream(..., context=context):
            yield chunk
```

**Files to modify:**
- `app/services/adult_orchestrator.py` (NEW)
- `app/routers/chat.py` — Route adult mode through orchestrator

---

## Phase 4: Tool Execution

**Goal:** BrinChat executes tools with env var substitution.

**Modify:** `app/services/tool_executor.py`

**Implementation:**
1. Add `execute_omega_tool(tool_call: OmegaToolCall) -> ToolResult`
2. Substitute `${FAL_KEY}` → actual value from environment
3. Route to existing image_backends.py / video_backends.py
4. Return URL + metadata

**New patterns:**
```python
def substitute_env_vars(template: str) -> str:
    """Replace ${VAR_NAME} with os.environ[VAR_NAME]"""
    pattern = r'\$\{(\w+)\}'
    def replacer(match):
        return os.environ.get(match.group(1), '')
    return re.sub(pattern, replacer, template)
```

**Files to modify:**
- `app/services/tool_executor.py` — Add execute_omega_tool method
- `app/config.py` — Add FAL_KEY, ensure BRAVE_SEARCH_API_KEY exists

---

## Phase 5: Lexi History Manager

**Goal:** Persist conversation history for Lexi (continuity).

**Status:** ✅ ALREADY IMPLEMENTED via existing infrastructure

**Analysis:** The existing `ConversationStore` already handles everything:
1. ✅ Stores all messages (file-based JSON, user-scoped)
2. ✅ `get_messages_for_api()` retrieves history (chat.py:495)
3. ✅ `lexi_service.build_messages(history=...)` processes it correctly
4. ✅ Lexi's 8K context naturally limits history; compaction handles long convos

**No new file needed** - ConversationStore is already the Lexi history manager.

---

## Phase 6: Chat Router Integration

**Goal:** Wire everything together in chat.py.

**Modify:** `app/routers/chat.py`

**Changes:**
1. Import AdultOrchestrator
2. After adult mode check (line ~460), route to orchestrator
3. Stream response from orchestrator
4. Handle errors gracefully (inject to Lexi naturally)

**Before:**
```python
if use_lexi:
    async for chunk in lexi_service.chat_stream(...):
        yield chunk
```

**After:**
```python
if use_lexi:
    orchestrator = AdultOrchestrator()
    async for chunk in orchestrator.process_message(...):
        yield chunk
```

---

## Phase 7: File Storage & Delivery

**Goal:** Store generated images in Nextcloud, inject URLs to Lexi.

**Implementation:**
1. Add Nextcloud upload helper (WebDAV)
2. Upload generated images to `joel/Lexi/` folder
3. Return public URL for injection

**Files to modify:**
- `app/services/file_storage.py` (NEW) — Nextcloud WebDAV upload
- `app/services/adult_orchestrator.py` — Use file storage after generation

---

## Phase 8: Memory System for Lexi

**Goal:** Enable Lexi to save/retrieve memories like Claude does.

**From ARCHITECTURE.md:** Lexi uses `[MEMORY]` tag extraction (existing pattern).

**Implementation:**
1. Add memory extraction instructions to Lexi's system prompt
2. Inject retrieved memories into Lexi's context (like Claude path)
3. Use existing `memory_extractor.py` for tag parsing

**Files to modify:**
- `app/services/lexi_service.py` — Add memory prompt + injection
- `app/services/memory_extractor.py` — Already supports `[MEMORY]` tags

**Memory Flow:**
```
User message → Query memory store → Inject to Lexi prompt
Lexi responds with [MEMORY] tags → Extract → Store
User sees response (tags stripped)
```

---

## Phase 9: Configuration & Testing

**New Environment Variables:**
```bash
# Add to .env
OMEGA_MODEL=qwen3-vl-abliterated    # Or selected model
TOOL_TRIGGER_ENABLED=true
ADULT_ORCHESTRATOR_ENABLED=true
FAL_KEY=...                          # fal.ai API key
```

**Testing:**
1. Test trigger scanner with various messages
2. Test Omega tool planning accuracy
3. Test full pipeline end-to-end
4. Test fast path (no triggers → Lexi direct)
5. Test false positive path (trigger → no tool → Lexi)

---

## File Summary

**New Files:**
| File | Purpose |
|------|---------|
| `app/services/trigger_scanner.py` | Regex-based tool detection |
| `app/services/omega_service.py` | Ollama wrapper for Omega |
| `app/services/adult_orchestrator.py` | Full pipeline coordination |
| `app/services/lexi_history_manager.py` | Conversation persistence |
| `app/services/file_storage.py` | Nextcloud upload |
| `app/tools/omega_definitions.py` | Tool schemas with env vars |

**Modified Files:**
| File | Changes |
|------|---------|
| `app/routers/chat.py` | Route adult mode to orchestrator |
| `app/services/tool_executor.py` | Add execute_omega_tool |
| `app/services/lexi_service.py` | Use history manager |
| `app/models/schemas.py` | Add OmegaToolCall schema |
| `app/config.py` | Add Omega/trigger configs |
| `.env` | Add new environment variables |

---

## Verification

1. **Trigger Scanner Test:**
   ```bash
   # Should return True
   python -c "from app.services.trigger_scanner import has_tool_triggers; print(has_tool_triggers('generate an image of a cat'))"
   ```

2. **Fast Path Test:**
   - Send "Tell me something naughty" in adult mode
   - Verify Lexi responds directly (no Omega call)
   - Check latency ~2-4s

3. **Tool Path Test:**
   - Send "Generate a sexy pic of a redhead" in adult mode
   - Verify trigger scan → OpenClaw classify → Omega plan → execute → Lexi respond
   - Check image appears in chat
   - Check Lexi's response references the image

4. **History Test:**
   - Send follow-up "Make another one"
   - Verify Lexi understands context from history

---

## Open Questions

1. **OpenClaw NSFW Classification:** Will Claude classify "generate sexy pic" as tool request without refusing? Needs testing. Fallback: skip OpenClaw, send all triggered messages to Omega.

2. **Omega Base Model:** Still evaluating options (huihui_ai/qwen3-vl-abliterated, aeline/Omega, Qwen/Qwen3-VL-2B). Selection criteria in FINE_TUNING.md.

---

## Implementation Order

1. Trigger Scanner (standalone, testable)
2. Omega Service (standalone, testable)
3. Tool Execution updates
4. Lexi History Manager
5. Memory System for Lexi
6. Adult Orchestrator (integrates 1-5)
7. Chat Router Integration
8. File Storage
9. Testing & Polish

---

## Progress Tracking

- [x] Phase 1: Trigger Scanner
- [x] Phase 2: Omega Service
- [x] Phase 3: Adult Orchestrator
- [x] Phase 4: Tool Execution
- [x] Phase 5: Lexi History Manager (existing ConversationStore)
- [x] Phase 6: Chat Router Integration
- [x] Phase 7: File Storage & Delivery
- [x] Phase 8: Memory System for Lexi
- [x] Phase 9: Configuration & Testing
