# Security & Privacy

`context-mesh` stores knowledge derived from agent sessions — which can include sensitive information about a team's codebase, architecture, security posture, and decision-making. Privacy and security are first-class concerns.

This document defines the privacy model, the threat model, and the controls in place.

---

## Privacy Model: Three-Tier Scopes

Every memory carries a **scope** that determines who can see it.

### Tier 1: `private`

- Visible only to the creator (the user who authored the memory or whose session produced it).
- Stored exclusively in the local database.
- Never synced to a team hub.
- Never replicated cross-machine without explicit user action.

### Tier 2: `team`

- Visible to all members of a defined team.
- Synced to the team's mesh hub.
- Each team member's local mirror contains all team-scoped memories.
- Team membership is managed by the hub administrator.

### Tier 3: `org`

- Visible to all members of an organization (multiple teams).
- Synced to an org-level hub.
- Used for cross-team rules: company-wide security policies, organization-level standards.

Custom scope levels can be defined for finer granularity (e.g., `team:security` for security-team-only memories).

### Default Scope

Unless overridden, new memories are tagged `private`. Federation is opt-in: memories don't propagate beyond local storage until the user (or agent, with permission) marks them as `team` or higher.

---

## Threat Model

We assume the following threats and design controls accordingly:

### Threat 1: Compromised Endpoint

A developer's laptop is compromised. The attacker has filesystem access.

**Mitigation:**
- Local memory file (`memory.db`) is protected by filesystem permissions.
- Secrets in memory bodies are redacted at distillation time (see §Redaction below).
- Optional: encrypt the database at rest using SQLCipher (deferred to v1.x).

### Threat 2: Malicious Insider

A team member with hub access exfiltrates memories.

**Mitigation:**
- Audit log records every retrieval and download.
- Hub-level rate limits flag bulk exfiltration patterns.
- Sensitive memories should be scoped narrowly (e.g., `team:security` rather than `team:default`).
- Beyond this, insider threat is fundamentally a trust problem — `context-mesh` provides visibility, not prevention.

### Threat 3: Supply Chain Attack

A compromised dependency could read memories.

**Mitigation:**
- Minimize dependencies. Pin versions. Audit major updates.
- The library does not phone home — no telemetry, no analytics, no remote calls outside of explicitly-configured hub sync.
- Optional integrity verification: `context-mesh verify-deps` checks all dependencies against a known-good lockfile.

### Threat 4: Hub Server Compromise

The team mesh hub is breached.

**Mitigation:**
- Hub stores only `team` and `org` scoped memories — `private` never reaches it.
- Hub-side authentication via short-lived tokens; no plaintext password storage.
- Audit log on the hub records every access.
- All hub-client traffic is TLS-encrypted.

### Threat 5: Embedding Inversion

An attacker reconstructs sensitive content from embeddings.

**Mitigation:**
- We use a smaller embedding model (`all-MiniLM-L6-v2`, 384-dim) where inversion is empirically harder than with large transformer encoders.
- Combined with our redaction pipeline (which removes secrets BEFORE embedding), this risk is substantially reduced.
- For high-sensitivity environments, the embedding step can be moved to a local model (no cloud API).

### Threat 6: Cross-Tenant Data Leak (SaaS Future)

Not applicable to v1 (no hosted service). Documented here as a deferred concern.

---

## Redaction

Before any memory is stored, the distillation engine runs a redaction pass.

### What Gets Redacted

| Type | Detection Strategy | Replacement |
|---|---|---|
| API keys (HuggingFace, OpenAI, AWS, etc.) | Regex patterns + entropy heuristics | `[REDACTED:API_KEY]` |
| Passwords, tokens, secrets | Pattern matching + context heuristics ("password=", "token=") | `[REDACTED:SECRET]` |
| Email addresses | Standard email regex | `[REDACTED:EMAIL]` (configurable) |
| Phone numbers | Pattern matching | `[REDACTED:PHONE]` (configurable) |
| AWS account IDs | 12-digit number pattern | `[REDACTED:AWS_ID]` |
| IP addresses | IPv4/IPv6 patterns | `[REDACTED:IP]` (configurable) |
| Database connection strings | URL pattern with password | `[REDACTED:DB_URL]` |
| Private keys (PEM, SSH) | Block markers (`-----BEGIN ... PRIVATE KEY-----`) | `[REDACTED:PRIVATE_KEY]` |
| Custom patterns | User-defined regex via `redaction.toml` | Configurable |

