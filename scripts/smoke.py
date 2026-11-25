#!/usr/bin/env python3
"""Exercise the deployed API contract. Makes a few small live model requests."""
import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
tag = uuid4().hex[:12]


def request(path, data=None, simulation=None, expected=200):
    headers = {} if data is None else {"Content-Type": "application/json"}
    if simulation:
        headers["X-Simulate-Failure"] = simulation
    req = Request(base + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
    try:
        res = urlopen(req, timeout=65)
    except HTTPError as err:
        res = err
    with res:
        body = json.load(res)
        if res.status != expected:
            raise RuntimeError(f"{path}: expected {expected}, got {res.status}: {body}")
    print(f"{res.status} {path} {json.dumps(body, ensure_ascii=False)}", flush=True)
    return body


def chat(suffix, message, simulation=None, expected=200):
    return request("/chat", {"session_id": f"demo-{tag}-{suffix}", "message": message},
                   simulation=simulation, expected=expected)


health = request("/health")
assert health["status"] == "ok" and health["keys_configured"] >= 2 and health["cache_enabled"] is True

question = f"My nickname for this test is Juniper. Acknowledge it in one short sentence. Test ID: {tag}."
live = chat("live", question)
cached = chat("cached", question)
lm, cm = live["metadata"], cached["metadata"]
assert lm["resolution_source"] == "LIVE_INFERENCE" and lm["key_id"] == "key_1"
assert cm["resolution_source"] == "LLM_CACHE" and cm["key_id"] is None and cm["fallback_attempts"] == 0
assert cached["reply"] == live["reply"]
assert (cm["input_tokens"], cm["output_tokens"]) == (lm["input_tokens"], lm["output_tokens"])
assert cm["latency_ms"] < lm["latency_ms"]

followup = chat("live", "What nickname did I just give you? Reply using just that word.")
assert "juniper" in followup["reply"].lower()
assert followup["metadata"]["resolution_source"] == "LIVE_INFERENCE"
history = request(f"/sessions/demo-{tag}-live/messages")
assert [entry["role"] for entry in history["messages"]] == ["user", "assistant", "user", "assistant"]
assert history["messages"][-1]["metadata"] == followup["metadata"]

failover = chat("failover", question, simulation="key_1=429")["metadata"]
assert failover["resolution_source"] == "LIVE_INFERENCE" and failover["key_id"] == "key_2"
assert failover["fallback_attempts"] == 1
assert chat("outage", question, simulation="*=429", expected=503) == {"detail": "all providers unavailable"}
assert request(f"/sessions/demo-{tag}-outage/messages", expected=404) == {"detail": "session not found"}
request("/chat", {"session_id": "smoke-invalid", "message": " "}, expected=422)
print("Passed: health, warm cache, multi-turn, history, failover, outage and validation.")
