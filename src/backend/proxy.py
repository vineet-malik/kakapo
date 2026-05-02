import argparse
import asyncio
import hashlib
import json
import os
import random
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

import faiss
import httpx
import numpy as np
import tiktoken
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OPENAI_BASE_URL = os.getenv(
    "OPENAI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/",
)
OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY",
    "",
).strip()
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemini-flash-latest")
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"

DB_PATH = "proxy.db"

# ---------------------------------------------------------------------------
# Semantic cache config
# ---------------------------------------------------------------------------
# Cosine similarity threshold — tune between 0.85 (looser) and 0.97 (stricter).
# 0.90 is a good starting point: catches paraphrases without false positives.
SEMANTIC_THRESHOLD = float(os.getenv("SEMANTIC_THRESHOLD", "0.90"))

# How long (seconds) a semantic cache entry is considered fresh. Default 24 h.
# Set to 0 to disable TTL entirely.
SEMANTIC_TTL_SECONDS = int(os.getenv("SEMANTIC_TTL_SECONDS", str(60 * 60 * 24)))

# Maximum number of vectors to keep in the FAISS index (LRU eviction beyond this).
SEMANTIC_MAX_ENTRIES = int(os.getenv("SEMANTIC_MAX_ENTRIES", "10000"))

GEMINI_PRICE = {"in": 0.0, "out": 0.0}
GPT4O_PRICE = {"in": 2.50, "out": 10.00}

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_encoder: Optional[SentenceTransformer] = None
_faiss_index: Optional[faiss.IndexFlatIP] = None
# Each entry: {response_json, original_query, created_at (unix ts)}
_faiss_payloads: list = []
_tokenizer = None
_db_conn: Optional[sqlite3.Connection] = None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db_conn.row_factory = sqlite3.Row
        _init_db(_db_conn)
    return _db_conn


