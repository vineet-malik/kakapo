# Kakapo

Kakapo is a voice-first **agentic IDE companion** (see [Concept](./docs/CONCEPT.md) and [Design](./docs/DESIGN.md)). This repository currently includes a **runnable prototype** you can share with the team: an LLM proxy with **exact + semantic caching**, a **chat demo**, and a **live dashboard** that shows cache hits, token savings, and estimated dollar savings.

---

## What is in this repo?

| Path | Purpose |
|------|---------|
| [`src/backend/proxy.py`](./src/backend/proxy.py) | FastAPI server: OpenAI-compatible `/v1/chat/completions`, SQLite request log, FAISS semantic cache, exact cache |
| [`src/backend/demo.html`](./src/backend/demo.html) | Split UI: chat on the left, embedded dashboard on the right |
| [`src/backend/dashboard.html`](./src/backend/dashboard.html) | Metrics + recent activity (polls `/api/stats` every 2s) |
| [`src/backend/seed_cache.py`](./src/backend/seed_cache.py) | Optional: pre-fill caches and sample `requests` rows (run **while proxy is stopped**) |
| [`docs/`](./docs/) | Product and architecture notes |

---

## Run it locally

### Prerequisites

- **Python 3.10+** (3.11+ recommended)
- **Network** on first run: installs PyTorch / Sentence Transformers and downloads the embedding model from Hugging Face (~hundreds of MB the first time).
- A **Google AI Studio API key** if you want real Gemini responses (free tier; model availability varies by project).

### 1. Clone and enter the backend

```bash
git clone <YOUR_REPO_URL> kakapo
cd kakapo/src/backend
```

### 2. Virtual environment and dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. API key (live mode)

Create a key in [Google AI Studio](https://aistudio.google.com/apikey), then export it (the variable name matches the OpenAI client convention; this project talks to Gemini’s OpenAI-compatible endpoint):

```bash
export OPENAI_API_KEY="your-key-here"
```

Copy [`src/backend/.env.example`](./src/backend/.env.example) as a personal checklist; the app does **not** auto-load `.env` files unless you use a shell hook or a process manager that injects them.

### 4. (Optional) Seed demo data

**Option A — full seed (recommended for a polished first open):** pre-populates exact + semantic caches with 15 curated Q&A pairs and inserts sample rows so the dashboard is not empty on first paint. **Stop the server** before running:

```bash
python3 seed_cache.py
```

**Option B — minimal seed on server start:** starts the proxy and seeds a smaller built-in Q&A set into the caches (no fake dashboard history):

```bash
python3 proxy.py --seed-cache
```

### 5. Start the server

From **`src/backend`** (so `dashboard.html`, `demo.html`, and `proxy.db` resolve correctly):

```bash
python3 proxy.py
# optional: bind another port
# python3 proxy.py --port 8080
```

Default URL: **http://127.0.0.1:8000** (the server listens on `0.0.0.0`, so teammates on the same LAN can use your machine’s IP and port **if** your firewall allows it).

| URL | What you get |
|-----|----------------|
| http://127.0.0.1:8000/demo | Chat + embedded dashboard |
| http://127.0.0.1:8000/dashboard | Dashboard only |
| http://127.0.0.1:8000/docs | FastAPI auto-generated API docs |

For **remote** teammates outside your network, use screen sharing, an internal tunnel (for example [ngrok](https://ngrok.com/) `ngrok http 8000`), or deploy this service to a small cloud VM—same commands, different host.

### No API key? Mock mode

For UI-only testing without calling Gemini:

```bash
export MOCK_MODE=true
python3 proxy.py
```

Responses are synthetic; latency is simulated.

---

## How the demo works (flow + examples)

Think of the proxy as sitting **between** your browser and the model. Every chat request goes through **exact cache → semantic cache → upstream model** (on miss).

### Exact cache

The server hashes the **last user message**, **model name**, and **temperature** (not the full chat transcript). So if the user sends the same text again—even after the assistant has replied—the key can still match and you get an **exact hit** (very low latency, no network call).

**Try it**

1. Open **Demo** (`/demo`).
2. Type `hello` and send → first time is usually a **miss** (real API call). Note latency and “FRESH” under the reply.
3. Send `hello` again → **exact hit**: response is instant, badge shows cached behavior, dashboard **Recent activity** shows a hit with **tokens saved** and non-zero **Saved** (estimated vs a GPT-4o–priced baseline, only credited on cache hits).

### Semantic cache

Longer prompts are embedded with `sentence-transformers/all-MiniLM-L6-v2` and matched in FAISS. If a new prompt is **close enough** to a stored one (similarity above an internal threshold), you get a **semantic hit** without calling the model.

**Try it**

1. Ask a detailed question, wait for the full answer.
2. Rephrase the same intent in different words. If similarity is high enough, you may see a semantic hit; if not, you get a miss and the new answer is cached for the future.

### Seeded questions (after `seed_cache.py`)

If you ran the seeder, questions such as **“How do I reset my password?”** may resolve from cache immediately, so the team sees green “hit” rows without typing a long prompt first.

### Model dropdown (routing demo)

In the demo’s input row, pick another **Gemini** model ID and send. The dashboard’s **Model** column reflects what was requested/logged. Different models help show that routing and logging are per-model; cache keys include the model name, so the same text with two models is two different cache entries.

### Reading the dashboard

- **Recent activity** (top): each row is one completed request—time, model, prompt snippet, **hit vs miss**, **tokens saved** (non-zero only when the cache avoided a model call), and **Saved** (dollar estimate of that avoidance vs the GPT-4o baseline).
- **Total saved today**: sum of those per-request savings.
- **Cache hit rate**: hits ÷ total requests in the log.

---

## Troubleshooting

| Issue | What to try |
|-------|-------------|
| `429` or quota errors from Gemini | In AI Studio, check which models have quota on your key; set `DEFAULT_MODEL` to a model that still allows free-tier calls (e.g. `gemini-flash-latest`), or pick another option in the demo dropdown. |
| Slow first startup | SentenceTransformer and FAISS load on startup; first Hugging Face download can take several minutes on a slow link. |
| `seed_cache.py` fails with DB locked | Stop `proxy.py` first; both use `proxy.db`. |
| Empty dashboard | Either send a few chat messages, or run `seed_cache.py`, then refresh. |

---

## Status

The **IDE / voice** product described in `docs/` is broader than this slice. The **proxy + demo + dashboard** here is the current **shareable engineering prototype** for token caching, observability, and team demos.
