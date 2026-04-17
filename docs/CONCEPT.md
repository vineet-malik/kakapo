# Kakapo — concept doc

Living document. Updated as we align on details.

## One-liner (draft)

**Agentic IDE companion** that is **easy to talk to** (voice-first), helping developers express intent, preserve project context, and execute work reliably.

## Vision

- **North star:** Natural, quick interaction between developer and development environment; voice is primary, not bolted on. Stretch framing: *creating the next future of human and machine interaction for building software.*
- **Product thesis (pivot):** A voice-first browser is hard to monetize for leisure use. A voice-first IDE companion solves a paid, high-friction workflow: coding, debugging, context tracking, and project handoff.
- **Steering (from project rules):** High-leverage decisions before implementation; trace real product and code flows; replies stay precise and action-oriented.

## Pivot summary (2026-04-16)

- **Hard pivot:** From browser-first product to **IDE-first product**.
- **Reason 1 (market):** “Browser you can talk to” is weak as a paid wedge for broad leisure browsing.
- **Reason 2 (value):** Talking to your IDE helps users express intent while doing real paid work; willingness to pay is higher.
- **Carry forward:** Reliability-first behavior, source/content respect, minimal mental load, and conversational correction loop remain core.

## What we know (from meeting notes)

- Browser is generally not a productivity wedge worth paying for by default users.
- IDEs fail when they lack a structured project knowledge base/context layer.
- AI without grounded project context becomes generic and unreliable.
- Original content/context should be preserved while assistance is layered on top.
- Collaboration is broken when project context cannot be handed over cleanly mid-stream.
- Development is often isolated (one dev, one environment); better shared context can reduce reporting overhead and coordination friction.

## Experience goal — control & feel

- **Bar:** **Extremely easy and intuitive to control** — the product fails if steering the session feels clever but brittle, or powerful but exhausting.
- **Honest constraint:** The ten jobs are already *possible* in a normal browser; Kakapo’s job is to make **finding and moving through the web** feel as if you have **full situational awareness** (where you’ve been, what’s open, what might matter next) **and** a **pinpoint way** to snap to the one specific thing you need — *not* “more tabs,” but **less friction between intent and the right page/paragraph/answer**.
- **Metaphor (design shorthand):** the “mystic / third eye” vibe — panoramic view of the trail + **surgical** pull toward the specific — must become **concrete UX**: voice + UI that show **where you are in the quest**, reduce backtracking, and answer **“take me to the thing that matches X”** without ceremony.

## Interaction model (draft)

