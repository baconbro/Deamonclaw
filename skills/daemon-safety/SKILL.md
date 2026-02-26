---
name: daemon-safety
description: >
  Permission and safety check system. CRITICAL: Before executing any
  action that sends emails, deletes files, makes purchases, posts publicly,
  or modifies external systems, you MUST check permissions through this skill.
triggers:
  - always (pre-action check)
priority: 100
---

## MANDATORY: Pre-Action Permission Check

Before ANY external action, check permission:

```bash
curl -X POST http://localhost:8400/api/safety/check \
  -H "Content-Type: application/json" \
  -d '{
    "action": "description of what you want to do",
    "tool": "tool_name",
    "parameters": {},
    "side_effects": ["email:send", "file:delete", "http:post"]
  }'
```

Response will be one of:

- `{"approved": true, "tier": 0}` → Proceed
- `{"approved": true, "tier": 1}` → Proceed, action logged
- `{"approved": false, "tier": 2, "reason": "requires_human_approval"}` → STOP.
  Tell the user what you want to do and wait for explicit approval.
- `{"approved": false, "tier": 3, "reason": "destructive_action"}` → STOP.
  Tell the user what you want to do, explain the risks, wait for approval.

### Log completed actions

After any external action completes:

```bash
curl -X POST http://localhost:8400/api/safety/log \
  -H "Content-Type: application/json" \
  -d '{
    "action": "what was done",
    "tool": "tool_name",
    "result": "outcome",
    "reversible": true
  }'
```

## NEVER skip the permission check. This is non-negotiable.
