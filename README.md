# Relay | Lyftr full-stack assignment

A React chat frontend and a FastAPI backend. LiteLLM supplies provider calls and exact response caching; SQLite stores completed turns.

## Live URLs

| Deliverable | Status |
| --- | --- |
| Frontend | Not published yet |
| Backend | Not published yet |
| Public GitHub repository | Not published yet |
| Demo (2–3 minutes, with candidate narration) | Not recorded yet |

Replace the status entries with verified public URLs before submitting. [Official submission form](https://docs.google.com/forms/d/e/1FAIpQLSfaPS5_NliZuEjhYXppeafBrNM9u0hp-q-vT8vDuv9GU-0THA/viewform).

### Publish in this order

1. **GitHub:** Extract the archive. Open a terminal inside its top-level project folder. Create a public repository and push the project. Example with the authenticated [GitHub CLI](https://cli.github.com/):
   ```sh
   git init -b main
   git add .
   git diff --cached --name-only
   git commit -m "Build Relay resilient chat"
   gh repo create lyftr-relay-chat --public --source=. --remote=origin --push
   ```
   Check that `.env`, SQLite files and provider keys are not staged.
2. **Backend:** In [Railway](https://railway.com/), create a service from the GitHub repo, set its root directory to `/backend` and use its Dockerfile. Attach a volume mounted at `/data`. Set a health check for `/health` and deploy one replica. In Variables, set `LLM_API_KEYS` to two real keys, `LLM_MODEL` to a model available to both, `DATABASE_URL=sqlite:////data/app.db`, and `ENABLE_FAILURE_SIMULATION=true`. Generate a public domain and check `/health`. If an attached Railway volume is not writable to the image user, set `RAILWAY_RUN_UID=0` for this service as documented in [Railway's volume reference](https://docs.railway.com/volumes/reference), then redeploy. Check the plan before enabling any paid resources. An alternative is a [Render Docker service with a persistent disk](https://render.com/docs/disks); a free ephemeral filesystem does not meet the restart requirement.
3. **Frontend:** Import the repo into [Vercel](https://vercel.com/), select the `frontend` root and Vite preset. Set `VITE_API_BASE_URL` to the public backend base URL, with `https://` and no `/chat` suffix. Deploy and make sure the production URL works without sign-in.
4. **Connect:** Set the backend's `ALLOWED_ORIGINS` to the exact public frontend origin (scheme and hostname, no trailing slash). Redeploy the backend and verify both URLs from a browser private window. Check that a previous session remains readable after restarting the backend.
5. **Record and submit:** Run the smoke test against the public backend and follow [DEMO.md](DEMO.md) to record the walkthrough. Add the links to this README, push, and use the official form. Do not share credentials or private prompts in the video.

## Run locally: with Docker and without

**With Docker (Compose v2):** Copy `.env.example` to `.env` and replace the placeholder keys with two distinct keys. Both must support `LLM_MODEL`. Then run:

```sh
make up
docker compose logs backend
```

Open http://localhost:3000 (web) or http://localhost:8000/health (API). Use `make down` to stop both services. This preserves the named SQLite volume. On Windows without GNU Make, `docker compose up --build -d` and `docker compose down` are equivalent.

**Without Docker:** Use Python 3.12 and Node 22.12 or later. From the repo root, create `.env` as above. Set `DATABASE_URL=sqlite:///./data/app.db` for local execution. Copy `.env.example` with `cp` in Bash or `Copy-Item` in PowerShell. From the repo root:

```sh
python -m venv .venv
# Bash:
source .venv/bin/activate
# PowerShell instead: .\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements-dev.txt
python -m uvicorn app:app --app-dir backend --env-file .env --port 8000
```

In a second terminal:

```sh
cd frontend
npm install
npm run dev
```

The dev frontend defaults to `http://localhost:8000`. For a different backend, set the public `VITE_API_BASE_URL` in the build environment or in `frontend/.env.local`.

## Design decisions: key fallback, caching, SQLite session storage

- **Key fallback:** Attempt configured keys in the listed order through LiteLLM's async client. Disable retries within each attempt so the next key gets a turn immediately. A 429, quota failure, 5xx, timeout, rejected credential or unusable provider response moves to the next key. A failed chain returns the contract's JSON 503. Return only the key's opaque label and failover count, never the key itself. Keys that share a quota bucket may all fail together; use independently allocated quotas where permitted by your provider.
- **Caching:** One bounded [LiteLLM in-memory cache](https://github.com/BerriAI/litellm/blob/main/litellm/caching/in_memory_cache.py) retains up to 256 exact replies for 15 minutes. A SHA-256 hash covers the configured model, ordered system/history/new-message list, and output-token limit. Session ID and credentials are not part of the fingerprint. A hit returns the original reply and provider usage, but reports this request's latency, a null key ID and zero failovers. Provider billing for the hit is zero; the displayed usage describes the reused completion. Failure simulation bypasses cache reads and writes to test the specified key path even on a warm prompt.
- **SQLite:** WAL mode, timestamped message rows and a session/ID index. Save both user and assistant rows and the effective system prompt in one transaction, only after a valid completion. Per-session locking on a single worker prevents simultaneous turns from reading the same history. Reads return ordered user and assistant messages without the stored system prompt.
- **Tradeoffs:** This is an unauthenticated evaluation demo, not a production chat service. Use non-sensitive prompts only. Anyone with a session ID can read it. Keep provider budgets and limits low during the demo; CORS is not authentication or rate limiting. For ongoing production use, add authentication, per-user rate and spend limits, and a retention policy, then disable failure simulation. SQLite and the process-local cache deliberately assume one backend replica.

## Verification: how to see failover, cache hits and per-message token usage

```sh
make test
python scripts/smoke.py http://localhost:8000
# Or point to your public HTTPS backend:
python scripts/smoke.py https://YOUR-BACKEND
```

The unit/API suite uses mocked provider responses and LiteLLM's real cache when installed. If this environment cannot install LiteLLM, `LYFTR_TEST_CACHE_DOUBLE=1 make test` can check the API contract with a test double, but does not validate LiteLLM itself. The smoke test requires real keys and does not skip actual inference.

**In the browser:** Start a new chat, send a fresh question and note `LIVE_INFERENCE`, model, latency and input/output tokens. Click **New chat**, then send exactly the same first question with the same system prompt. Look for `LLM_CACHE`, identical text and token counts, and lower latency. In another session, say “My nickname is Juniper” and ask “What is my nickname?” to demonstrate history.

**Manually exercise each endpoint** (use Bash or Git Bash; on PowerShell prefer `curl.exe` and normal PowerShell escaping):

```sh
API=http://localhost:8000
SID="proof-$(date +%s)"
curl -sS "$API/health"
curl -sS -X POST "$API/chat" -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=429' \
  -d "{\"session_id\":\"$SID\",\"message\":\"Say hello in one sentence.\"}"
curl -sS "$API/sessions/$SID/messages"
curl -i -sS -X POST "$API/chat" -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: *=429' \
  -d "{\"session_id\":\"${SID}-outage\",\"message\":\"This must not be saved.\"}"
curl -i -sS "$API/sessions/${SID}-outage/messages"
```

Expect health 200; chat 200 from `key_2` with one failover; history 200 with two rows; all-key outage 503; outage history 404. Change simulation to `key_1=500` for a server-error failover. An empty message returns JSON 422 and does not create a session.

## Setup Used: your editor and AI tools

The source was created in ChatGPT Work with OpenAI's dot and command-line tools. Add the editor you use to review, test, and publish it before submission. The backend uses Python 3.12, FastAPI and LiteLLM; the frontend uses Node 22, React and Vite.

## Bonus features (if any): what you added beyond the requirements and why

An optional per-session system prompt, a one-click **New chat** control, a backend status indicator, and visible key/failover metadata make tests easy to demonstrate. The live smoke script verifies all three endpoints and the error paths without provider credentials on the client.
