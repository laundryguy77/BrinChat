# BrinChat Session Routing & Task Extraction

## Overview

BrinChat supports **unified session routing** with OpenClaw, allowing Joel (the primary user) to share context with the main OpenClaw session while other users get isolated sessions with **automatic task extraction**.

## How It Works

### For Joel (Primary User)

1. **Main Session Routing** (`agent:main:main`)
   - Joel's requests route to the main OpenClaw session
   - Same context as CLI, webchat, Nextcloud Talk, etc.
   - Full access to SOUL.md, IDENTITY.md, USER.md, MEMORY.md, tools
   - Seamless conversation continuity across all channels
   - Detected by user ID (4) OR username ("Joel", case-insensitive)

2. **Benefits**:
   - "Same Brin everywhere"
   - Files shared in one channel accessible in others
   - Memory and context preserved across channels
   - Can reference conversations from any channel

### For Other Users

1. **Isolated Sessions** (`agent:main:openai-user:brinchat:{username}`)
   - Stable per-user sessions (not per-request)
   - Cannot see Joel's context or files
   - Each user gets their own conversation history

2. **Automatic Task Extraction**
   - When a user makes a request that looks like a task, it's automatically extracted
   - A Nextcloud Deck card is created in the Backlog
   - Brin processes these tasks during normal operation
   - Users don't need direct access - they just ask for things

### Configuration

In `.env`:
```bash
# Primary user detection (ID OR username)
OPENCLAW_PRIMARY_USER_ID=4
OPENCLAW_PRIMARY_USERNAME=Joel
OPENCLAW_MAIN_SESSION_KEY=agent:main:main

# Nextcloud Deck for task extraction
NEXTCLOUD_URL=http://10.10.10.140:8080
NEXTCLOUD_USER=admin
NEXTCLOUD_PASS=admin123
DECK_BOARD_ID=2
DECK_BACKLOG_STACK_ID=5
```

## Technical Implementation

### Session Routing

1. **Primary User Detection**
   - `_is_primary_user()` in `claude_service.py` checks both ID and username
   - Case-insensitive username matching
   - Either match grants primary user status

2. **Primary User (Joel)**
   - HTTP header: `x-openclaw-session-key: agent:main:main`
   - Routes directly to main OpenClaw session
   - Full context sharing with all channels

3. **Other Users**
   - Payload field: `user: brinchat:{username}`
   - OpenClaw derives stable session key
   - Isolated but persistent sessions

### Task Extraction

1. **Detection**
   - Quick keyword pre-filter (can you, please, help me, etc.)
   - Uses EXTRACTION_MODEL (qwen2.5-coder:3b) to analyze messages
   - Conservative detection - only clear task requests

2. **Card Creation**
   - Creates card in Nextcloud Deck Backlog
   - Title prefixed with `[BrinChat]`
   - Description includes requester info and original message
   - Brin processes cards during normal heartbeat/polling

## Files Changed

- `app/config.py`: Added routing and Deck configuration
- `app/services/claude_service.py`: 
  - Added `_is_primary_user()` for dual ID/username detection
  - Updated `_get_headers()` and `_get_user_field()` for routing
- `app/services/task_extraction_service.py`: NEW - Task detection and Deck card creation
- `app/routers/chat.py`: 
  - Import task extraction service
  - Call task extraction after assistant response for non-primary users

## Testing

### Session Routing

1. **Verify Joel routes to main session:**
   ```bash
   # Before chat
   openclaw sessions --json | jq '.sessions[] | .key' | grep main
   
   # Chat as Joel in BrinChat
   # After chat - should still be agent:main:main, no new openai:UUID session
   ```

2. **Verify other users get isolated sessions:**
   ```bash
   # Chat as another user in BrinChat
   openclaw sessions --json | jq '.sessions[] | select(.key | contains("brinchat"))'
   # Should see: agent:main:openai-user:brinchat:{username}
   ```

### Task Extraction

1. **As a non-Joel user, send a task request:**
   - "Can you create a script to backup the database?"
   - "Please update the homepage with new links"

2. **Check Deck board:**
   ```bash
   curl -s -u admin:admin123 \
     "http://10.10.10.140:8080/index.php/apps/deck/api/v1.0/boards/2/stacks/5/cards" \
     -H "OCS-APIRequest: true" | jq '.[].title'
   ```

3. **Verify card was created:**
   - Title should be `[BrinChat] {extracted task title}`
   - Description includes requester name and original message

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         BrinChat                                │
├─────────────────────────────────────────────────────────────────┤
│  User Login → is_primary_user() check                          │
│       │                                                         │
│       ├── Joel (ID=4 OR username="Joel")                       │
│       │       │                                                 │
│       │       └── x-openclaw-session-key: agent:main:main      │
│       │               │                                         │
│       │               └── Main Session (shared context)        │
│       │                                                         │
│       └── Other Users                                          │
│               │                                                 │
│               ├── user: brinchat:{username}                    │
│               │       │                                         │
│               │       └── Isolated Session                     │
│               │                                                 │
│               └── Task Extraction → Deck Card                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    OpenClaw Gateway                             │
├─────────────────────────────────────────────────────────────────┤
│  agent:main:main ←── Joel's BrinChat                           │
│       ↑              ←── CLI                                   │
│       ↑              ←── Webchat                               │
│       ↑              ←── Nextcloud Talk                        │
│       └── Unified Context (SOUL.md, MEMORY.md, tools, etc.)    │
│                                                                 │
│  agent:main:openai-user:brinchat:alice ←── Alice's BrinChat   │
│  agent:main:openai-user:brinchat:bob   ←── Bob's BrinChat     │
│       └── Isolated per-user sessions                           │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    Nextcloud Deck                               │
├─────────────────────────────────────────────────────────────────┤
│  Board: Brin Tasks (ID: 2)                                     │
│       │                                                         │
│       ├── Backlog (ID: 5) ←── Task cards from BrinChat users  │
│       ├── In Progress (ID: 6)                                  │
│       ├── Blocked (ID: 7)                                      │
│       └── Done (ID: 8)                                         │
└─────────────────────────────────────────────────────────────────┘
```

## Notes

- OpenClaw's `/v1/chat/completions` endpoint supports:
  - `x-openclaw-session-key` header for explicit session routing
  - `user` field in payload for stable user-based session derivation
- When both are present, the header takes precedence
- Session routing is transparent to the frontend
- Task extraction uses qwen2.5-coder:3b (fast, local) via Ollama
- Cards are created asynchronously after response - doesn't block chat
