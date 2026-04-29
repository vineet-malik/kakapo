"""
Seeds the proxy's exact + semantic caches with 15 curated Q/A pairs,
and backfills 12 fake rows in the `requests` table over the last 30
minutes so the dashboard has baseline history on first load.

IMPORTANT: The proxy MUST be stopped before running this script — it
writes to cache.pkl and proxy.db directly.

Run from src/backend/:
    python3 seed_cache.py
"""

import json
import os
import pickle
import random
import sqlite3
import time
from datetime import datetime, timedelta

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from proxy import (
    CACHE_PKL,
    DB_PATH,
    DEFAULT_MODEL,
    GPT4O_PRICE,
    count_tokens,
    exact_cache_key,
    _init_db,
)

# ---------------------------------------------------------------------------
# Seed Q/A pairs (15 total)
# ---------------------------------------------------------------------------
SEED_QA: list[tuple[str, str]] = [
    (
        "How do I reset my password?",
        "To reset your password, click the 'Forgot password?' link on the sign-in page and enter the email address associated with your account. "
        "We'll send you a secure reset link that expires in 30 minutes. Follow the link, choose a new password that's at least 8 characters with a mix of letters, "
        "numbers, and symbols, and you'll be signed in automatically. If the email doesn't arrive within a few minutes, check your spam folder or contact support "
        "and we can manually trigger a reset."
    ),
    (
        "What's included in the Pro plan?",
        "The Pro plan includes unlimited requests per month, priority response times under 200ms p95, access to all Gemini and GPT-tier models, up to 10 team seats, "
        "90 days of full request logs with export, and access to the semantic cache tuning controls. You also get SSO via Google or GitHub, a dedicated support "
        "channel with same-day response, and early access to new features. Pro is $49/month billed annually, or $59 month-to-month, and you can upgrade or downgrade "
        "anytime from the billing page."
    ),
    (
        "Can I export my data to CSV?",
        "Yes — every data table in the app has an Export button in the top-right that downloads the current view as CSV. "
        "This includes request logs, cost breakdowns, and cache analytics. For larger exports (over 100k rows) we stream the file in chunks so your browser won't hang. "
        "If you need programmatic access, the same data is available via the /api/export endpoint which returns CSV, JSON, or Parquet — see the API reference for "
        "authentication and query parameters."
    ),
    (
        "How do I cancel my subscription?",
        "You can cancel your subscription at any time from Settings → Billing → Cancel subscription. Cancellation takes effect at the end of the current billing "
        "period, so you'll continue to have full access until then and won't be charged again. Your data is retained for 30 days after cancellation in case you "
        "want to reactivate, after which it's permanently deleted. If you cancel within 14 days of a new annual plan, we offer a full refund — just reply to your "
        "purchase confirmation email."
    ),
    (
        "Is there a mobile app available?",
        "We have a mobile-optimized web app that works on any modern mobile browser — just open the site on your phone and it'll adapt automatically. "
        "Native iOS and Android apps are on our roadmap for Q3, with push notifications for budget alerts and anomaly detection. In the meantime, you can "
        "'Add to Home Screen' from Safari or Chrome to get an app-like experience with offline caching of your dashboard. If there's a specific mobile workflow "
        "you need, let us know in the feedback form and we'll prioritize it."
    ),
    (
        "What's the difference between REST and GraphQL?",
        "REST exposes multiple endpoints, each returning a fixed shape — you often need several round trips and get data you don't use. "
        "GraphQL exposes a single endpoint where the client describes exactly the fields it wants in one query, so you fetch less data in fewer requests. "
        "REST is simpler to cache at the HTTP layer and plays nicely with CDNs; GraphQL is better for complex clients with varying data needs but requires more "
        "thought around n+1 queries, persisted queries for caching, and query complexity limits. In practice: REST for straightforward CRUD and public APIs, "
        "GraphQL when you have many heterogeneous clients hitting the same data graph."
    ),
    (
        "How does semantic caching work?",
        "Semantic caching stores prior LLM responses keyed by the meaning of the prompt rather than its exact text. Each incoming prompt is converted into a "
        "dense vector embedding using a model like all-MiniLM-L6-v2, then compared via cosine similarity against embeddings of previously cached prompts. "
        "If the similarity exceeds a threshold (commonly 0.90–0.95), the cached response is returned — saving the model call entirely. This catches paraphrases, "
        "typos, and reworded follow-ups that exact hashing would miss. The tradeoff is that a too-loose threshold returns incorrect cached answers, so picking "
        "the right cutoff per domain is the main tuning knob."
    ),
    (
        "What's a good React state management library?",
        "For most apps, start with React's built-in useState and useReducer — they handle 80% of state needs without a library. If you need to share state "
        "across many components, Zustand is the current favorite: tiny API, no boilerplate, works outside React. Redux Toolkit is still excellent for large "
        "apps with complex async flows and time-travel debugging needs. For server state specifically (API data), use TanStack Query (React Query) — it handles "
        "caching, refetching, and invalidation far better than any global-state library. Jotai and Recoil are good atomic alternatives if Zustand's store pattern "
        "doesn't fit. Avoid Context API for frequently-changing state — it causes wasteful re-renders."
    ),
    (
        "Explain the CAP theorem briefly",
        "The CAP theorem states that in a distributed system, you can guarantee at most two of three properties: Consistency (every read sees the latest write), "
        "Availability (every request gets a response), and Partition tolerance (the system keeps working despite network splits). Since network partitions are "
        "unavoidable in any real distributed system, the real choice is between CP (consistent but may reject requests during a partition — like etcd or HBase) "
        "and AP (always responds but may return stale data — like Cassandra or DynamoDB in its default mode). The 'pick two' framing is a simplification: in "
        "practice it's a spectrum with tunable consistency levels, and PACELC extends the theorem to cover latency tradeoffs in normal operation."
    ),
    (
        "How do I deploy a FastAPI app to production?",
        "The standard production stack is Uvicorn or Gunicorn-with-Uvicorn-workers behind a reverse proxy like nginx or Caddy, containerized with Docker. "
        "For a quick start: write a Dockerfile based on python:3.11-slim, copy requirements and install, then run `gunicorn app:app -w 4 -k uvicorn.workers.UvicornWorker`. "
        "Deploy the image to Fly.io, Railway, or Google Cloud Run for a simple managed setup, or to ECS/Kubernetes if you need more control. Essentials: "
        "structured logging (use loguru or structlog), health-check endpoints at /healthz and /readyz, Prometheus metrics via prometheus-fastapi-instrumentator, "
        "and environment-based config with pydantic-settings. Put long-running work in a background queue (Celery or arq with Redis) rather than in request handlers."
    ),
    (
        "What are the benefits of using TypeScript?",
        "TypeScript catches entire categories of bugs at compile time — typos in property names, wrong argument types, forgetting to handle null, missing cases "
        "in a switch — which would otherwise only surface at runtime or in production. It makes refactoring dramatically safer: rename a field and the compiler "
        "tells you every call site to update. IDE experience improves significantly with precise autocomplete, inline docs, and go-to-definition that actually "
        "works across modules. For teams, types act as enforced, always-up-to-date documentation on function contracts. The cost is a build step and some learning "
        "curve around generics and conditional types, but for any codebase you'll maintain for more than a few weeks, the productivity gain is worth it."
    ),
    (
        "How do I optimize a slow SQL query?",
        "Start by running EXPLAIN ANALYZE to see the actual execution plan — look for sequential scans on large tables, nested loops with high row counts, "
        "or sorts that spill to disk. The most common wins are: add indexes on columns used in WHERE, JOIN, and ORDER BY clauses (multi-column indexes should "
        "match the query's filter order); rewrite correlated subqueries as JOINs or window functions; avoid SELECT * and fetch only the columns you need; "
        "and use LIMIT with an ORDER BY on an indexed column for pagination rather than OFFSET on large result sets. If the query is fine but the plan is bad, "
        "check if statistics are stale (ANALYZE the table). For recurring slow queries, consider a materialized view refreshed on a schedule. Finally, sometimes "
        "the fix is architectural — denormalizing, caching at the app layer, or moving analytics off your OLTP database."
    ),
    (
        "What's the best way to handle authentication in Next.js?",
        "For most Next.js apps, NextAuth.js (now Auth.js) is the default choice — it handles OAuth providers, email magic links, credentials, session management, "
        "and JWT or database sessions out of the box, and integrates cleanly with both the Pages and App Router. For enterprise needs with SSO, SAML, or complex "
        "RBAC, a hosted provider like Clerk, Auth0, or WorkOS gives you drop-in components and managed user directories. If you're building something simple "
        "with just email/password, you can roll your own with iron-session for encrypted cookies and Argon2 for password hashing — about 100 lines of code. "
        "Whatever you choose: store sessions in httpOnly secure cookies (not localStorage), enable CSRF protection on mutation routes, and rate-limit login "
        "attempts by IP and email."
    ),
    (
        "Compare Kubernetes and Docker Swarm",
        "Kubernetes is the industry standard for container orchestration: massive ecosystem, every cloud provider has a managed offering (EKS, GKE, AKS), "
        "rich features for rolling deploys, autoscaling, service mesh, secrets, and multi-tenancy. The cost is steep complexity — you'll spend real time on "
        "YAML, operators, networking (CNI), and storage (CSI). Docker Swarm is dramatically simpler: a few commands to set up a cluster, compose-file compatible, "
        "built into Docker itself — you can be running a production cluster in under an hour. The tradeoff is a much smaller ecosystem, slower feature velocity, "
        "and fewer managed services. Rule of thumb: small team running 5–20 services on a few nodes → Swarm is often enough and easier to operate. "
        "Anything with multi-region, multi-team, or complex traffic shaping → Kubernetes, ideally managed."
    ),
    (
        "How do I set up CI/CD with GitHub Actions?",
        "Create a .github/workflows/ci.yml file in your repo — that's it, GitHub picks it up automatically. A typical setup has two workflows: one that runs on "
        "pull requests (lint, type-check, unit tests) and one that runs on pushes to main (build, integration tests, deploy). Use the matrix strategy to test "
        "across Node/Python versions in parallel, actions/cache for dependency caching to cut install time by 70%+, and concurrency groups to cancel superseded "
        "runs on rapid pushes. For deploys, store credentials in GitHub Secrets (never in the workflow file), use OIDC to authenticate to AWS/GCP without long-lived "
        "keys, and gate production deploys behind a manual approval using environments. Keep individual steps under 10 minutes — if a step runs longer, split it "
        "or move it to a self-hosted runner."
    ),
]

