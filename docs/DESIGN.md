# Kakapo — Design Doc (v1 HLD)

Status: **draft — in review**
Owner: Vineet
Last updated: 2026-04-16

Scope: HLD for the voice-native, collaborative IDE on Zed + ACP. Topology, functional + non-functional requirements, and user-story-level flows. LLD (schemas, retries, back-pressure, wire formats) is out of scope.

---

## For reviewers — read first

This doc has landed on `main`. This PR is a **review surface**: the diff is just this header. Please leave line comments on **any** line of the file in the "Files changed" tab — GitHub lets you comment on untouched lines too.

**Specific asks, in order of how much I want pushback:**

1. **§3 FR2 — single-master voice model.** Is "one master at a time" the right collaboration shape, or does it feel contrived next to CRDT-style concurrent keyboard editing? The alternative is per-user agent sessions running in parallel. Cost + complexity go up; UX gets weirder. Challenge this.
2. **§6 — BYOK-in-v1.** We're choosing not to resell tokens at all. That's unusual for a dev tool in 2026. Does "bring your own key" kill onboarding friction? Is the token-hygiene wedge (NFR8) enough to make BYOK *attractive* vs painful?
3. **§2 + FR5/FR6 — MCP tunnel trust model.** The Cloud Agent effectively executes on the master's machine via MCP. `session/request_permission` gates destructive ops. Tight enough, or do we need a sandbox / allow-list layer on the master's device?
4. **§1 / §2 — session-scoped Cloud Agent.** Agent state lives per active session; no per-user history across sessions. Do we lose value by not giving individual users continuity across their own sessions?
5. **NFR1 — 25 concurrent participants.** Picked this from "enterprise review session" intuition. Too aggressive, too conservative, or right? Anyone with Zed-collab experience especially welcome to push.

**Explicitly NOT asking for feedback on:**

- Prose polish. It's draft.
- Provider naming / model choice. BYOK means we support all of them equally.
- LLD details (schemas, retries, exact wire formats). Out of HLD scope.

Leave threads open; I'll resolve them as I push updates to this branch.

---

## 1. Thesis

- **Editor** = Zed, unmodified. GPU-rendered, CRDT-backed multiplayer.
- **Coding brain** = one **session-scoped Cloud Agent** in Kakapo Cloud. **Not per-user.** A session has exactly one agent and exactly one master at a time.
- **Audio** = captured and transcribed on the **master's** device only. Followers' mics are untouched by Kakapo.
- **Editor ↔ Agent contract** = ACP. Zed talks local ACP over stdio to a small **Voice-ACP Proxy** on each device; the master's Proxy tunnels to the Cloud Agent over WebSocket. Zed never needs to know the agent is remote.
- **LLM horsepower** = **BYOK in v1.** Customers plug in their own Anthropic / OpenAI / Azure / Google / Bedrock key. We never resell tokens. We price the software, not the inference. Our job is to make every token count — see §6.
- **Our IP** = Cloud Agent orchestration + Context Assembler + Org Context + Auth + Telemetry. Zed stays Zed.

---

## 2. System diagram