- **Embodied listener:** Kakapo appears as a digital bird that visibly “listens” (looking at user, slight head tilt) when engaged.
- **Processing cue:** During first-pass reasoning, Kakapo can “look into its computer” with a small lightweight visual flash that hints at internal processing; keep it simple and non-heavy.
- **Progress micro-text style:** Pair the processing cue with tiny status text in a friendly bird voice (e.g. “checking versions...”, “finding the right trail...”), but keep it meaningful and grounded (no senseless fluff).
- **Display threshold:** Show processing status only when work lasts more than **1.0s**.
- **Focus handoff:** After intent capture, Kakapo takes user to the first relevant step and applies a soft highlight.
- **Micro-labeling:** Each highlight includes a short **3-6 word** label describing what is being pointed out.
- **Present-focus navigation:** Show only current and next options; include two alternatives for rerouting when current journey is not preferred.
- **Trust-at-a-glance metadata:** Current/next cards should include a clear source badge (e.g. Official Docs, StackOverflow, GitHub Issue).
- **Choice input style:** Accept natural-language choice phrases (e.g. “go left birdie,” “option one,” “the other one”) instead of rigid command syntax.
- **Adaptive phrasing memory (optional):** Learn per-user phrasing patterns when it helps parsing accuracy; do not force personalization when gains are negligible.
- **Choice confirmation loop:** When Kakapo infers the intended card, mark it **orange** and ask for lightweight confirmation; proceed on any affirmative/implicit assent (e.g. “yes,” “hmm”), and stop only on explicit no/correction.
- **Silence handling:** After orange preview, use a **2.0s** no-response timeout and proceed by default unless user explicitly rejects/corrects.
- **No extra acknowledgment:** If proceeding on timeout/assent, avoid additional spoken confirmations; just execute assistive next step.
- **Highlight anchor rule:** Put the relevant spot directly in view and highlight where eyes should land first (e.g. first word of relevant paragraph, or top of relevant infographic/section).
- **Anchor fallback:** If exact anchor confidence is low, highlight the closest useful section/header so user can start immediately; refine after user feedback.
- **Conversational correction loop:** User can interrupt/correct in natural speech; Kakapo re-interprets and proposes a better path.
- **Uncertainty handling:** When not fully sure, ask a short clarifying question by voice; use the user's response to choose the next best path.
- **Clarifier default format:** Ask for the missing constraint that closes the decision gap (e.g. “Give me your constraint: version/budget/goal?”).
- **Clarifier cadence:** Ask **one constraint at a time** by default. If the information gap is very large and single-question loops would be frustrating, ask a slightly longer bundled question (still concise, never essay-like).
- **Source-respecting display:** Keep original website look/content intact while guiding attention.
- **Route policy:** Default to the **most reliable** path, and among reliable options choose the **simplest wise path** (minimum complexity that still gets the job done right).
- **Conflict rule:** When reliability and speed conflict, **always choose reliability**.

## Information delivery philosophy

- **Source-first by default:** Always try to show an original internet property that best explains the situation and solution (e.g. specific StackOverflow answer, official doc section, issue comment, changelog entry).
- **Journey mode when needed:** If no single page is sufficient, stitch a **multi-page path** into the shortest reliable journey that covers required context and exact fix.
- **Output contract:** If synthesizing, keep it grounded in sources: each recommendation should map to explicit references in the stitched journey.

## Ten real-world jobs (multi-page sessions)

Illustrative situations where an **easier-to-control agentic browser** should win:

1. **Sales** — finding leads across many profiles, directories, or company pages.
2. **Software dev** — chasing a bug across docs, forums, issues, patches.
3. **Car buyer** — comparing listings until something fits budget and constraints.
4. **Traveller** — building an itinerary (flights, stays, sights) across sites.
5. **“What to watch”** — browsing reviews, trailers, availability across services.
6. **Distress / help-seeking** — high anxiety; needs trustworthy paths through many pages *(sensitivity: outcomes matter; design carefully).*
7. **Debate / argument** — sourcing counter-arguments and citations across the web.
8. **Free day planning** — “today’s plan” from scattered local/event/info sites.
9. **QA** — regression on a web UI: repeated flows across states and pages.
10. **Journalist** — research (e.g. a regional story) across news, data, official sources.

**Implication:** Persona is **not one avatar** — it’s a **pattern** (multi-hop, goal-driven web work).

## V1 wedge (decided)

- **Primary v1 job:** **QA engineer running regression flows on a web UI** — repeated, multi-step paths across pages and states; high daily load; current tooling is heavy and stressful.
- **Strategic fit:** Kakapo targets **paid, outcome-heavy work** where AI-assisted guidance is worth the cost. **Not** a head-to-head replacement for leisure browsing on a zero-marginal-cost browser like Chrome: edge-only, no-processing “just browse” competes on price users won’t pay for when they only want free net use.

## Product identity — IDE first

- **Shape of the thing:** Kakapo is **an IDE-native agentic assistant** that can support testing and other dev flows, not a niche testing suite.
- **Anti-pattern:** Building a “browser with QA extras” narrows value and weakens monetization.
- **Mitigation:** Keep QA as a strong wedge, while validating two additional developer jobs to ensure the core stays broadly useful in software work.
- **QA story detail:** Deferred while we draft without over-niching.