def _init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS exact_cache (
            key TEXT PRIMARY KEY,
            response_json TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Semantic cache stored in SQLite for durability.
        -- The FAISS index is rebuilt from this table on startup.
        CREATE TABLE IF NOT EXISTS semantic_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_text TEXT NOT NULL,
            query_hash TEXT NOT NULL UNIQUE,
            response_json TEXT NOT NULL,
            created_at REAL NOT NULL,          -- Unix timestamp (float)
            last_used_at REAL NOT NULL,         -- For LRU eviction
            hit_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP,
            model TEXT,
            prompt_hash TEXT,
            prompt_preview TEXT,
            tokens_in INTEGER,
            tokens_out INTEGER,
            tokens_saved INTEGER DEFAULT 0,
            cost_usd REAL,
            counterfactual_usd REAL,
            saved_usd REAL,
            cache_status TEXT,
            latency_ms INTEGER
        );
    """)

    # Migrate older DBs
    cols = {row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()}
    if "tokens_saved" not in cols:
        conn.execute("ALTER TABLE requests ADD COLUMN tokens_saved INTEGER DEFAULT 0")
    conn.commit()


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------
def get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = tiktoken.get_encoding("cl100k_base")
    return _tokenizer


def count_tokens(text: str) -> int:
    return len(get_tokenizer().encode(text))


def messages_token_count(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        total += count_tokens(m.get("content") or "")
        total += 4  # per-message overhead
    return total


# ---------------------------------------------------------------------------
# Semantic cache — FAISS index rebuilt from SQLite on startup
# ---------------------------------------------------------------------------
def get_encoder() -> SentenceTransformer:
    global _encoder
    if _encoder is None:
        _encoder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _encoder


def _embed(text: str) -> np.ndarray:
    vec = get_encoder().encode([text], normalize_embeddings=True)[0]
    return vec.astype(np.float32)


def _build_faiss_index_from_db():
    """
    Rebuild the in-memory FAISS index from the semantic_cache SQLite table.

    This replaces the old pickle approach. Benefits:
    - Survives server crashes (SQLite is ACID, pickle is not)
    - Can be inspected / backed up with standard DB tooling
    - TTL eviction happens here — stale rows are deleted before the index is built
    """
    global _faiss_index, _faiss_payloads

    dim = 384  # all-MiniLM-L6-v2 output dimension
    _faiss_index = faiss.IndexFlatIP(dim)
    _faiss_payloads = []

    db = get_db()

    # Evict expired entries before rebuilding
    if SEMANTIC_TTL_SECONDS > 0:
        cutoff = time.time() - SEMANTIC_TTL_SECONDS
        deleted = db.execute(
            "DELETE FROM semantic_cache WHERE created_at < ?", (cutoff,)
        ).rowcount
        if deleted:
            print(f"[semantic_cache] Evicted {deleted} expired entries (TTL={SEMANTIC_TTL_SECONDS}s)")

    # Enforce max size via LRU — keep the most recently used entries
    total = db.execute("SELECT COUNT(*) FROM semantic_cache").fetchone()[0]
    if total > SEMANTIC_MAX_ENTRIES:
        to_delete = total - SEMANTIC_MAX_ENTRIES
        db.execute("""
            DELETE FROM semantic_cache
            WHERE id IN (
                SELECT id FROM semantic_cache
                ORDER BY last_used_at ASC
                LIMIT ?
            )
        """, (to_delete,))
        print(f"[semantic_cache] LRU evicted {to_delete} entries (max={SEMANTIC_MAX_ENTRIES})")

    db.commit()

    rows = db.execute(
        "SELECT id, query_text, response_json, created_at FROM semantic_cache ORDER BY id ASC"
    ).fetchall()

    if not rows:
        print("[semantic_cache] Empty — nothing to load")
        return

    # Batch-embed all stored queries for efficiency
    queries = [r["query_text"] for r in rows]
    encoder = get_encoder()
    vectors = encoder.encode(queries, normalize_embeddings=True, show_progress_bar=False)

    for i, row in enumerate(rows):
        vec = vectors[i].astype(np.float32).reshape(1, -1)
        _faiss_index.add(vec)
        _faiss_payloads.append({
            "db_id": row["id"],
            "response_json": row["response_json"],
            "original_query": row["query_text"],
            "created_at": row["created_at"],
        })

    print(f"[semantic_cache] Rebuilt FAISS index with {len(_faiss_payloads)} entries")


def semantic_lookup(query: str) -> Optional[dict]:
    """
    Look up a query in the semantic cache.

    Returns the payload dict (including response_json) on a hit, or None.
    Also updates last_used_at and hit_count in SQLite on a hit (for LRU eviction).

    Improvement over original:
    - Checks TTL at lookup time as a second guard (index may be stale mid-run)
    - Updates usage stats so LRU eviction is accurate
    - Returns the top-k and picks the best scoring *non-expired* entry
      instead of blindly returning the nearest vector (which may be stale)
    """
    if _faiss_index is None or _faiss_index.ntotal == 0:
        return None

    vec = _embed(query).reshape(1, -1)

    # Fetch top 5 candidates — lets us skip expired ones without a full scan
    k = min(5, _faiss_index.ntotal)
    scores, indices = _faiss_index.search(vec, k)

    now = time.time()
    db = get_db()

    for rank in range(k):
        score = scores[0][rank]
        if score < SEMANTIC_THRESHOLD:
            break  # Results are ordered by score; no point continuing

        idx = indices[0][rank]
        if idx < 0 or idx >= len(_faiss_payloads):
            continue

        payload = _faiss_payloads[idx]

        # Runtime TTL check (handles entries added after last restart)
        if SEMANTIC_TTL_SECONDS > 0:
            age = now - payload.get("created_at", 0)
            if age > SEMANTIC_TTL_SECONDS:
                continue

        # Update usage for LRU eviction
        db_id = payload.get("db_id")
        if db_id:
            db.execute(
                "UPDATE semantic_cache SET last_used_at = ?, hit_count = hit_count + 1 WHERE id = ?",
                (now, db_id),
            )
            db.commit()

        return payload

    return None


def semantic_add(query: str, response_json: str):
    """
    Add a new entry to both the SQLite semantic_cache table and the FAISS index.

    Skips duplicates using a hash of the query text.
    Enforces max size with LRU eviction before inserting.

    Improvement over original:
    - Persists to SQLite immediately (crash-safe)
    - Deduplicates via query_hash
    - Enforces SEMANTIC_MAX_ENTRIES cap at write time
    """
    if not query.strip():
        return

    query_hash = hashlib.sha256(query.encode()).hexdigest()
    now = time.time()
    db = get_db()

    # Skip if we already have this exact query cached
    existing = db.execute(
        "SELECT id FROM semantic_cache WHERE query_hash = ?", (query_hash,)
    ).fetchone()
    if existing:
        return

    # Enforce max size: evict LRU entry if at capacity
    total = db.execute("SELECT COUNT(*) FROM semantic_cache").fetchone()[0]
    if total >= SEMANTIC_MAX_ENTRIES:
        lru_id = db.execute(
            "SELECT id FROM semantic_cache ORDER BY last_used_at ASC LIMIT 1"
        ).fetchone()
        if lru_id:
            db.execute("DELETE FROM semantic_cache WHERE id = ?", (lru_id["id"],))
            # Also remove from in-memory index — rebuild is cheaper than
            # trying to surgically remove a FAISS vector (FAISS doesn't support
            # random deletion on IndexFlatIP; we'd need IndexIDMap for that).
            # For simplicity we rebuild when an eviction happens. This is rare
            # (only when the cache is full) so the overhead is acceptable.
            _rebuild_faiss_from_db_sync(db)

    # Insert into SQLite
    cursor = db.execute(
        """INSERT OR IGNORE INTO semantic_cache
           (query_text, query_hash, response_json, created_at, last_used_at, hit_count)
           VALUES (?, ?, ?, ?, ?, 0)""",
        (query, query_hash, response_json, now, now),
    )
    db.commit()

    if cursor.rowcount == 0:
        return  # Race condition — another insert won

    # Add vector to in-memory FAISS index
    vec = _embed(query).reshape(1, -1)
    _faiss_index.add(vec)

    new_db_id = db.execute(
        "SELECT id FROM semantic_cache WHERE query_hash = ?", (query_hash,)
    ).fetchone()["id"]

    _faiss_payloads.append({
        "db_id": new_db_id,
        "response_json": response_json,
        "original_query": query,
        "created_at": now,
    })


def _rebuild_faiss_from_db_sync(db: sqlite3.Connection):
    """
    Re-sync the in-memory FAISS index with what's currently in SQLite.
    Called after an LRU eviction to keep index and DB in sync.
    """
    global _faiss_index, _faiss_payloads

    dim = 384
    _faiss_index = faiss.IndexFlatIP(dim)
    _faiss_payloads = []

    rows = db.execute(
        "SELECT id, query_text, response_json, created_at FROM semantic_cache ORDER BY id ASC"
    ).fetchall()

    if not rows:
        return

    queries = [r["query_text"] for r in rows]
    encoder = get_encoder()
    vectors = encoder.encode(queries, normalize_embeddings=True, show_progress_bar=False)

    for i, row in enumerate(rows):
        vec = vectors[i].astype(np.float32).reshape(1, -1)
        _faiss_index.add(vec)
        _faiss_payloads.append({
            "db_id": row["id"],
            "response_json": row["response_json"],
            "original_query": row["query_text"],
            "created_at": row["created_at"],
        })


# ---------------------------------------------------------------------------
# Exact cache
# ---------------------------------------------------------------------------
def exact_cache_key(model: str, messages: list[dict], temperature: float) -> str:
    """Key on the last user message only (see original comments for rationale)."""
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m.get("content") or ""
            break
    raw = model + "\n" + last_user + "\n" + str(temperature)
    return hashlib.sha256(raw.encode()).hexdigest()


def exact_lookup(key: str) -> Optional[str]:
    row = get_db().execute(
        "SELECT response_json FROM exact_cache WHERE key = ?", (key,)
    ).fetchone()
    return row["response_json"] if row else None


def exact_store(key: str, response_json: str):
    get_db().execute(
        "INSERT OR REPLACE INTO exact_cache (key, response_json) VALUES (?, ?)",
        (key, response_json),
    )
    get_db().commit()


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------
def compute_costs(tokens_in: int, tokens_out: int, cache_hit: bool):
    counterfactual = (tokens_in / 1e6) * GPT4O_PRICE["in"] + (tokens_out / 1e6) * GPT4O_PRICE["out"]
    cost = (tokens_in / 1e6) * GEMINI_PRICE["in"] + (tokens_out / 1e6) * GEMINI_PRICE["out"]
    if cache_hit:
        cost = 0.0
        saved = counterfactual
        tokens_saved = tokens_in + tokens_out
    else:
        saved = 0.0
        tokens_saved = 0
    return cost, counterfactual, saved, tokens_saved


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log_request(
    model: str,
    prompt_hash: str,
    prompt_preview: str,
    tokens_in: int,
    tokens_out: int,
    cache_status: str,
    latency_ms: int,
):
    cache_hit = cache_status in ("exact", "semantic")
    cost, counterfactual, saved, tokens_saved = compute_costs(tokens_in, tokens_out, cache_hit)
    get_db().execute(
        """INSERT INTO requests
           (model, prompt_hash, prompt_preview, tokens_in, tokens_out, tokens_saved,
            cost_usd, counterfactual_usd, saved_usd, cache_status, latency_ms)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (model, prompt_hash, prompt_preview, tokens_in, tokens_out, tokens_saved,
         cost, counterfactual, saved, cache_status, latency_ms),
    )
    get_db().commit()


