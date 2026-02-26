---
name: daemon-memory
description: >
  Advanced memory system with semantic search. Use this skill whenever the
  user asks you to remember something, recall past events, find similar
  past experiences, or when you need context from previous tasks.
triggers:
  - "remember this"
  - "what did we do last time"
  - "similar to before"
  - "recall"
  - "find past"
---

## Usage

When you need to store or recall memories, call the DAEMON memory API:

### Store a memory
```bash
curl -X POST http://localhost:8400/api/memory/store \
  -H "Content-Type: application/json" \
  -d '{
    "type": "episodic",
    "goal": "what the task was",
    "outcome": "what happened",
    "success": true,
    "tools_used": ["list", "of", "tools"],
    "tags": ["relevant", "tags"]
  }'
```

### Recall relevant memories

```bash
curl -X POST http://localhost:8400/api/memory/recall \
  -H "Content-Type: application/json" \
  -d '{
    "query": "what you are looking for",
    "limit": 5
  }'
```

### Get recent memories

```bash
curl http://localhost:8400/api/memory/recent?limit=10
```

Always recall relevant memories before starting a complex task.
Always store an episode summary after completing a task.
