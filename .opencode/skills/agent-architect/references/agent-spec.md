# OpenCode Agent Specification

Complete reference for agent configuration options.

## File Location

| Scope | Path |
|-------|------|
| Project | `.opencode/agents/<name>.md` |
| Global | `~/.config/opencode/agents/<name>.md` |

File name becomes agent name (e.g., `review.md` → `review` agent).

## Frontmatter Options

### Required

| Field | Type | Description |
|-------|------|-------------|
| `description` | string | What the agent does and when to use it. **Critical for auto-delegation.** |

### Mode

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | string | `all` | `primary`, `subagent`, or `all` |

- **primary**: Main agents, switch with Tab key
- **subagent**: Invoked by primary agents or via `@mention`
- **all**: Can be used as both

### Model & Temperature

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | inherited | Format: `provider/model-id` |
| `temperature` | number | model default | 0.0-1.0, lower = more deterministic |

**Temperature guidelines**:
- `0.0-0.2`: Code analysis, planning (focused)
- `0.3-0.5`: General development (balanced)
- `0.6-1.0`: Brainstorming, exploration (creative)

**Model examples**:
```yaml
model: anthropic/claude-sonnet-4-20250514
model: anthropic/claude-haiku-4-20250514
model: openai/gpt-4o
model: opencode/gpt-5.1-codex  # OpenCode Zen
```

### Execution Control

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `maxSteps` | number | unlimited | Max agentic iterations before forced text response |
| `disable` | boolean | false | Disable the agent |
| `hidden` | boolean | false | Hide from `@` autocomplete (subagents only) |

### Tools

Control which tools are available:

```yaml
tools:
  write: true
  edit: true
  bash: true
  read: true
  grep: true
  glob: true
  list: true
  patch: true
  skill: true
  todowrite: true
  todoread: true
  webfetch: true
  question: true
  task: true
  lsp: true  # experimental
```

**Wildcards** for MCP tools:
```yaml
tools:
  mymcp_*: false  # disable all tools from mymcp server
```

**Important**: If `tools` is omitted, agent inherits ALL available tools.

### Permissions

Fine-grained control over tool behavior:

```yaml
permission:
  edit: allow    # allow | ask | deny
  bash: ask
  webfetch: deny
```

**Granular bash permissions**:
```yaml
permission:
  bash:
    "*": deny                    # default: deny not listed
    "git status *": allow       # allow git status
    "git diff *": allow         # allow git diff
    "git push *": deny          # deny git push
    "npm test *": allow         # allow npm test
    "rm *": deny                # deny rm commands
```

**Rules evaluated in order, last match wins.**

### Task Permissions

Control which subagents can be invoked via Task tool:

```yaml
permission:
  task:
    "*": deny                   # deny all by default
    "code-reviewer": allow      # allow specific agent
    "internal-*": ask           # ask for internal agents
```

### Prompt

Custom system prompt from file:

```yaml
prompt: "{file:./prompts/review.txt}"
```

Path relative to config file location.

### Additional Provider Options

Pass-through options to provider:

```yaml
# OpenAI reasoning models
reasoningEffort: high
textVerbosity: low
```

## Body (System Prompt)

Everything after frontmatter becomes the agent's system prompt.

### Recommended Structure

```markdown
---
description: ...
mode: subagent
tools: ...
---

# Role
What the agent does and its expertise.

# Process
Step-by-step workflow:
1. First step
2. Second step
3. ...

# HITL Rules
When to stop and ask:
- Condition 1 → action
- Condition 2 → action

# Definition of Done
Completion checklist:
- [ ] Criterion 1
- [ ] Criterion 2

# Output Format
How to structure responses.
```

## Built-in Agents

| Agent | Mode | Description |
|-------|------|-------------|
| `build` | primary | Default agent with all tools enabled |
| `plan` | primary | Analysis without changes (edit/bash = ask) |
| `general` | subagent | Research and multi-step tasks |
| `explore` | subagent | Fast codebase exploration |

## Available Tools Reference

| Tool | Description | Permission Key |
|------|-------------|----------------|
| `read` | Read file contents | `read` |
| `edit` | Modify existing files | `edit` |
| `write` | Create/overwrite files | `edit` |
| `patch` | Apply patches | `edit` |
| `bash` | Execute shell commands | `bash` |
| `grep` | Search file contents (regex) | `grep` |
| `glob` | Find files by pattern | `glob` |
| `list` | List directory contents | `list` |
| `webfetch` | Fetch URL content | `webfetch` |
| `task` | Launch subagents | `task` |
| `skill` | Load skills | `skill` |
| `question` | Ask user questions | `question` |
| `todowrite` | Manage todo lists | `todowrite` |
| `todoread` | Read todo lists | `todoread` |
| `lsp` | LSP queries (experimental) | `lsp` |