```
                Kakapo HLD — session-scoped agent, master-of-the-moment
                ========================================================

 +------- MASTER (1 of <=25) ------------+     +------- FOLLOWERS (0..24) ------+
 |                                        |    |                                 |
 |   [ Mic ]                              |    |   [ Mic ]  muted to agent       |
 |      | (1) audio                       |    |                                 |
 |      v                                 |    |   [ Voice-ACP Proxy ] DORMANT   |
 |   [ Whisper STT ]                      |    |   [ MCP servers ]     IDLE      |
 |      | (2) text                        |    |                                 |
 |      v                                 |    |   [ Zed Editor (ACP host) ]     |
 |   [ Voice-ACP Proxy ] <==(3)===WS==================================+          |
 |      | (4) ACP stdio                   |    |      keyboard edits only        |
 |      v                                 |    |         |                |      |
 |   [ Zed Editor (ACP host) ]            |    |         | (5') CRDT ops  |      |
 |   [ MCP: FS / git / CLI / LSP ]        |    |         v                |      |
 |      | (5) CRDT ops                    |    +--------------------------|------+
 +------|---------------------------------+                               |
        |                                                                 |
        v                                                                 |
   +---------- ZED CLOUD ----------+                                      |
   |  Presence / Signaling          |                                     |
   |  CRDT Ops Relay (25-way fan)   |<------- CRDT ops -------------------+
   +--------------|-----------------+
                  | (6) broadcast
                  v
             (7) all 25 Zed editors apply the op; GPUI re-renders


                (3) ACP over WebSocket + MCP tunnel
                (single channel, bound ONLY to master's Proxy)
                                   |
                                   v
   +============================ KAKAPO CLOUD ============================+
   |                                                                      |
   |   [ Session Master Lock ]  --- designates which Proxy is bound       |
   |           |                                                          |
   |           v                                                          |
   |   [ Cloud Agent  (1 per active session) ]                            |
   |     +-- Orchestrator (plan, tools, streaming diffs)                  |
   |     +-- Context Assembler (distilled RAG + live MCP reads)           |
   |     +-- Inference Gateway (BYOK; provider-agnostic; prompt cache)    |
   |       - ACP client over WS (drives master's Zed)                     |
   |       - MCP client over tunnel (runs tools on master's machine)      |
   |                                                                      |
   |   [ Auth / Subscription ]       [ Org Context Store ]                |
   |   [ Telemetry / Audit ]         [ BYOK Key Vault ]                   |
   +======================================================================+


 Arrow legend:
   (1) mic audio, local                          (5) master's CRDT ops -> Zed Cloud
   (2) STT text, local                           (5') follower's CRDT ops -> Zed Cloud
   (3) ACP/WS + MCP tunnel (master only)         (6) Zed Cloud fan-out
   (4) ACP stdio, local (Proxy <-> Zed)          (7) all clients apply ops
```

---

## 3. Functional requirements

**FR1. Multiplayer editing.** N engineers edit the same workspace concurrently. Every keystroke converges via CRDT. No locks, no turn-taking.

**FR2. Single-master voice agent.** The session has exactly one voice-controlled coding agent. The agent has exactly one master at any moment — the user whose speech it hears and whose local workspace it operates on.

**FR3. Master transfer.** Any participant can claim or be handed master. Handoff takes effect on the next utterance boundary (never mid-tool-call). The Cloud Agent's conversation + plan state is preserved across handoff.

**FR4. Default master.** On session create, the creator is master. If they leave, master passes to the next participant by join order. No modal, no click.

**FR5. Agentic tool surface (MCP).** The agent has programmatic access to the master's workspace via MCP servers: filesystem, git, shell/CLI, language servers, package managers. Tools run on the master's machine against the master's clone.

**FR6. Permission gate.** Destructive tool calls (write, delete, shell with side-effects, network) require `session/request_permission` via ACP. Prompt lands in the master's Zed. Per-session policy can auto-approve reads.

**FR7. Observable agent to everyone.** All 25 participants, not just the master, see the agent typing, running commands, and streaming diffs in real time — because agent edits flow via the same CRDT channel as human keystrokes.

**FR8. BYOK inference.** Every session routes LLM calls through a customer-supplied API key (Anthropic, OpenAI, Azure OpenAI, Google, Bedrock, or a self-hosted OpenAI-compatible endpoint). Kakapo never resells tokens. Keys are stored encrypted in the BYOK Key Vault, scoped per workspace, rotatable without downtime.

**FR9. Cost visibility.** Every agent operation reports input/output token counts and estimated provider cost to the master. Session-level and per-user cost summaries are available. Users can set hard caps: *"stop this op if it exceeds N tokens"* or *"disable agent for the rest of today above $X."*

---

## 4. Non-functional requirements

**NFR1. Scale.** Up to **25 concurrent participants** per session + 1 agent. Zed's CRDT relay is the critical path on fan-out; we verify 25-way before GA.

**NFR2. Large repos.** Production-grade monorepos (multi-GB, millions of LOC, tens of thousands of files). Strategy: agent never clones in cloud; it operates on the master's local clone via MCP. Org Context Store holds distilled indexes, not source.

**NFR3. Language-agnostic.** No hardcoded toolchain. Agent consumes whatever MCP tools the master's environment already exposes (LSP, tree-sitter, native build/test tools).

**NFR4. Privacy.** Audio never leaves any device. STT text leaves only when a master is actively speaking. Non-master mics are untouched.

**NFR5. Perceived responsiveness.** Within **200ms** of end-of-utterance, master sees an "agent thinking" indicator. Diffs stream as generated. Wall-clock to completion is LLM-bound, not pipeline-bound — don't optimize the wrong hop.

