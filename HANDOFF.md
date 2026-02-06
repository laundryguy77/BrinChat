# BrinChat Adult Mode Implementation - Handoff Document

## 🔄 Phase Cycle Instructions

**Read these instructions before continuing work:**

1. **Review the phase plan** — Read `ADULT_MODE_PLAN.md` for the current phase, figure out how to manage sub-agents using the manage-team skill (`~/clawd/skills/orchestration/manage-team/`)

2. **Spawn agents and assign** — Use `sessions_spawn` to create implementation sub-agents for the phase work

3. **Review and audit** — When implementation agents complete, spawn auditor sub-agents to critically examine the work and document gaps

4. **Update plan log** — Record progress in `IMPLEMENTATION_LOG.md` and check the box in `ADULT_MODE_PLAN.md`

5. **Update this handoff document** — Update the summary below for the next session

**Before beginning any phase work:**
- Read `/home/tech/clawd/BrinChat.md` — Main vision document
- Read `/home/tech/projects/BrinChat/ADULT_MODE_PLAN.md` — Detailed implementation plan
- Read `/home/tech/clawd/projects/brinchat-uncensored/FINE_TUNING.md` — Omega model plan (when relevant)

**Constraints:**
- Make NO assumptions about support code existing
- Document gaps in `GAPS.md` — do NOT try to fix things outside the plan
- One cycle minimum per phase

---

## Current State Summary

**Last Updated:** 2026-02-01 02:35 EST  
**Current Phase:** Phase 3 (Adult Orchestrator) — Starting  
**Overall Progress:** 22% (2/9 phases complete)

### What's Complete

- [x] **Phase 1: Trigger Scanner** ✅
  - Created `app/services/trigger_scanner.py` with TriggerScanner class
  - Added `TOOL_TRIGGER_ENABLED` config option
  - 9 gaps documented

- [x] **Phase 2: Omega Service** ✅
  - Created `app/services/omega_service.py` with OmegaService class
  - Created `app/tools/omega_definitions.py` with tool schemas
  - Added config + schema (OMEGA_MODEL, OmegaToolCall)
  - 9 gaps documented (2 high severity: duplicate definitions, name mismatch)

### What's In Progress

- [ ] Phase 3: Adult Orchestrator — **STARTING NOW**

### What's Remaining

- [ ] Phase 4: Tool Execution
- [ ] Phase 5: Lexi History Manager
- [ ] Phase 6: Chat Router Integration
- [ ] Phase 7: File Storage & Delivery
- [ ] Phase 8: Memory System for Lexi
- [ ] Phase 9: Configuration & Testing

---

## Key Decisions Made

### Decision 1: Prefer False Positives
**Context:** Trigger patterns could be tight (fewer matches) or broad (more matches)  
**Chosen:** Broad patterns — prefer false positives over false negatives  
**Rationale:** Better to route to Brin unnecessarily than to miss a tool request Lexi can't handle

### Decision 2: Module-level Convenience Functions
**Context:** Could require class instantiation or provide module-level functions  
**Chosen:** Both — class for flexibility, module functions for ease of use  
**Rationale:** `from app.services.trigger_scanner import has_tool_triggers` is cleaner for simple use cases

---

## Known Issues & Blockers

See `GAPS.md` for full list. Key integration gaps for Phase 6:
- GAP-P1-004/005: Imports needed in chat.py
- GAP-P1-006: Conditional logic needed for trigger check
- GAP-P1-007/009: Profile/prompt context unclear when switching Lexi→Brin

---

## Files Modified

| File | Changes | Phase |
|------|---------|-------|
| `app/services/trigger_scanner.py` | **NEW** — TriggerScanner class | Phase 1 |
| `app/config.py` | Added `TOOL_TRIGGER_ENABLED`, `OMEGA_*` configs | Phase 1, 2 |
| `app/services/omega_service.py` | **NEW** — OmegaService class | Phase 2 |
| `app/tools/omega_definitions.py` | **NEW** — Tool schemas with ${VAR} placeholders | Phase 2 |
| `app/models/schemas.py` | Added `OmegaToolCall` Pydantic model | Phase 2 |

---

## Environment State

- **Project Location:** `/home/tech/projects/BrinChat`
- **Service:** `sudo systemctl status brinchat`
- **Python venv:** `/home/tech/projects/BrinChat/venv`
- **Config:** `.env` file in project root

---

## Suggested Next Steps

**For Phase 2 (Omega Service):**

1. Read vision documents:
   - `/home/tech/clawd/BrinChat.md` — Omega role in adult mode pipeline
   - `/home/tech/clawd/projects/brinchat-uncensored/FINE_TUNING.md` — Omega model selection

2. Review Phase 2 in `ADULT_MODE_PLAN.md`:
   - Create `app/services/omega_service.py`
   - Create `app/tools/omega_definitions.py`
   - Add `OmegaToolCall` schema to `app/models/schemas.py`
   - Add `OMEGA_MODEL`, `OMEGA_BASE_URL` to config

3. Spawn implementation agents:
   - Agent 1: Create omega_service.py (Ollama wrapper)
   - Agent 2: Create omega_definitions.py (tool schemas)
   - Agent 3: Add config options and schema

4. Spawn auditor agents:
   - Code auditor: Review implementation quality
   - Integration auditor: Check Ollama API compatibility, vision support

5. Update HANDOFF.md and check the phase box

---

## Gap Documentation

See `GAPS.md` for all documented gaps and missing support code.
