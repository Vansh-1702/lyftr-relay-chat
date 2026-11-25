# 2:30 walkthrough (record with your voice)

Before recording: deploy both services, publish the GitHub repository, run the smoke script once, and open the deployed frontend plus a terminal with the smoke command typed. Keep hosting Variables and your local `.env` off-screen. If needed, zoom in so the metadata and terminal output are readable.

| Time | Show | Say in your own words |
| --- | --- | --- |
| 0:00–0:20 | Deployed frontend; show its URL and API online indicator. | “This is Relay, a React chat interface backed by FastAPI. It records conversations in SQLite and uses LiteLLM for inference and caching.” |
| 0:20–0:55 | Send a fresh first prompt: “My nickname is Juniper. Acknowledge it in one sentence. Demo <today's time>.” Point to the live label, model, latency and tokens. Start a new chat, paste exactly the same first prompt, and compare the cache hit. | “This first reply was live. In a new session, the model and full prompt match, so we get the exact same text and usage from cache, but in less time.” |
| 0:55–1:10 | In that second session, ask “What nickname did I give you?” | “The frontend sends only the new message. FastAPI loads prior turns from SQLite and passes them to the model.” |
| 1:10–2:10 | Run `python scripts/smoke.py https://YOUR-BACKEND`. Point to `GET /health`, `POST /chat`, and `GET /sessions/.../messages`. Show the failover response, then the 503 and 404. | “This checks all three public endpoints. With `X-Simulate-Failure: key_1=429`, the request returns a real answer through key two and reports one failed attempt. With every key set to fail, the response is a 503, and history is still empty.” |
| 2:10–2:30 | Public GitHub README; briefly show Compose and the SQLite mount. | “The README has reproducible startup and verification steps. Docker uses a named volume for SQLite so history survives restarts. API keys are stored as backend environment variables and never sent to the frontend.” |

Upload the video somewhere the evaluator can view without logging in. Check all four links in a private window before submitting.
