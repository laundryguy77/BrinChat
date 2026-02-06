# BrinChat

A web-based chat interface for Claude via OpenClaw, with optional Lexi (uncensored) mode support.

![BrinChat Interface](docs/screen.png)

## Features

- **ChatGPT-style interface** - Clean, responsive UI built with Tailwind CSS
- **OpenClaw integration** - Full access to Claude with tools, memory, and context
- **Unified session routing** - Joel shares main OpenClaw session across all interfaces
- **Voice support** - Push-to-talk and VAD modes with Edge TTS (0.5s latency)
- **File upload** - PDF, code, images, ZIP archives
- **Lexi mode** - Optional uncensored mode via local Ollama (passcode protected)
- **Automatic task extraction** - Non-Joel user requests create Deck cards for Brin
- **Accessibility** - WCAG 2.1 compliant with screen reader support

## Quick Start

```bash
# Clone and enter directory
cd /home/tech/projects/BrinChat

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.template .env
# Edit .env with your settings (JWT_SECRET and ADULT_PASSCODE required)

# Run
./start_brinchat.sh
```

Access at http://localhost:8081 (or https://brin.cullerdigitalmedia.com externally)

## Configuration

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `JWT_SECRET` | Secret key for JWT tokens (≥32 chars) |
| `ADULT_PASSCODE` | Passcode to unlock Lexi mode |

### Optional Features

| Variable | Description |
|----------|-------------|
| `BRAVE_SEARCH_API_KEY` | Enable web search |
| `HF_TOKEN` | Enable video generation |
| `VOICE_ENABLED=true` | Enable voice features |
| `TTS_BACKEND` | `edge` (default), `piper`, `coqui`, `kokoro` |

### Session Routing

Joel (user ID 4 or username "Joel") routes to the main OpenClaw session (`agent:main:main`), sharing context across BrinChat, CLI, webchat, and Nextcloud Talk.

Other users get isolated stable sessions (`agent:main:openai-user:brinchat:{username}`).

See [docs/SESSION_ROUTING.md](docs/SESSION_ROUTING.md) for details.

## Architecture

```
BrinChat (port 8081)
├── Frontend: HTML + Tailwind CSS + Vanilla JS
├── Backend: FastAPI + SQLite
├── Chat: OpenClaw API (Claude) or Ollama (Lexi)
└── Voice: Whisper STT (5001) + Edge/Qwen TTS (5002)
```

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl/Cmd + N` | New conversation |
| `Ctrl/Cmd + K` | Focus search |
| `Ctrl/Cmd + /` | Toggle sidebar |
| `/` | Focus message input |
| `Enter` | Send message |
| `Shift + Enter` | New line |
| `Escape` | Close modals |

## Development

### File Structure

```
BrinChat/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── config.py            # Configuration
│   ├── routers/             # API endpoints
│   ├── services/            # Business logic
│   └── models/              # Pydantic schemas
├── static/
│   ├── index.html           # Main UI
│   └── js/                  # Frontend modules
├── conversations/           # User chat history (JSON)
└── docs/                    # Documentation
```

### Rate Limits

- **Chat**: 30 messages/minute per user
- **Files**: 10 files, 50MB each per request
- **Message**: 100KB max payload

### Security

- JWT authentication with secure cookies
- XSS protection via DOMPurify + security headers
- CSRF protection via same-origin policy
- Rate limiting on all sensitive endpoints
- Input validation and size limits

## License

Private - Culler Digital Media

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history.
