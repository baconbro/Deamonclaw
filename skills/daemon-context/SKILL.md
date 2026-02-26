---
name: daemon-context
description: >
  The DAEMON sidecar provides environmental context from audio, video,
  screen monitoring, and structured memory. When you see <daemon_context>
  tags in the conversation, use this information to inform your responses.
  This is real-time awareness of the user's environment.
priority: 99
---

## How DAEMON Context Works

The DAEMON perception system monitors the user's environment and injects
context into your conversations. This context appears as:

```
<daemon_context>
<audio_recent>Last 5 minutes of conversation transcription</audio_recent>
<screen_context>Current active application and content</screen_context>
<relevant_memories>Similar past experiences</relevant_memories>
<active_goals>Current goal priorities</active_goals>
<pending_actions>Actions awaiting approval</pending_actions>
</daemon_context>
```

Use this context naturally. Don't mention "DAEMON" or the perception system
to the user — just be informed by it, like a colleague who's been in the
room and remembers past conversations.