# ---------------------------------------------------------------------------
# Mock response
# ---------------------------------------------------------------------------
def make_mock_response(model: str, tokens_in: int) -> dict:
    tokens_out = random.randint(50, 200)
    content = "Sure, here's an answer to your question. This is a mock response generated in MOCK_MODE."
    return {
        "id": f"chatcmpl-mock-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    }


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------
SEED_QA_PAIRS = [
    ("What is the capital of France?",
     "The capital of France is Paris."),
    ("How does photosynthesis work?",
     "Photosynthesis is the process by which plants convert sunlight, water, and carbon dioxide into glucose and oxygen, using chlorophyll in their leaves."),
    ("What is the difference between TCP and UDP?",
     "TCP is connection-oriented and guarantees ordered, reliable delivery. UDP is connectionless, has lower overhead, and does not guarantee delivery or order."),
    ("Explain the theory of relativity in simple terms.",
     "Einstein's theory of relativity says that time and space are linked, and that measurements of time and distance depend on the observer's motion and gravity."),
    ("What is machine learning?",
     "Machine learning is a branch of AI where computers learn patterns from data to make predictions or decisions without being explicitly programmed for each task."),
    ("How do I reverse a string in Python?",
     "You can reverse a string in Python using slicing: `s[::-1]`. For example, `'hello'[::-1]` returns `'olleh'`."),
    ("What causes the seasons on Earth?",
     "The seasons are caused by Earth's axial tilt of about 23.5 degrees, which changes how directly sunlight hits different parts of the planet as Earth orbits the Sun."),
    ("What is the difference between SQL and NoSQL databases?",
     "SQL databases are relational with fixed schemas and use structured query language. NoSQL databases are non-relational, schema-flexible, and optimized for scale or specific data shapes like documents, key-value, or graphs."),
    ("How does HTTPS work?",
     "HTTPS encrypts HTTP traffic using TLS. The client and server perform a handshake to agree on a cipher and exchange keys via certificates, then all subsequent data is encrypted end-to-end."),
    ("What is a Python decorator?",
     "A Python decorator is a function that takes another function and extends its behavior without modifying it directly. It's applied using the `@decorator_name` syntax above a function definition."),
]


def _seed_semantic_cache():
    seeded = 0
    for question, answer in SEED_QA_PAIRS:
        tokens_in = count_tokens(question) + 4
        tokens_out = count_tokens(answer)
        resp = {
            "id": f"chatcmpl-seed-{seeded}",
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
        resp_json = json.dumps(resp)
        messages = [{"role": "user", "content": question}]
        exact_store(exact_cache_key(DEFAULT_MODEL, messages, 1.0), resp_json)
        semantic_add(question, resp_json)
        seeded += 1

    print(f"[seed] Pre-populated cache with {seeded} Q/A pairs")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB must be initialised before we rebuild the FAISS index from it
    get_db()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_tokenizer)
    # Rebuild FAISS index from SQLite (replaces pickle load)
    await loop.run_in_executor(None, _build_faiss_index_from_db)
    if os.getenv("SEED_CACHE", "false").lower() == "true":
        await loop.run_in_executor(None, _seed_semantic_cache)
    yield
    # No need to save pickle on shutdown — SQLite is already up-to-date
    if _db_conn:
        _db_conn.close()


app = FastAPI(lifespan=lifespan)


@app.post("/v1/chat/completions")
async def chat_completions(request: dict):
    t0 = time.time()

    messages: list[dict] = request.get("messages", [])
    model: str = request.get("model", DEFAULT_MODEL)
    temperature: float = float(request.get("temperature", 1.0))

    last_user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user_msg = m.get("content") or ""
            break

    tokens_in = messages_token_count(messages)
    prompt_hash = hashlib.sha256(last_user_msg.encode()).hexdigest()[:16]
    prompt_preview = last_user_msg[:60]

    # 1. Exact cache
    exact_key = exact_cache_key(model, messages, temperature)
    cached = exact_lookup(exact_key)
    if cached:
        latency_ms = int((time.time() - t0) * 1000)
        resp = json.loads(cached)
        tokens_out = resp.get("usage", {}).get("completion_tokens", 0)
        log_request(model, prompt_hash, prompt_preview, tokens_in, tokens_out, "exact", latency_ms)
        return JSONResponse(content=resp)

    # 2. Semantic cache
    if last_user_msg.strip():
        sem_hit = semantic_lookup(last_user_msg)
        if sem_hit:
            latency_ms = int((time.time() - t0) * 1000)
            resp = json.loads(sem_hit["response_json"])
            tokens_out = resp.get("usage", {}).get("completion_tokens", 0)
            log_request(model, prompt_hash, prompt_preview, tokens_in, tokens_out, "semantic", latency_ms)
            return JSONResponse(content=resp)

    # 3. Cache miss — upstream API or mock
    if MOCK_MODE:
        await asyncio.sleep(0.8)
        resp = make_mock_response(model, tokens_in)
        tokens_out = resp["usage"]["completion_tokens"]
    else:
        payload = dict(request)
        if "model" not in payload:
            payload["model"] = DEFAULT_MODEL

        async with httpx.AsyncClient(timeout=60.0) as client:
            api_resp = await client.post(
                OPENAI_BASE_URL.rstrip("/") + "/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            api_resp.raise_for_status()
            resp = api_resp.json()

        tokens_out = resp.get("usage", {}).get("completion_tokens", 0)

    resp_json = json.dumps(resp)
    exact_store(exact_key, resp_json)
    semantic_add(last_user_msg, resp_json)

    latency_ms = int((time.time() - t0) * 1000)
    log_request(model, prompt_hash, prompt_preview, tokens_in, tokens_out, "miss", latency_ms)
    return JSONResponse(content=resp)


# ---------------------------------------------------------------------------
# Stats API — now includes semantic cache health info
# ---------------------------------------------------------------------------
@app.get("/api/stats")
async def stats():
    db = get_db()

    row = db.execute("""
        SELECT
            COALESCE(SUM(cost_usd), 0.0)          AS total_spent_usd,
            COALESCE(SUM(saved_usd), 0.0)          AS total_saved_usd,
            COALESCE(SUM(tokens_saved), 0)         AS total_tokens_saved,
            COUNT(*)                                AS total_requests,
            COALESCE(SUM(CASE WHEN cache_status IN ('exact','semantic') THEN 1 ELSE 0 END), 0) AS hits
        FROM requests
    """).fetchone()

    total = row["total_requests"]
    hits = row["hits"]
    cache_hit_rate = (hits / total) if total > 0 else 0.0

    cutoff = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    bucket_rows = db.execute("""
        SELECT
            strftime('%Y-%m-%dT%H:%M:00', ts) AS bucket,
            SUM(saved_usd) AS bucket_saved
        FROM requests
        WHERE ts >= ?
        GROUP BY bucket
        ORDER BY bucket
    """, (cutoff,)).fetchall()

    cumulative = 0.0
    timeline = []
    for r in bucket_rows:
        cumulative += r["bucket_saved"]
        timeline.append({"ts": r["bucket"], "cumulative_saved": round(cumulative, 6)})

    recent_rows = db.execute("""
        SELECT ts, model, cache_status, tokens_in, tokens_out, tokens_saved,
               saved_usd, prompt_preview
        FROM requests
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    recent_requests = [
        {
            "ts": r["ts"],
            "model": r["model"],
            "cache_status": r["cache_status"],
            "tokens_in": r["tokens_in"],
            "tokens_out": r["tokens_out"],
            "tokens_saved": r["tokens_saved"] or 0,
            "saved_usd": round(r["saved_usd"], 6),
            "prompt_preview": r["prompt_preview"],
        }
        for r in recent_rows
    ]

    # Semantic cache health
    sem_row = db.execute("""
        SELECT COUNT(*) AS total,
               AVG(hit_count) AS avg_hits,
               MIN(created_at) AS oldest
        FROM semantic_cache
    """).fetchone()

    semantic_cache_info = {
        "total_entries": sem_row["total"] or 0,
        "avg_hit_count": round(sem_row["avg_hits"] or 0, 2),
        "oldest_entry_age_hours": round(
            (time.time() - (sem_row["oldest"] or time.time())) / 3600, 1
        ),
        "max_entries": SEMANTIC_MAX_ENTRIES,
        "ttl_hours": SEMANTIC_TTL_SECONDS / 3600 if SEMANTIC_TTL_SECONDS > 0 else None,
        "threshold": SEMANTIC_THRESHOLD,
    }

    return {
        "total_spent_usd": round(row["total_spent_usd"], 6),
        "total_saved_usd": round(row["total_saved_usd"], 6),
        "total_tokens_saved": int(row["total_tokens_saved"] or 0),
        "cache_hit_rate": round(cache_hit_rate, 4),
        "total_requests": total,
        "timeline": timeline,
        "recent_requests": recent_requests,
        "semantic_cache": semantic_cache_info,
    }


@app.get("/api/semantic-cache")
async def semantic_cache_entries():
    """Inspect the semantic cache — useful for debugging threshold tuning."""
    db = get_db()
    rows = db.execute("""
        SELECT id, query_text, created_at, last_used_at, hit_count
        FROM semantic_cache
        ORDER BY last_used_at DESC
        LIMIT 100
    """).fetchall()
    now = time.time()
    return {
        "entries": [
            {
                "id": r["id"],
                "query": r["query_text"],
                "age_hours": round((now - r["created_at"]) / 3600, 1),
                "last_used_hours_ago": round((now - r["last_used_at"]) / 3600, 1),
                "hit_count": r["hit_count"],
            }
            for r in rows
        ]
    }


@app.delete("/api/semantic-cache")
async def clear_semantic_cache():
    """Wipe the entire semantic cache (useful when changing models or threshold)."""
    global _faiss_index, _faiss_payloads
    db = get_db()
    deleted = db.execute("DELETE FROM semantic_cache").rowcount
    db.commit()
    dim = 384
    _faiss_index = faiss.IndexFlatIP(dim)
    _faiss_payloads = []
    return {"deleted": deleted}


@app.get("/dashboard")
async def dashboard():
    if os.path.exists("dashboard.html"):
        return FileResponse("dashboard.html")
    return JSONResponse({"error": "dashboard.html not found"}, status_code=404)


@app.get("/demo")
async def demo():
    if os.path.exists("demo.html"):
        return FileResponse("demo.html")
    return JSONResponse({"error": "demo.html not found"}, status_code=404)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM Optimization Proxy")
    parser.add_argument(
        "--seed-cache",
        action="store_true",
        help="Pre-populate the semantic cache with 10 sample Q/A pairs on startup",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.seed_cache:
        os.environ["SEED_CACHE"] = "true"

    uvicorn.run("proxy:app", host=args.host, port=args.port, reload=False)