## Platform (v1)

- **Shape:** **Desktop application** — QA runs it on a **MacBook** (or Mac desktop). **Windows is not in the first release.**
- **Device coverage (software):** Phone/tablet/Android/iPhone targets are handled as **viewport, dimensions, and related emulation presets** (e.g. common iPhone/Android profiles) on the laptop; switching “device” is a product/feature problem, not a separate hardware SKU per user.
- **Implication for build:** One native shell embeds the web surface; device matrices are **configuration**, not separate client apps at v1.

## Example scenario — setting up Unity networking

Realistic multi-page session where Kakapo should clearly beat normal browsing.

- **Goal:** Integrate an open-source Unity networking stack; fix a compile syntax error while wiring setup.
- **Path complexity:** Official docs, GitHub README, migration notes, issues, Discord/forum snippets, StackOverflow, package changelog.
- **Why this matters:** This is exactly where users lose time: context switching, broken breadcrumbs, and “I saw that fix somewhere” memory gaps.

### Walk-through (desired Kakapo experience)

1. User says: “Set me up with `<package>` for Unity 2022 LTS, URP, host-client basics.”
2. Kakapo opens official install + quickstart docs, pins them as **Setup Trail**.
3. Compile fails. User says: “This syntax error in `NetworkBootstrap.cs`, find exact fix for this API version.”
4. Kakapo searches issues/release notes for matching signatures, then jumps to the exact relevant paragraph/code diff.
5. User says: “Show only fixes compatible with my Unity + package version.”
6. Kakapo filters out stale answers and surfaces version-matched steps.
7. User says: “Apply mentally: what do I change in my file?”
8. Kakapo summarizes a minimal patch plan (imports, method rename, init order) and links each step to source evidence.
9. User says: “Keep this as checklist; continue setup.”
10. Session continues with preserved trail + decision memory.

**Product signal for v1:** “Take me to the exact fix for *my* version/context” across many pages, with less backtracking.

## Reference

- Notebook brainstorm photo: `WhatsApp_Image_2026-03-29_at_18.13.04-0ae7d943-f3c9-423f-9f51-f30dabfe47cf.png` (see Cursor project assets).

## Open (not decided yet)

| Topic        | Status |
| ------------ | ------ |
| Primary user & context | Developer workflows; QA wedge currently strongest |
| Platform (desktop / extension / web) | IDE-first surface (exact form TBD) |
| V1 scope (dev flows, context graph, actions) | TBD |
| Secondary anchor use cases (2) | TBD — keep IDE-first concept honest beyond QA |
| Voice model (always-on vs push-to-talk, local vs cloud) | Always listening with wake word; ignore non-directed room chatter |
| Micro-interaction polish (status update cadence, etc.) | Deferred to execution; derive from established UX tenets |
| Trust / privacy stance | TBD |
| Success metrics | TBD |

## Decisions log

*(Newest first.)*