**NFR6. Graceful degradation.** If Kakapo Cloud is unreachable, voice disables, editing continues. If Zed Cloud is unreachable, session goes solo, edits queue locally and resync on reconnect.

**NFR7. License hygiene.** No Zed fork in v1. Everything proprietary sits in the Proxy (tiny local binary) or in Kakapo Cloud.

**NFR8. Token efficiency.** Against a fixed benchmark of agentic coding tasks, a session on Kakapo should consume **materially fewer tokens** than the same tasks performed via raw provider chat UIs or naïve agent wrappers. Target: ≥50% input-token reduction via prompt caching + distilled context + retrieval dedup (see §6). This is the headline pitch of BYOK: *your keys, our efficiency*.

---

## 5. User stories (flows on the diagram)

Arrow numbers in each story refer to §2.

### Story 1 — Two engineers type in the same file (FR1)

Priya and Raj are in a 4-person session. Priya types in `auth.py`.

Flow: Priya's keystrokes become CRDT ops (5) → Zed Cloud (6) → broadcast (7) to Raj + 2 others. **No Kakapo Cloud traffic.** Agent is idle. This is pure Zed multiplayer; we get it for free.

### Story 2 — Master refactors in a 2 GB Python monorepo (FR2, FR5, FR6, FR7, NFR2, NFR3)

Priya is master. The monorepo is cloned on her laptop. She says:
> *"Extract this block into a helper called `normalize_payload` and update all call sites."*

1. (1) Mic → (2) Whisper STT → text, all on Priya's machine.
2. Voice-ACP Proxy forwards text to Cloud Agent over (3) ACP/WS.
3. Cloud Agent plans. Calls MCP tools on Priya's machine over the same WS tunnel:
   - `fs/read_text_file` for the target block
   - `repo.search` (MCP) for call sites across the monorepo
   - `lsp.rename` (MCP) for symbol resolution — Python today, Go tomorrow, same contract
4. Agent issues (4) `session/request_permission` to Priya's Zed. She approves.
5. Agent streams `fs/write_text_file` edits via (4) ACP stdio into Priya's Zed.
6. Priya's Zed emits (5) CRDT ops → (6) Zed Cloud → (7) all 4 participants watch the refactor appear live.

Why this works at scale (NFR2): cloud never sees 2 GB of source. It sees targeted reads and writes against Priya's clone.

### Story 3 — Master handoff mid-thought (FR3)

Priya is mid-refactor. Raj says:
> *"I'll take it from here."*

