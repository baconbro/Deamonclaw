---
name: daemon-perception
description: >
  Query the DAEMON perception system to understand what's happening in the
  user's environment. Use when the user asks about what they said, what's
  on screen, recent activities, or when you need environmental context.
triggers:
  - "what did I say"
  - "what's on my screen"
  - "what was that about"
  - "what happened"
  - "recent audio"
  - "recent activity"
---

## Query recent perceptions

```bash
# Get recent audio transcriptions
curl http://localhost:8400/api/perception/audio/recent?minutes=30

# Get current screen context
curl http://localhost:8400/api/perception/screen/current

# Get recent high-salience events
curl http://localhost:8400/api/perception/events?min_salience=0.5&minutes=60

# Search perceptions by content
curl -X POST http://localhost:8400/api/perception/search \
  -H "Content-Type: application/json" \
  -d '{"query": "budget discussion", "limit": 10}'
```