| Date | Decision | Notes |
| ---- | -------- | ----- |
| 2026-04-16 | **Hard pivot = IDE-first product** | Browser voice assistant not compelling enough as paid default; IDE assistance has clearer value and monetization |
| 2026-04-16 | **Context is core primitive** | Product must preserve/structure project knowledge for reliable AI and better collaboration handoffs |
| 2026-04-09 | **Product = browser first**; QA is wedge, not product definition | Avoid “testing suite that browses”; add **2 secondary** anchor jobs so non-QA users stay in scope |
| 2026-04-09 | **Platform = desktop app, macOS first** | QA uses laptop; device targets via viewport/dimension presets; **Windows not v1** |
| 2026-04-09 | **V1 wedge = QA web UI regression** | Daily use is heavy with incumbent solutions; product positions for paid power users, not leisure “free browser” replacement |
| 2026-03-29 | Requirement priority = **reliability is functional; speed is non-functional** | In conflicts, reliability wins every time to avoid wasted user journey/time |
| 2026-03-29 | Route ranking = **reliability first, simplicity second** | Kakapo should choose the most trustworthy path; then pick the least complex effective route |
| 2026-03-29 | Interaction model = **digital Kakapo guide** with current+next focus | Bird-like listening cues, first-step highlight + 3-6 word label, two alternatives, user can reroute by voice |
| 2026-03-29 | Voice model = **always listening + wake word** | Never feels "deaf"; avoid triggering on room chatter |
| 2026-03-29 | Uncertainty handling = **clarify by voice** | Prefer a short question over confidence tags; move forward based on user’s spoken correction |
| 2026-03-29 | Clarifying question template = **ask missing constraint** | Use concise prompts like “Give me your constraint: version/budget/goal?” to close knowledge gap fast |
| 2026-03-29 | Clarifier cadence = **one-by-one, with large-gap exception** | Minimize mental load; allow compact multi-part question only to avoid long frustrating loops |
| 2026-03-29 | Card UI = **show source badge by default** | Trust should be legible instantly on current/next cards |
| 2026-03-29 | Option selection = **natural language, not strict commands** | Parse user’s spontaneous phrasing for route choice intent |
| 2026-03-29 | Personalization = **helpful, optional adaptation** | Remember user phrasing only when it clearly improves understanding |
| 2026-03-29 | First-5s feedback = **light processing cue** | Bird may “look into computer” + subtle flash to show internal reasoning without heavy UI |
| 2026-03-29 | Processing feedback = **visual cue + tiny status text** | Keep users oriented and engaged while Kakapo reasons |
| 2026-03-29 | Status tone = **friendly but meaningful** | Friendly bird voice, never senseless fluff |
| 2026-03-29 | Status display threshold = **>1.0s** | Avoid unnecessary UI chatter on instant operations |
| 2026-03-29 | Selection confirmation = **orange preview + implicit yes proceeds** | Advance unless explicit no/correction is given |
| 2026-03-29 | Confirmation timing = **2.0s silence timeout proceeds** | Avoid dead air; continue unless explicit rejection arrives |
| 2026-03-29 | Highlight targeting = **first-look anchor point** | Bring exact relevant entry point into view (first relevant word/top of relevant visual block) |
| 2026-03-29 | Anchor fallback = **closest useful section first** | Keep momentum when exact pinpoint is uncertain; refine with user correction |
| 2026-03-29 | Information policy = **source-first**, then **stitched journey** | Prefer original page for explanation; synthesize only when one page is insufficient |
| 2026-03-29 | Value = **agentic + easier control**; anchor use = **multi-page sessions** | Ten concrete jobs captured; single vs multi-page framing documented |
| 2026-03-29 | UX bar = **effortless intuitive control**; feel = **panoramic session + pinpoint intent** | Metaphor: mystic / “third eye” → productized as trail awareness + snap-to-specific, not gimmick |

## Notes from discovery

*(Captured from conversation.)*

- Multi-page session idea formalized: **many innovative uses**, shared structure = **more than a couple webpages**, goal-driven hops.
- Notebook: 10 examples + quote on human–machine interface future; iteration visible (e.g. “today’s plan” replacing an earlier news-reader line).
- **Feel target:** User should steer the web like someone with **total read on the session** who can still **dial into one exact needle** — magic as **interaction design**, not marketing fluff.
- **Delivery rule:** Prefer showing the best original source page; when required, construct the most efficient multi-page learning/fix path.
- **V1 focus:** QA regression on web UI; commercial story = high-frequency **pro** work, not competing with free leisure browsing on Chrome-class browsers.
- **Browser-first:** Nail QA first, but hold **two secondary** use cases so the agentic-browser core does not collapse into a niche testing tool only.
- **Pivot note:** Move to IDE-first because developer expression + context continuity is a stronger paid problem than leisure browsing.