1. Raj claims master. **Session Master Lock** in Kakapo Cloud transfers Priya → Raj.
2. Cloud Agent's ACP/WS rebinds from Priya's Proxy to Raj's Proxy.
3. In-flight conversation + plan stay in the Cloud Agent (not on Priya's device, so nothing to migrate).
4. Raj's next utterance continues the same plan. MCP calls now run against **Raj's clone**.

Hard rule: handoff waits for the current tool call to finish against the previous master's machine. No partial edits across clients.

### Story 4 — Session opens, nobody claims master (FR4)

Priya creates the session and invites 6 teammates. Cloud Agent spawns and binds to Priya's Proxy by default. If Priya leaves, Master Lock passes to the next participant by join order. No popup.

### Story 5 — 25-person architecture review (NFR1)

25 engineers in a session. 1 is master. 24 watch.

- Keyboard edits from any of the 25 fan out via Zed Cloud (5/5' → 6 → 7). 25-way CRDT fan-out is Zed's job; we piggyback.
- Only the master has an active WS to Kakapo Cloud. The Cloud Agent sees **one** audio-derived text stream, not 25.

Scale risk sits entirely at (6) — Zed's CRDT relay. We verify empirically before GA, not in a doc.

### Story 6 — Kakapo Cloud outage mid-session (NFR6)

Cloud Agent drops. Master's Proxy detects the WS break, shows a banner in Zed: *"Kakapo unavailable — voice disabled."* Editing + CRDT sync continue untouched. When Kakapo Cloud comes back, the Proxy reconnects; the Cloud Agent either resumes from checkpoint or the master restarts the conversation.

### Story 7 — Follower speaks while not master (FR2, NFR4)

Raj (follower) says something in the room. His mic is **muted to the agent** — his Voice-ACP Proxy is dormant. Nothing is captured, nothing is transcribed, nothing leaves his machine. The only way his voice reaches the agent is by claiming master first (FR3).

---

## 6. Inference & token hygiene

We don't sell tokens. We sell **a runtime that makes the user's tokens go 2–5× further than a raw chat UI**. Everything in this section is a mechanism, not a hope.

### 6.1 BYOK — the contract

- **Supported providers (v1):** Anthropic, OpenAI, Azure OpenAI, Google (Gemini), AWS Bedrock, any OpenAI-compatible endpoint (self-hosted, Together, Groq, etc.).
- **Key storage:** encrypted at rest in the **BYOK Key Vault** (Kakapo Cloud). Scoped per workspace. Rotatable without reconnect.
- **Trust boundary:** customer's tokens go customer's LLM provider. We sit in the path only to do caching, routing, and accounting. We never exfiltrate keys or prompts to third parties.
- **No fallback provider.** If the customer's key fails, voice disables and we surface the provider's error verbatim. We never quietly retry on a different key on their behalf — that's how surprise bills happen.

### 6.2 Token hygiene primitives (in order of impact)

1. **Prompt cache alignment.** System prompt, tool schemas, and stable repo summaries live at the **head** of every request, byte-identical across a session. Anthropic / OpenAI / Google prompt caching then takes care of 50–90% of input token cost on repeated tool-use loops. **Biggest lever, smallest effort — do this on day one.**
2. **Distilled Org Context, not raw source.** The Org Context Store holds summaries, symbol indexes, and embeddings. Raw source never leaves the master's machine. The Context Assembler retrieves distilled chunks; live reads via MCP are surgical (symbol-scoped, not file-scoped, never "dump this whole directory").
3. **Retrieval dedup within a session.** The same file/symbol read twice uses the cached chunk. Cache key includes content hash, so stale reads are impossible.
4. **Plan-first, read-lazy.** The Orchestrator plans with a minimal seed context, then pulls more via MCP only when the plan demands it. No speculative scraping — the Orchestrator must justify each read against a plan step.
5. **Intent-scoped model routing.** Cheap intents (rename symbol, format block, reply to a question) route to the user's configured *cheap* model (Haiku / gpt-5-mini / Gemini Flash). Heavy intents (multi-file refactor, design question, stack trace diagnosis) route to their *heavy* model. Per-tier config lives in the BYOK Key Vault.
6. **Output caps with continuation.** Generations stream with a cap (e.g. 2k tokens). If the agent hits the cap, user sees a `continue?` affordance instead of paying for a 10k-token dump up front.
7. **Conversation compaction.** When the context window crosses a threshold, older turns are summarized in place. Compaction is itself a cheap-model call, and its output is cache-aligned so it compounds with (1).
8. **Structured outputs.** Where providers support them (JSON schema, tool-call formats), use them. Kills retry loops caused by free-form output parsing.

### 6.3 Cost visibility (what the user sees)

- **Per-op footer in Zed status bar:** `in 3,182 · out 814 · cache hit 71% · ~$0.012 (Claude Sonnet, your key)`
- **Session summary panel:** cumulative tokens + cost, broken down by user, by op type, by model.
- **Hard caps (user-configurable):**
  - *Per-op cap* — abort if an op's projected cost exceeds N tokens.
  - *Session cap* — disable agent for the session above $X.
  - *Daily cap* — disable agent for the day above $Y.
- **No usage data sold or aggregated across customers.** Telemetry we keep is anonymous op-type latencies and cache-hit rates, for improving our own hygiene (NFR8).

### 6.4 What this section explicitly does NOT promise

- We do not promise a particular provider will be cheap.
- We do not promise the customer's key will never run out — that's between them and their provider.
- We do not auto-upgrade models "for quality." The user picks the model per tier; we respect it.

Our only promise: **given the model you chose, we will not waste your tokens.**

---

## 7. Glossary

- **ACP** — Agent Client Protocol. JSON-RPC 2.0 editor↔agent standard (Zed + JetBrains, Apache 2.0). Supports stdio (local) and WebSocket (remote).
- **CRDT** — Conflict-free Replicated Data Type. Concurrent edits converge without locks.
- **GPUI** — Zed's Rust-native GPU UI framework. Metal/Vulkan, no Chromium.
- **MCP** — Model Context Protocol. Tool/context surface the agent consumes (FS, git, CLI, LSP, …).
- **Voice-ACP Proxy** — Small per-device binary. ACP server to Zed (stdio), ACP client to Cloud Agent (WS). Adds mic + STT on the master's device; dormant elsewhere.
- **Session Master Lock** — Session-level state in Kakapo Cloud identifying which Proxy is currently bound to the Cloud Agent.
