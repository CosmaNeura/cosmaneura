# Claude Code Security Tools

> AI-powered security review and code quality tools for Cosmaneura

This directory contains Claude Code skills for security analysis, audit preparation, and code quality enforcement tailored to the Cosmaneura healthcare application stack (FastAPI, React, PostgreSQL, Clerk, HIPAA).

---

## Quick Start

### Run a Security Review

```
/security-review
```

Or ask Claude:
- "Review this code for security issues"
- "Check the patient routes for vulnerabilities"
- "Security audit the authentication flow"

### Run Audit Preparation

```
/audit-prep
```

Or ask Claude:
- "Prepare for security audit"
- "Run the HIPAA compliance checklist"
- "Check audit readiness"

### Check Code Quality

Ask Claude:
- "Review this for code quality"
- "Refactor this function"
- "Is this code minimal enough?"

---

## Available Skills

| Skill | Command | Purpose |
|-------|---------|---------|
| Security Review | `/security-review` | Find vulnerabilities in code |
| Audit Prep | `/audit-prep` | Pre-audit readiness checklist |
| Code Quality | (auto-activates) | Enforce minimal code philosophy |
| Skill Creator | `/skill-creator` | Create new skills |

---

## Security Review

### What It Does

Analyzes code for security vulnerabilities with severity ratings:

| Severity | Examples |
|----------|----------|
| **P0 Critical** | Secrets exposure, SQL injection, auth bypass, HIPAA violations |
| **P1 High** | SSRF, insecure deserialization, missing input validation |
| **P2 Medium** | Missing rate limiting, logging issues, race conditions |

### How to Use

**Review uncommitted changes:**
```
/security-review
```

**Review specific files:**
```
/security-review app/routes/internal/patients.py
```

**Review a directory:**
```
/security-review app/routes/
```

**Ask naturally:**
```
"Check the patient API for security issues"
"Review authentication for vulnerabilities"
"Is this endpoint secure?"
```

### Output Format

The skill produces a structured report:

```markdown
## Security Review Summary

**Scope**: app/routes/internal/patients.py
**Findings**: 1 Critical, 2 High, 1 Medium

### Key Risks
- PHI exposed in error messages
- Missing authorization check on patient access

### Findings

#### [P0 CRITICAL] PHI in Error Response
**Location**: `app/routes/internal/patients.py:L42`
**Description**: Patient name included in 404 error message...
**Remediation**: Return generic error without PHI...

### Positive Observations
- Clerk authentication properly configured
- SQLModel used throughout (no SQL injection)

### Recommendations
- Add integration tests for authorization
- Implement centralized error handling
```

### Vulnerability Categories

**Python/FastAPI:**
- Secrets in code or logs
- Missing auth middleware
- SQL injection patterns
- PHI/HIPAA violations
- Unsafe error handling
- Celery task vulnerabilities

**TypeScript/React:**
- Secrets in frontend bundle
- XSS (dangerouslySetInnerHTML, eval)
- Insecure storage (localStorage)
- Missing Clerk guards

**Clerk Authentication:**
- Routes without auth
- Session handling issues
- RBAC bypass

---

## Audit Preparation

### What It Does

Runs a comprehensive 10-category checklist to assess security audit readiness:

1. Build & Dependencies
2. Secrets Management
3. Authentication (Clerk)
4. Authorization
5. Input Validation
6. API Security
7. HIPAA Compliance
8. Error Handling & Logging
9. Testing
10. Documentation

### How to Use

**Run full assessment:**
```
/audit-prep
```

**Ask naturally:**
```
"Are we ready for a security audit?"
"Run the HIPAA compliance checklist"
"Check our security posture"
```

### Output Format

```markdown
# Audit Readiness Report

**Project**: Cosmaneura
**Date**: 2024-03-04
**Commit**: abc123

## Summary

| Category | Pass | Warn | Fail | Skip |
|----------|------|------|------|------|
| Build & Dependencies | 5 | 1 | 0 | 0 |
| Secrets Management | 4 | 0 | 1 | 0 |
| Authentication | 6 | 0 | 0 | 0 |
| ...

## Overall Readiness: NEARLY READY

## Critical Issues (must fix)
1. [FAIL] Hardcoded test secret in app/config.py

## Recommended Improvements
1. [WARN] Add rate limiting to /api/auth endpoints
```

### Checklist Categories

Each category has specific checks that produce PASS/WARN/FAIL/SKIP:

**HIPAA Compliance (Category 7):**
- PHI de-identified before AI processing
- No PHI in application logs
- All PHI access is audit logged
- PHI encrypted at rest
- Session timeout configured
- No PHI in localStorage
- BAA in place

---

## Code Quality

### What It Does

Enforces minimal code philosophy with measurable constraints:

| Metric | Soft Limit | Hard Limit |
|--------|------------|------------|
| Function lines | 40 | 60 |
| File lines | 300 | 600 |
| Parameters | 3 | 4 |
| Nesting depth | 3 | 4 |

### Activation

Auto-activates when you:
- Write new code
- Ask for refactoring
- Request code improvements
- Mention "clean", "minimal", "simplify"

### Key Principles

