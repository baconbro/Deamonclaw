# DAEMON Sidecar for OpenClaw

A Python sidecar that runs alongside [OpenClaw](https://github.com/openclaw/openclaw)
and adds capabilities OpenClaw doesn't have out of the box:

| Capability | Provided by |
|---|---|
| WhatsApp, Telegram, Slack, Discord, Signal, iMessage | **OpenClaw** |
| Browser automation, shell, file tools | **OpenClaw** |
| Voice wake / talk mode, mobile apps | **OpenClaw** |
| ClawHub skill ecosystem | **OpenClaw** |
| **Perception pipeline** (audio VAD + Whisper, screen OCR) | **DAEMON sidecar** |
| **Salience filter** (attention scoring) | **DAEMON sidecar** |
| **Consciousness manager** (sleep/wake/focused states) | **DAEMON sidecar** |
| **Structured memory** (PostgreSQL + pgvector) | **DAEMON sidecar** |
| **Permission tiers + safety layer + audit ledger** | **DAEMON sidecar** |
| **Proactive engagement engine** (interrupt budget, quiet hours) | **DAEMON sidecar** |
| **Cost-aware tracking** | **DAEMON sidecar** |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    DAEMON Sidecar (Python)                   │
│                    FastAPI on :8400                          │
│                                                              │
│  Perception Plane  │  Consciousness  │  Structured Memory   │
│  Safety Layer      │  Proactive Eng  │  Cost Tracker        │
└────────┬───────────┴────────┬────────┴──────────────────────┘
         │  Integration       │  Integration       Integration │
         │  Point 1           │  Point 2           Point 3     │
         │  (Skills/REST)     │  (WebSocket→)      (←WebSocket)│
┌────────┴───────────────────┴────────────────────────────────┐
│                        OpenClaw                              │
│  Channels · Gateway · Agent Loop · Tools · ClawHub Skills   │
└─────────────────────────────────────────────────────────────┘
```

Three integration points connect the sidecar to OpenClaw:

1. **Skills → REST API** — OpenClaw skills call `http://localhost:8400/api/...`
2. **WebSocket client** — sidecar pushes proactive messages through OpenClaw's gateway
3. **WebSocket listener** — sidecar monitors all OpenClaw events for the audit ledger

## Quick Start

### Prerequisites

- OpenClaw already running with its gateway on `ws://localhost:18789`
- Docker and Docker Compose

### 1. Clone and configure

```bash
git clone https://github.com/yourusername/daemon-sidecar.git
cd daemon-sidecar
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY
```

### 2. Start sidecar services

```bash
docker compose up -d
```

This starts:
- **daemon-sidecar** — the FastAPI sidecar on port 8400
- **daemon-db** — PostgreSQL 16 with pgvector on port 5433
- **daemon-whisper** — faster-whisper speech-to-text server on port 8500
- **daemon-ollama** — local Ollama models on port 11435

### 3. Install DAEMON skills into OpenClaw

```bash
./install-skills.sh /path/to/openclaw/workspace
```

Installed skills:

| Skill | Purpose |
|---|---|
| `daemon-memory` | Store and recall episodic memories |
| `daemon-safety` | Pre-action permission checks + audit log |
| `daemon-perception` | Query audio transcriptions and screen context |
| `daemon-context` | Receive injected environmental context |

### 4. Restart OpenClaw

```bash
openclaw restart
```

### 5. Verify

```bash
curl http://localhost:8400/health
curl http://localhost:8400/api/consciousness/status
curl http://localhost:8400/api/dashboard/overview
```

Or send a message to your OpenClaw via WhatsApp / Telegram:
> "What's the DAEMON status?"

The agent will call `/api/consciousness/status` and reply with the current
power level, active sensors, and costs.

## API Reference

### Memory

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/memory/store` | Store an episodic memory |
| `POST` | `/api/memory/recall` | Semantic / keyword recall |
| `GET` | `/api/memory/recent?limit=10` | Most recent memories |

### Safety

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/safety/check` | Permission tier check (0–3) |
| `POST` | `/api/safety/log` | Log a completed action |

### Perception

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/perception/audio/recent?minutes=30` | Recent transcriptions |
| `GET` | `/api/perception/screen/current` | Current screen context |
| `GET` | `/api/perception/events?min_salience=0.5` | High-salience events |
| `POST` | `/api/perception/search` | Search perceptions by text |

### Consciousness

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/consciousness/status` | Power level + active sensors |
| `POST` | `/api/consciousness/wake?level=engaged` | Force wake |
| `POST` | `/api/consciousness/sleep` | Force deep sleep |

### Bridge (Phase 3)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/bridge/inject-context` | Build + inject `<daemon_context>` XML |
| `GET` | `/api/bridge/status` | WebSocket connection health |

### Dashboard

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Web dashboard UI |
| `GET` | `/api/dashboard/overview` | Full JSON status overview |
| `GET` | `/api/dashboard/ledger?limit=20` | Recent audit ledger entries |

## Configuration

Edit `daemon.yaml` to customise:

- **Perception** — enable/disable audio/screen/video sensors, Whisper URL
- **Consciousness** — idle and deep-sleep timeouts
- **Proactive** — interrupt budget, quiet hours, salience threshold
- **Safety** — tier patterns for permission classification
- **Thinking** — Claude model, max tokens, cadence per consciousness level, auto_communicate flag

All values can be overridden with environment variables (see `.env.example`).

## Development Roadmap

### Phase 1 (Weeks 1–2): Foundation ✓
- [x] FastAPI sidecar skeleton
- [x] PostgreSQL + pgvector memory
- [x] Safety permission gate + audit ledger
- [x] OpenClaw skills (memory, safety, perception, context)
- [x] Docker Compose stack

### Phase 2 (Weeks 3–4): Perception ✓
- [x] Real audio sensor — pyaudio + webrtcvad VAD + Whisper HTTP transcription
- [x] Real screen sensor — mss screenshot + pytesseract OCR + xdotool window title
- [x] Video sensor — opencv motion detection, opt-in (disabled by default)
- [x] Embedding-based salience scoring — sentence-transformers `all-MiniLM-L6-v2`
- [x] Vector recall — pgvector cosine similarity search for memory recall
- [x] Embedding storage — episodes stored with embedding vectors for semantic lookup

### Phase 3 (Weeks 5–6): Proactive + Bridge ✓
- [x] Full OpenClaw gateway WebSocket integration with outbound message queue
- [x] Automatic reconnect with exponential backoff (2 → 4 → 8 → 16 → 32 → 60s)
- [x] Message queue — up to 50 messages buffered while offline, flushed on reconnect
- [x] Context builder — assembles `<daemon_context>` XML from audio, screen, memories
- [x] Context injection into OpenClaw conversations via `POST /api/bridge/inject-context`
- [x] Event listener — monitors all OpenClaw WebSocket events for live ledger population
- [x] Bridge status endpoint — `GET /api/bridge/status`

### Continuous Thinking (ongoing) ✓
- [x] `ContinuousThinkingEngine` — cadenced Claude reasoning loop while DAEMON is awake
- [x] Two transparent output types streamed to the dashboard in real-time:
  - `<think>` — inner monologue (italic, streamed live via SSE)
  - `<message priority="…">` — official user communication (delivered via bridge)
  - `<memory>` — auto-persists important observations to episodic memory
- [x] Cadence adapts to consciousness level: 20s (focused) → 45s → 2min → 10min
- [x] `GET /api/thinking/stream` — SSE endpoint for live thinking chunks
- [x] `GET /api/thinking/history` — completed cycle ring buffer
- [x] `GET /api/thinking/communications` — all generated user communications
- [x] `POST /api/thinking/trigger` — manually trigger an immediate cycle
- [x] Dashboard redesigned as a split-panel UI:
  - Left: status cards + recent communications + audit ledger
  - Right: **Live Stream** tab (inner monologue in real-time) + **Communications** tab

### Phase 4 (Weeks 7–8): Polish ✓
- [x] Web dashboard — dark-theme UI served at `/`, auto-refreshes every 5 seconds
  - Consciousness level with animated indicator
  - Active sensors, salient events list, cost tracker
  - Bridge connection status, pending approvals
  - Audit ledger table with tier colour coding
- [x] Video sensor — motion-detected webcam events with JPEG thumbnails
- [x] ClawHub publish script — `clawhub/publish.sh`

## License

MIT — see [LICENSE](LICENSE).
