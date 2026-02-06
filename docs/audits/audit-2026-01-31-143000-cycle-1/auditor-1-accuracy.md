# Session Lifecycle Documentation - Technical Accuracy Audit

**Auditor:** Subagent (Accuracy Focus)  
**Date:** 2026-01-31  
**Document:** `/home/tech/projects/BrinChat/docs/session-lifecycle.md`  
**Status:** ⚠️ MULTIPLE CRITICAL INACCURACIES FOUND

---

## Executive Summary

The session-lifecycle.md document contains **significant fabrications** and inaccuracies. Many of the detailed TypeScript interfaces, event schemas, and API structures do not exist in the actual OpenClaw codebase. The document appears to describe an idealized or speculative architecture rather than the actual implementation.

**Severity:** HIGH - Document cannot be trusted for technical reference without major corrections.

---

## Critical Inaccuracies

### 1. Hook Event Schemas Are Fabricated

| Field | Content |
|-------|---------|
| **Severity** | Critical |
| **Location** | Part 2, all event sections |
| **Claim** | Elaborate TypeScript interfaces like `SessionStartEvent { type, action, sessionKey, agentId, timestamp, isResume, workspace, metadata }` |
| **Actual** | Events have only: `{ type, action, sessionKey, context, timestamp, messages }` |
| **Source** | `/home/tech/.npm-global/lib/node_modules/openclaw/dist/hooks/internal-hooks.js` - `createInternalHookEvent()` |

### 2. Hook/Event Names Are Wrong

| Field | Content |
|-------|---------|
| **Severity** | Critical |
| **Location** | Part 2, Event Reference headers |
| **Claim** | PascalCase events: `PreToolUse`, `PostToolUse`, `SessionStart`, etc. |
| **Actual** | snake_case: `before_tool_call`, `after_tool_call`, `session_start`, etc. |
| **Source** | `/home/tech/.npm-global/lib/node_modules/openclaw/dist/plugins/hooks.js` |

### 3. Session Transcript JSONL Format Is Wrong

| Field | Content |
|-------|---------|
| **Severity** | Critical |
| **Location** | Appendix B, Data Structure Reference |
| **Claim** | Simple `{ role, content, timestamp }` format |
| **Actual** | Envelope format: `{ type, id, parentId, timestamp, message: { role, content: [...blocks] } }` |
| **Source** | `~/.openclaw/agents/main/sessions/*.jsonl` (actual files) |

### 4. Queue Mode "interrupt" Does Not Exist

| Field | Content |
|-------|---------|
| **Severity** | Major |
| **Location** | Part 1, Queue Mode descriptions |
| **Claim** | Queue modes include "interrupt" |
| **Actual** | Modes are: `steer`, `followup`, `collect`, `steer-backlog`, `steer+backlog` |
| **Source** | `/home/tech/.npm-global/lib/node_modules/openclaw/dist/config/zod-schema.core.js` |

### 5. Agent Loop Event Stream Is Wrong

| Field | Content |
|-------|---------|
| **Severity** | Critical |
| **Location** | Part 2, entire event flow |
| **Claim** | Events like `SessionStart`, `UserPromptSubmit`, `PreToolUse` |
| **Actual** | Events: `agent_start`, `turn_start`, `message_start/update/end`, `tool_execution_start/update/end`, `turn_end`, `agent_end` |
| **Source** | `/home/tech/.npm-global/lib/node_modules/openclaw/dist/agents/steerable-agent-loop.js` |

### 6. Two Hook Systems Conflated

| Field | Content |
|-------|---------|
| **Severity** | Major |
| **Location** | Throughout document |
| **Claim** | Single unified hook system |
| **Actual** | Two separate systems: External Webhooks (`gateway/hooks.js`) and Internal Lifecycle Hooks (`hooks/internal-hooks.js`) |
| **Source** | Both files in openclaw/dist/ |

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 4 |
| Major | 2 |
| Minor | 0 |
| Unverifiable | 3 |

**Recommendation:** Do NOT use this document as a technical reference until corrected. Much content appears speculative rather than implementation documentation.