1. **Least code wins** — fewer lines is better
2. **No premature abstraction** — wait for rule of 3
3. **Delete, don't comment** — git has history
4. **YAGNI** — build what's requested, nothing more

---

## Skill Creator

### What It Does

Helps you create new Claude Code skills with quality gates.

### How to Use

```
/skill-creator
```

Or ask:
```
"Create a new skill for database migrations"
"Add a skill for API documentation"
```

### Quality Gates

New skills must pass:

| Gate | Requirement |
|------|-------------|
| Purpose | Teaches something Claude doesn't know |
| Activation | Specific, testable triggers |
| Density | ≥5% imperative words (MUST/FORBIDDEN) |
| Concision | ≤250 lines |
| Tables | Core guidance in tables |
| Anti-patterns | Common mistakes documented |

---

## Directory Structure

```
.claude/
├── README.md                    # This file
├── AGENTS.md                    # Agent safety boundaries
├── settings.local.json          # Permissions
├── skills/
│   ├── security-review/
│   │   └── SKILL.md             # Security vulnerability analysis
│   ├── audit-prep/
│   │   └── SKILL.md             # Audit readiness checklist
│   ├── code-quality/
│   │   └── SKILL.md             # Minimal code philosophy
│   └── skill-creator/
│       └── SKILL.md             # Create new skills
└── knowledgebase/
    └── vulnerabilities.md       # Vulnerability patterns reference
```

---

## Knowledgebase

The `knowledgebase/` directory contains reference materials loaded during reviews:

### vulnerabilities.md

10 vulnerability classes with detection patterns:

1. **Secrets Exposure** — hardcoded credentials, secrets in logs
2. **HIPAA Violations** — PHI in logs, missing de-identification
3. **Authentication Bypass** — missing middleware, client-only checks
4. **Authorization Bypass (IDOR)** — missing ownership verification
5. **SQL Injection** — string interpolation in queries
6. **Cross-Site Scripting (XSS)** — dangerouslySetInnerHTML, eval
7. **Celery Task Vulnerabilities** — untrusted data, PHI in results
8. **Unsafe Error Handling** — bare except, stack traces to client
9. **Missing Rate Limiting** — unprotected auth endpoints
10. **Insecure Storage** — tokens/PHI in localStorage

Each class includes:
- Risk description
- Vulnerable code examples
- Safe code examples
- Detection search commands

---

## Integration with Project

### CLAUDE.md

The root `CLAUDE.md` file provides project context and points agents to these skills. It includes:

- Project architecture
- Technology stack
- Security-first development standards
- Skill reference table

### AGENTS.md

The `.claude/AGENTS.md` file defines:

- Safety boundaries (safe/ask first/never)
- Verification matrix (what to run before completing tasks)
- Directory map
- Non-negotiable invariants (HIPAA, security, code quality)

---

## Extending the Tools

### Adding a New Skill

1. Create directory: `.claude/skills/{skill-name}/`
2. Create `SKILL.md` with frontmatter:
   ```yaml
   ---
   name: skill-name
   description: "Action + outcome. Triggers: keyword1, keyword2"
   ---
   ```
3. Include sections: When to Activate, Core Content, Anti-Patterns
4. Add to `CLAUDE.md` skill table
5. Verify quality gates pass

### Adding to Knowledgebase

Add new `.md` files to `.claude/knowledgebase/` for:
- Additional vulnerability patterns
- Compliance requirements
- Domain-specific security guidance

Reference them in skill files:
```markdown
Read `.claude/knowledgebase/new-patterns.md` for additional context.
```

---

## Examples

### Example 1: Pre-PR Security Check

Before opening a PR:
```
Review my changes for security issues
```

Claude will:
1. Run `git diff HEAD`
2. Analyze changes against vulnerability patterns
3. Report findings with severity and remediation

### Example 2: HIPAA Compliance Audit

Preparing for compliance review:
```
/audit-prep
```

Claude will:
1. Discover project scope
2. Run all 10 category checklists
3. Generate readiness report with PASS/WARN/FAIL
4. Prioritize remediation items

### Example 3: Secure a New Endpoint

When adding a patient endpoint:
```
I'm adding a new endpoint to fetch patient records. Review it for security.
```

Claude will check:
- Clerk auth middleware present
- Authorization (ownership) verified
- Input validation with Pydantic
- No PHI in logs or errors
- Error handling without exposure

---

## Best Practices

1. **Run security review before every PR** — catch issues early
2. **Run audit-prep monthly** — track security posture over time
3. **Load relevant skills** — mention the domain to activate knowledge
4. **Check the knowledgebase** — vulnerability patterns are searchable
5. **Extend as needed** — add skills for new security domains

---

## Troubleshooting

### Skill Not Activating

Ensure you use trigger words from the skill description:
- Security review: "security", "vulnerability", "audit", "review"
- Audit prep: "audit", "compliance", "checklist", "HIPAA"
- Code quality: "quality", "refactor", "clean", "minimal"

### Too Many False Positives

The security review includes false positive filtering. If still too noisy:
1. Provide more context about the code
2. Specify the scope more narrowly
3. Ask to filter by severity level

### Missing Context

If Claude misses project-specific patterns:
1. Add to `.claude/knowledgebase/`
2. Reference in the skill file
3. Mention the context in your prompt
