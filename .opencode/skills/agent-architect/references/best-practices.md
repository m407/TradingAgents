# Agent Design Best Practices

Proven patterns for creating effective OpenCode agents.

## Single-Responsibility Principle

**Each agent = one clear goal.**

| Good | Bad |
|------|-----|
| "Reviews code for security vulnerabilities" | "Reviews code, writes tests, deploys" |
| "Extracts Mermaid diagrams from markdown" | "Processes all documentation" |
| "Validates pipeline outputs" | "Runs entire pipeline" |

**Benefits**:
- Easier to test and debug
- Clearer tool scoping
- Better auto-delegation by description

## Permission Hygiene

**Always explicitly list tools.** If omitted, agent inherits ALL tools.

### Read-Heavy Agents (Analyzer, Reviewer, Planner)

```yaml
tools:
  read: true
  grep: true
  glob: true
  webfetch: true
  write: false
  edit: false
  bash: false
```

### Write-Enabled Agents (Implementer, Builder)

```yaml
tools:
  read: true
  edit: true
  write: true
  bash: true
  grep: true
  glob: true
permission:
  bash:
    "*": ask
    "git status *": allow
    "git diff *": allow
    "npm test *": allow
    "npm run build *": allow
```

### Orchestrator Agents

```yaml
tools:
  read: true
  grep: true
  glob: true
  task: true        # can invoke subagents
  write: false
  edit: false
  bash: false
permission:
  task:
    "*": allow      # can invoke any subagent
```

## HITL (Human-in-the-Loop) Rules

Embed explicit "ask first" rules in agent prompts.

### Pattern

```markdown
# HITL Rules

- If acceptance criteria are ambiguous → ask numbered questions and WAIT
- If design implies public API change → STOP and request approval
- If refactors exceed ADR scope → ask before proceeding
- If tests require changes beyond scope → ask before modifying
```

### Examples by Role

**PM/Spec Agent**:
```markdown
# HITL Rules
- If requirements are incomplete → ask clarifying questions
- If scope is unclear → propose options and wait for selection
```

**Architect Agent**:
```markdown
# HITL Rules
- If design affects public API → STOP and request approval
- If migration risk detected → document and ask before finalizing
```

**Implementer Agent**:
```markdown
# HITL Rules
- If refactors exceed scope → ask before proceeding
- If tests fail unexpectedly → report and wait for guidance
- Only mark DONE if all tests pass
```

## Definition of Done

Every agent should have completion criteria.

### Pattern

```markdown
# Definition of Done

- [ ] All target files processed
- [ ] Issues documented with severity levels
- [ ] Recommendations provided with rationale
- [ ] Summary written in specified format
- [ ] No errors in execution log
```

### Examples

**Code Reviewer**:
```markdown
# Definition of Done
- [ ] All changed files reviewed
- [ ] Security issues flagged (if any)
- [ ] Performance concerns noted (if any)
- [ ] Code style violations listed
- [ ] Summary with approve/request-changes recommendation
```

**Test Runner**:
```markdown
# Definition of Done
- [ ] All tests executed
- [ ] Failed tests documented with error messages
- [ ] Coverage report generated (if applicable)
- [ ] Summary: passed/failed/skipped counts
```

## Description Writing

The `description` field is critical for auto-delegation.

### Good Descriptions

```yaml
# Action-oriented, specific triggers
description: Reviews code for security vulnerabilities and OWASP compliance. Use after code changes or before security audits.

description: Extracts Mermaid diagrams from markdown files and saves as .mmd. Use when processing documentation with embedded diagrams.

description: Validates pipeline outputs for completeness and quality. Use as final step before delivery.
```

### Bad Descriptions

```yaml
# Too vague
description: Helps with code

# No trigger context
description: Security agent

# Too broad
description: Does everything related to testing
```

## Agent Chaining Pattern

For complex workflows, use orchestrator + specialized subagents.

### Structure

```
orchestrator (primary)
    ├── spec-writer (subagent)
    ├── architect (subagent)
    ├── implementer (subagent)
    └── validator (subagent)
```

### Orchestrator Example

```yaml
---
description: Coordinates multi-step workflows by delegating to specialized subagents
mode: primary
tools:
  read: true
  task: true
  write: false
  edit: false
  bash: false
---

# Role
Coordinate workflow phases by delegating to appropriate subagents.

# Process
1. Analyze request → determine required phases
2. Delegate to spec-writer → wait for completion
3. Delegate to architect → wait for completion
4. Delegate to implementer → wait for completion
5. Delegate to validator → report final status
```

## Temperature Guidelines

| Temperature | Use Case | Example Agents |
|-------------|----------|----------------|
| 0.0-0.2 | Deterministic, precise | Code analyzer, validator, security auditor |
| 0.3-0.5 | Balanced | General implementer, reviewer |
| 0.6-0.8 | Creative | Brainstorming, documentation writer |
| 0.9-1.0 | Highly varied | Exploration, ideation |

## Common Anti-Patterns

### 1. Tool Sprawl
**Problem**: Omitting `tools` gives agent ALL tools.
**Fix**: Always explicitly list needed tools.

### 2. Missing HITL
**Problem**: Agent makes risky changes without asking.
**Fix**: Add HITL rules for destructive operations.

### 3. Vague Description
**Problem**: Agent not auto-delegated correctly.
**Fix**: Include action verbs and trigger contexts.

### 4. No Definition of Done
**Problem**: Unclear when agent task is complete.
**Fix**: Add explicit completion checklist.

### 5. Overly Broad Scope
**Problem**: Agent tries to do too much, fails at everything.
**Fix**: Split into focused single-responsibility agents.
