import argparse
import asyncio
import hashlib
import json
import os
import pickle
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
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemini-flash-latest")
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"

DB_PATH = "proxy.db"
CACHE_PKL = "cache.pkl"
SEMANTIC_THRESHOLD = 0.93
# Allow short prompts ("hello", "hi") into the semantic cache too. The
# threshold still gates correctness; this just stops us from skipping them.
MIN_CHARS_FOR_SEMANTIC = 0

GEMINI_PRICE = {"in": 0.0, "out": 0.0}
GPT4O_PRICE = {"in": 2.50, "out": 10.00}

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_encoder: Optional[SentenceTransformer] = None
_faiss_index: Optional[faiss.IndexFlatIP] = None
_faiss_payloads: list = []  # [{response_json, original_query}]
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
    # Migrate older DBs that predate the tokens_saved column.
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
# Semantic cache
# ---------------------------------------------------------------------------
def get_encoder() -> SentenceTransformer:
    global _encoder
    if _encoder is None:
        _encoder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _encoder


def _load_semantic_cache():
    global _faiss_index, _faiss_payloads
    if os.path.exists(CACHE_PKL):
        try:
            with open(CACHE_PKL, "rb") as f:
                data = pickle.load(f)
            _faiss_index = data["index"]
            _faiss_payloads = data["payloads"]
            print(f"[cache] Loaded {len(_faiss_payloads)} semantic entries from {CACHE_PKL}")
            return
        except Exception as e:
            print(f"[cache] Failed to load {CACHE_PKL}: {e}")
    dim = 384  # all-MiniLM-L6-v2 output dim
    _faiss_index = faiss.IndexFlatIP(dim)
    _faiss_payloads = []


def _save_semantic_cache():
    if _faiss_index is None:
        return
    with open(CACHE_PKL, "wb") as f:
        pickle.dump({"index": _faiss_index, "payloads": _faiss_payloads}, f)
    print(f"[cache] Saved {len(_faiss_payloads)} semantic entries to {CACHE_PKL}")


def _embed(text: str) -> np.ndarray:
    enc = get_encoder()
    vec = enc.encode([text], normalize_embeddings=True)[0]
    return vec.astype(np.float32)


def semantic_lookup(query: str) -> Optional[dict]:
    if _faiss_index is None or _faiss_index.ntotal == 0:
        return None
    vec = _embed(query).reshape(1, -1)
    scores, indices = _faiss_index.search(vec, 1)
    if scores[0][0] >= SEMANTIC_THRESHOLD:
        return _faiss_payloads[indices[0][0]]
    return None


def semantic_add(query: str, response_json: str):
    if len(query) <= MIN_CHARS_FOR_SEMANTIC:
        return
    vec = _embed(query).reshape(1, -1)
    _faiss_index.add(vec)
    _faiss_payloads.append({"response_json": response_json, "original_query": query})


# ---------------------------------------------------------------------------
# Exact cache
# ---------------------------------------------------------------------------
def exact_cache_key(model: str, messages: list[dict], temperature: float) -> str:
    """Key on the last user message only.

    Hashing the full message history meant the second 'hello' in a chat had a
    different key from the first (because the prior assistant turn was now in
    the list), so identical user prompts always missed. For a Q/A demo the
    intent is "same prompt → same cached answer," so we key on the last user
    turn plus model and temperature.
    """
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
    """Returns (cost, counterfactual, saved, tokens_saved).

    `counterfactual` is what the same call would cost on a baseline model
    (GPT-4o pricing here) — useful as a "what if we always used the expensive
    model" reference even on cache misses.

    `saved` is *only* what the cache saved us. On a miss we still hit the
    upstream model, so the cache saved us $0 and 0 tokens — the previous
    behaviour of crediting `counterfactual - cost` on misses bundled in
    routing savings and made the demo confusing.
    """
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
# Seed data — demo-friendly Q/A pairs for pre-populating semantic cache
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
    """Pre-populate both caches with canned Q/A pairs so early demo
    requests hit the cache immediately."""
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
    _load_semantic_cache()
    # Pre-warm tokenizer and encoder in background
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_tokenizer)
    if os.getenv("SEED_CACHE", "false").lower() == "true":
        await loop.run_in_executor(None, _seed_semantic_cache)
    yield
    _save_semantic_cache()
    if _db_conn:
        _db_conn.close()


app = FastAPI(lifespan=lifespan)


@app.post("/v1/chat/completions")
async def chat_completions(request: dict):
    t0 = time.time()

    messages: list[dict] = request.get("messages", [])
    model: str = request.get("model", DEFAULT_MODEL)
    temperature: float = float(request.get("temperature", 1.0))

    # Last user message for semantic cache
    last_user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user_msg = m.get("content") or ""
            break

    tokens_in = messages_token_count(messages)
    prompt_hash = hashlib.sha256(last_user_msg.encode()).hexdigest()[:16]
    prompt_preview = last_user_msg[:60]

    # 1. Exact cache check
    exact_key = exact_cache_key(model, messages, temperature)
    cached = exact_lookup(exact_key)
    if cached:
        latency_ms = int((time.time() - t0) * 1000)
        resp = json.loads(cached)
        tokens_out = resp.get("usage", {}).get("completion_tokens", 0)
        log_request(model, prompt_hash, prompt_preview, tokens_in, tokens_out, "exact", latency_ms)
        return JSONResponse(content=resp)

    # 2. Semantic cache check
    if len(last_user_msg) > MIN_CHARS_FOR_SEMANTIC:
        sem_hit = semantic_lookup(last_user_msg)
        if sem_hit:
            latency_ms = int((time.time() - t0) * 1000)
            resp = json.loads(sem_hit["response_json"])
            tokens_out = resp.get("usage", {}).get("completion_tokens", 0)
            log_request(model, prompt_hash, prompt_preview, tokens_in, tokens_out, "semantic", latency_ms)
            return JSONResponse(content=resp)

    # 3. Cache miss — call API or mock
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

    # 1-minute buckets, last 60 minutes
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

    return {
        "total_spent_usd": round(row["total_spent_usd"], 6),
        "total_saved_usd": round(row["total_saved_usd"], 6),
        "total_tokens_saved": int(row["total_tokens_saved"] or 0),
        "cache_hit_rate": round(cache_hit_rate, 4),
        "total_requests": total,
        "timeline": timeline,
        "recent_requests": recent_requests,
    }


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