### What Does NOT Get Redacted

- Code structure, file names, function names — these are core to memory utility.
- Repository names, branch names, commit hashes — needed for source traceability.
- Bug descriptions, design decisions — the substantive content of memory.

### Redaction Limitations

Redaction is **best-effort, not perfect**. Adversarially-crafted strings can evade pattern matching. We document this clearly so users can decide what to scope `private` vs `team`.

For high-security environments, a manual review step can be added before memories are promoted from `private` to `team`.

---

## Authentication & Authorization (Hub Mode)

### Authentication

Hub clients authenticate via tokens. Two token types:

- **User tokens** — long-lived, scoped to a user identity.
- **Session tokens** — short-lived (~1 hour), used for active sync sessions.

Tokens are stored in the system keychain when possible, never in plaintext config files.

### Authorization

Authorization is checked on every hub operation:

| Operation | Required Authorization |
|---|---|
| `pull` memories from hub | Membership in scope's group |
| `push` memories to hub | Membership in scope's group + write permission |
| Modify a memory | Author OR scope admin |
| Delete a memory | Scope admin only |
| View audit log | Scope admin only |

Authorization decisions are made at the hub. Clients receive denial errors as `403 Forbidden`.

---

## Memory Lifecycle Policies

### Decay (Auto-Expiration)

Episodic memories auto-decay after a default of 30 days. Configurable per-scope.

After decay, memories are not deleted but marked `decayed_at`. They no longer surface in default retrieval. They remain available via explicit historical queries.

### Hard Delete

Hard delete removes a memory entirely from the database (and propagates the deletion via sync).

Allowed only by:
- The memory's author.
- A scope admin.

Audit log retains a record of the deletion (with metadata, not content).

### Right To Forget

Users can request all memories they authored be hard-deleted. Standard GDPR-style flow:

```bash
context-mesh forget-me
```

This deletes all memories where the author identity matches the current user. Audit log entries referencing those memories retain only the audit metadata, not the content references.

---

## Federation Privacy

When syncing to a hub:

- **`private` memories** never leave the local store, ever.
- **`team` memories** sync only to the team hub, only between team members.
- **`org` memories** sync to the org hub, visible org-wide.

Memories are content-addressable (hashed). Identical content from two sources is deduplicated, but the audit log records both sources independently.

Cross-team boundaries are enforced server-side at the hub. Even a malicious client cannot retrieve memories scoped to a team they don't belong to.

---

## Compliance Notes

For teams with regulatory obligations:

### GDPR

- Right to access: `context-mesh export-mine` exports all memories the user authored.
- Right to forget: `context-mesh forget-me` (described above).
- Data portability: standard JSON export format.
- Lawful basis: legitimate interest for engineering knowledge sharing within a team. Document this in your team's privacy notice.

### SOC 2

- Audit log provides access traceability.
- Authentication and authorization enforce access controls.
- Self-hosted deployment keeps data within your environment.
- Encryption in transit (TLS) for all hub communication.
- Encryption at rest available via SQLCipher (v1.x).

### HIPAA / Healthcare Data

`context-mesh` is **NOT designed to store PHI**. Patterns and rules are fine; specific patient data is not. Redaction strategies should be tightened in healthcare environments to filter any health-identifying information aggressively.

---

## Default Configuration: Locked-Down

Out of the box, `context-mesh` defaults are conservative:

- Default scope: `private`.
- Federation: disabled until user runs `context-mesh connect`.
- Network access: none beyond explicit hub URL.
- Telemetry: none.
- Auto-redaction: enabled at the strictest level.

Users must explicitly opt into looser settings. This mirrors the principle: secure by default, configurable for trust.

---

## Reporting Security Issues

For vulnerability reports, see `SECURITY.md` (top-level). Use coordinated disclosure: contact the maintainer privately, do NOT open a public issue.

---

## Out Of Scope For v1

- **Encryption at rest** — deferred to v1.x via SQLCipher adapter.
- **Hardware security module integration** — for high-security environments; v2.
- **Differential privacy for cross-org pattern sharing** — required only for cross-org federation; v2.
- **Federated identity protocols** (SAML, OIDC) — single-tenant per hub in v1; multi-tenant identity is v2.
