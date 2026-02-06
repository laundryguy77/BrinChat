# BrinChat Adult Mode - Gap Documentation

Document gaps, missing support code, and issues discovered during implementation.  
**Do NOT fix these during the implementation plan — document only.**

---

## Gap Categories

| Category | Description |
|----------|-------------|
| **MISSING_CODE** | Required code/module doesn't exist yet |
| **INTEGRATION** | Integration point with existing code unclear or missing |
| **CONFIG** | Configuration option not available |
| **DEPENDENCY** | External dependency needed |
| **UNCLEAR** | Specification unclear, needs clarification |

---

## Documented Gaps

### Phase 1: Trigger Scanner

| Gap ID | Type | Description | Severity |
|--------|------|-------------|----------|
| GAP-P1-001 | UNCLEAR | `animation` pattern lacks action verb — matches past tense references like "the animation was cool" (by design, but worth reviewing) | Low |
| GAP-P1-002 | MISSING_CODE | Missing negative test cases for broad patterns (false positive scenarios) | Low |
| GAP-P1-003 | MISSING_CODE | No integration test showing TriggerScanner → routing decision flow | Medium |
| GAP-P1-004 | MISSING_CODE | `trigger_scanner` module NOT imported in `chat.py` (needed for Phase 6) | Medium |
| GAP-P1-005 | MISSING_CODE | `TOOL_TRIGGER_ENABLED` config NOT imported in `chat.py` (needed for Phase 6) | Medium |
| GAP-P1-006 | INTEGRATION | No conditional logic in chat.py to check triggers when `use_lexi=True` | Medium |
| GAP-P1-007 | UNCLEAR | When switching from Lexi→Brin for tool access, unclear if sensitive profile sections should still load | Medium |
| GAP-P1-008 | INTEGRATION | No debug context emission for trigger scanning decisions (frontend won't know why routed to Brin) | Low |
| GAP-P1-009 | UNCLEAR | System prompt handling unclear - minimal OpenClaw prompt may not include uncensored context when switching from Lexi | Medium |

**Notes:**
- Gaps 004-009 are expected — they will be addressed in Phase 6 (Chat Router Integration)
- Broad patterns (web_search, file_ops) match liberally by design (prefer false positives)

---

### Phase 2: Omega Service

| Gap ID | Type | Description | Severity |
|--------|------|-------------|----------|
| GAP-P2-001 | DUPLICATE | `OmegaToolCall` defined twice: dataclass in `omega_service.py:21-60` AND Pydantic model in `schemas.py:54-68`. Incompatible defaults (`prompt: Optional[str]` vs `prompt: str = ""`). | High |
| GAP-P2-002 | INCONSISTENCY | `omega_definitions.py::get_tool_definitions_prompt()` documents `<tool_call>` XML tags, but `TOOL_PLANNING_PROMPT` in `omega_service.py` expects raw JSON. Misleading documentation. | Medium |
| GAP-P2-003 | UNCLEAR | `omega_definitions.py` defines tool execution details (endpoints, auth headers) but `OmegaService` doesn't use them. Is this file for Phase 4 (tool execution) not Phase 2? | Low |
| GAP-P2-004 | MISSING_CODE | No unit tests for `OmegaService` — `plan_tool_call`, `describe_image`, `_parse_tool_response` have no test coverage | Medium |
| GAP-P2-005 | CONFIG | `OMEGA_*` vars duplicated: read via `os.getenv()` in `omega_service.py:12-14` AND defined in `config.py:51-53`. Should use single source of truth (import from config). | Low |
| GAP-P2-006 | INTEGRATION | `_fetch_image_as_base64()` doesn't verify content-type before encoding — could pass non-image data to vision model | Low |

| GAP-P2-007 | INTEGRATION | Tool name mismatch: omega_service uses "image"/"video"/"websearch" but omega_definitions uses "generate_image"/"generate_video"/"web_search" — Phase 4 execution will fail without mapping | High |
| GAP-P2-008 | MISSING_CODE | No ${VAR} substitution implementation for FAL_KEY, BRAVE_SEARCH_API_KEY placeholders (needed Phase 4) | Medium |
| GAP-P2-009 | CONFIG | config.py lacks FAL_KEY environment variable definition needed for Phase 4 | Medium |

**Notes:**
- GAP-P2-001, GAP-P2-007 are highest priority — duplicate definitions and naming mismatches
- GAP-P2-003 is intentional — omega_definitions.py designed for Phase 4 tool executor
- Import test passes, so basic structure is sound

---

### Phase 3: Adult Orchestrator

| Gap ID | Type | Description | Severity |
|--------|------|-------------|----------|
| GAP-P3-001 | INTEGRATION | Classification timeout/error returns `"unknown"` tool_type, passed to `_omega_pipeline()`. Placeholder handles gracefully, but real Phase 4 execution needs explicit "unknown" handling | Medium |
| GAP-P3-002 | INTEGRATION | chat.py doesn't handle adult mode SSE events (`routing`, `classification`, `status`, `tool_plan`, `tool_result`) — needs event handling in Phase 6 | Medium |
| GAP-P3-003 | MISSING_CODE | No integration test for orchestrator → TriggerScanner → Omega → Lexi flow | Medium |
| GAP-P3-004 | MISSING_CODE | No unit tests for `_classify_with_openclaw()` error paths | Low |

**Notes:**
- Code quality excellent — proper type hints, docstrings, async patterns, SSE formatting
- Pipeline logic sound — trigger scan → fast path/tool path routing works correctly
- All service imports work — TriggerScanner, OmegaService, LexiService, LEXI_PERSONA
- Phase 4 placeholder properly marked with warnings/logging
- GAP-P3-002 expected — will be addressed in Phase 6 (Chat Router Integration)

---

### Phase 4: Tool Execution

*To be filled during Phase 4*

---

### Phase 5: Lexi History Manager

*To be filled during Phase 5*

---

### Phase 6: Chat Router Integration

*To be filled during Phase 6*

---

### Phase 7: File Storage & Delivery

*To be filled during Phase 7*

---

### Phase 8: Memory System for Lexi

*To be filled during Phase 8*

---

### Phase 9: Configuration & Testing

*To be filled during Phase 9*

---

## Summary Statistics

| Phase | Gaps Found | Critical | Resolved |
|-------|------------|----------|----------|
| 1 | 9 | 0 | 0 |
| 2 | 9 | 2 | 0 |
| 3 | 4 | 0 | 0 |
| 4 | 0 | 0 | 0 |
| 5 | 0 | 0 | 0 |
| 6 | 0 | 0 | 0 |
| 7 | 0 | 0 | 0 |
| 8 | 0 | 0 | 0 |
| 9 | 0 | 0 | 0 |
| **Total** | 0 | 0 | 0 |