assert len(SEED_QA) == 15, f"expected 15 seed pairs, got {len(SEED_QA)}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def build_response_envelope(i: int, question: str, answer: str) -> dict:
    tokens_in = count_tokens(question) + 4
    tokens_out = count_tokens(answer)
    return {
        "id": f"chatcmpl-seed-{i}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": DEFAULT_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    }


SEED_HISTORY_MODELS = [
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash",
    "gemma-3-4b-it",
]


def seed_fake_history(conn: sqlite3.Connection, n: int = 12):
    """Insert fake historical rows spread across the last 30 minutes."""
    now = datetime.utcnow()
    statuses = ["miss", "miss", "miss", "exact", "exact", "semantic", "semantic"]

    # Drop any prior seeded demo history so repeated runs don't duplicate.
    conn.execute("DELETE FROM requests WHERE prompt_preview LIKE '[seed-demo]%'")

    rows = []
    for i in range(n):
        minutes_ago = random.uniform(0.5, 29.5)
        ts = now - timedelta(minutes=minutes_ago)
        question, answer = random.choice(SEED_QA)
        tokens_in = count_tokens(question) + 4
        tokens_out = count_tokens(answer)
        model = random.choice(SEED_HISTORY_MODELS)

        status = random.choice(statuses)
        cache_hit = status in ("exact", "semantic")

        counterfactual = (tokens_in / 1e6) * GPT4O_PRICE["in"] + (tokens_out / 1e6) * GPT4O_PRICE["out"]
        cost = 0.0  # Gemini free tier; even on a miss we don't pay.
        # Saved is *only* what the cache spared us: 0 on miss, full counterfactual on hit.
        saved = counterfactual if cache_hit else 0.0
        tokens_saved = (tokens_in + tokens_out) if cache_hit else 0

        latency = random.randint(10, 80) if cache_hit else random.randint(400, 1800)

        rows.append((
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            model,
            f"seed-{i:03x}",
            f"[seed-demo] {question[:45]}",
            tokens_in,
            tokens_out,
            tokens_saved,
            cost,
            counterfactual,
            saved,
            status,
            latency,
        ))

    # Sort by timestamp so auto-increment IDs line up with chronology.
    rows.sort(key=lambda r: r[0])
    conn.executemany(
        """INSERT INTO requests
           (ts, model, prompt_hash, prompt_preview, tokens_in, tokens_out, tokens_saved,
            cost_usd, counterfactual_usd, saved_usd, cache_status, latency_ms)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"[seed] Loading embedding model (all-MiniLM-L6-v2)…")
    encoder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    # Start FAISS + responses list fresh. (If you want to merge into existing
    # cache.pkl, load it first and append — we overwrite for deterministic demos.)
    dim = 384
    index = faiss.IndexFlatIP(dim)
    responses: list[dict] = []

    # Init DB
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _init_db(conn)

    print(f"[seed] Populating exact + semantic caches with {len(SEED_QA)} Q/A pairs…")
    for i, (question, answer) in enumerate(SEED_QA):
        envelope = build_response_envelope(i, question, answer)
        envelope_json = json.dumps(envelope)

        # Exact cache: same key format proxy.py uses
        messages = [{"role": "user", "content": question}]
        key = exact_cache_key(DEFAULT_MODEL, messages, 1.0)
        conn.execute(
            "INSERT OR REPLACE INTO exact_cache (key, response_json) VALUES (?, ?)",
            (key, envelope_json),
        )

        # Semantic cache: normalized embedding into FAISS
        vec = encoder.encode([question], normalize_embeddings=True)[0].astype(np.float32)
        index.add(vec.reshape(1, -1))
        responses.append({"response_json": envelope_json, "original_query": question})

    conn.commit()

    # Persist cache.pkl in the exact shape proxy.py expects at startup
    with open(CACHE_PKL, "wb") as f:
        pickle.dump({"index": index, "payloads": responses}, f)
    print(f"[seed] Wrote {CACHE_PKL} ({len(responses)} entries, dim={dim})")

    # Baseline history for the dashboard
    n_rows = seed_fake_history(conn)
    print(f"[seed] Inserted {n_rows} fake request rows spanning the last 30 minutes")

    conn.close()
    print(f"Seeded {len(SEED_QA)} queries into cache")


if __name__ == "__main__":
    main()
