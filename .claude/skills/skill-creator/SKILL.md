---
name: skill-creator
description: "Create new Claude Code skills with quality gates. Triggers: create skill, new skill, add skill, skill template"
---

# Skill Creator

Create new Claude Code skills for the Cosmaneura project.

## When to Activate

- Creating a new skill for this repo
- Reviewing skill structure and quality
- Updating existing skills
- Deciding between skill vs other documentation

---

## Decision Matrix: Should This Be a Skill?

| Scenario | Create Skill? | Alternative |
|----------|---------------|-------------|
| Repeated task with specific workflow | YES | — |
| Domain knowledge needed for implementation | YES | — |
| Security/compliance requirements | YES | — |
| One-off task | NO | Just do it |
| General reference info | NO | Add to CLAUDE.md |
| Project-specific config | NO | Add to README |

**Skill Purpose Gate**: A skill MUST teach Claude something it doesn't already know or enforce project-specific constraints.

---

## Quality Gates

Before finalizing any skill, verify ALL gates pass:

| Gate | Check | Status |
|------|-------|--------|
| **Purpose** | Does this teach something Claude doesn't know? | REQUIRED |
| **Activation** | Are trigger conditions specific and testable? | REQUIRED |
| **Density** | Imperative words (MUST/REQUIRED/FORBIDDEN) ≥5%? | REQUIRED |
| **Concision** | Total lines ≤250? | REQUIRED |
| **Tables** | Core guidance in tables, not prose? | REQUIRED |
| **Anti-patterns** | Includes common mistakes to avoid? | REQUIRED |
| **Frontmatter** | Has name + description with triggers? | REQUIRED |
| **Testable** | Can you verify if skill was followed? | REQUIRED |

---

## Skill Structure Template

```markdown
---
name: skill-name
description: "Action verb + outcome. Triggers: keyword1, keyword2, keyword3"
---

# Skill Title

## When to Activate

- Specific trigger condition 1
- Specific trigger condition 2

## Global Invariants

| Rule | Enforcement |
|------|-------------|
| Critical rule 1 | REQUIRED |
| Critical rule 2 | FORBIDDEN |

## Core Content

[Tables and concise guidance - NO verbose prose]

## Anti-Patterns

| Pattern | Status | Reason |
|---------|--------|--------|
| Bad practice 1 | FORBIDDEN | Why it fails |
```

---

## Frontmatter Specification

```yaml
---
name: kebab-case-name        # REQUIRED: matches directory name
description: "..."           # REQUIRED: format below
---
```

**Description Format:**
```
"{Action verb} {outcome}. Triggers: {keyword1}, {keyword2}, {keyword3}"
```

| Component | Rule | Example |
|-----------|------|---------|
| Action verb | Present tense, imperative | Review, Create, Validate |
| Outcome | What gets accomplished | code for security issues |
| Triggers | 2-5 activation keywords | security, review, audit |

---

## File Organization

| Type | Location | Example |
|------|----------|---------|
| Simple skill | `.claude/skills/{name}.md` | `code-quality.md` |
| Complex skill | `.claude/skills/{name}/SKILL.md` | `security-review/SKILL.md` |
| References | `.claude/skills/{name}/references/` | `references/patterns.md` |

**When to use directory structure:**
- Skill needs reference files
- Skill exceeds 150 lines
- Skill has multiple related artifacts

---

## Content Guidelines

### Imperative Density

Skills must be directive. Target ≥5% imperative words:

| Word | Usage |
|------|-------|
| **MUST** / **REQUIRED** | Mandatory action |
| **MUST NOT** / **FORBIDDEN** | Prohibited action |
| **SHOULD** | Recommended (use sparingly) |
| **NEVER** | Absolute prohibition |
| **ALWAYS** | Unconditional requirement |

### Table-First Design

| Content Type | Format | Why |
|--------------|--------|-----|
| Rules | Table | Scannable, enforceable |
| Comparisons | Table | Clear contrast |
| Steps | Numbered list | Sequential |
| Examples | Code blocks | Copyable |
| Explanations | Brief prose | Context only |

---

## Validation Checklist

Before committing a new skill:

- [ ] Passes all 8 quality gates
- [ ] Frontmatter has name + description + triggers
- [ ] "When to Activate" section present
- [ ] "Anti-Patterns" section present
- [ ] Tables used for rules and comparisons
- [ ] Line count ≤250
- [ ] Added to CLAUDE.md skill table

---

## Anti-Patterns

| Pattern | Status | Instead |
|---------|--------|---------|
| Verbose explanations | FORBIDDEN | Use tables |
| Missing triggers | FORBIDDEN | Add 2-5 keywords |
| No anti-patterns section | FORBIDDEN | Add common mistakes |
| Prose over tables | FORBIDDEN | Convert to table |
| Skills >250 lines | FORBIDDEN | Split or use references/ |
| Teaching Claude basics | FORBIDDEN | Focus on project-specific |
| Vague activation | FORBIDDEN | Make testable |
