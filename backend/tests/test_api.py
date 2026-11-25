import asyncio
import copy
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from app import Settings, create_app
from gateway import Gateway

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


class TestCache:
    """Test double for environments without LiteLLM; not used by the application."""
    def __init__(self):
        self.items = {}

    def get_cache(self, key):
        return self.items.get(key)

    def set_cache(self, key, value, **kwargs):
        self.items[key] = value


class ProviderError(Exception):
    def __init__(self, status):
        self.status_code = status


class APIContract(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(TemporaryDirectory())
        self.settings = Settings(
            ("test-secret-1", "test-secret-2"),
            database_url=f"sqlite:///{Path(temp) / 'test.db'}", simulation=True,
            model="test/model"
        )
        self.calls = []
        self.fail = {}
        self.c = self.client()

    def client(self, settings=None):
        settings = settings or self.settings

        async def complete(**options):
            self.calls.append(copy.deepcopy(options))
            key = options["api_key"]
            if key in self.fail:
                raise ProviderError(self.fail[key])
            await asyncio.sleep(0.04)
            messages = options["messages"]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=f"reply to {messages[-1]['content']}"))],
                usage=SimpleNamespace(prompt_tokens=sum(len(m["content"].split()) for m in messages),
                                      completion_tokens=3),
            )

        fake = TestCache() if os.getenv("LYFTR_TEST_CACHE_DOUBLE") == "1" else None
        gateway = Gateway(settings.keys, settings.model, settings.timeout, settings.max_tokens,
                          complete=complete, cache=fake)
        return self.enterContext(TestClient(create_app(settings, gateway)))

    def send(self, sid, message="What is 2+2?", header=None, **kwargs):
        headers = {} if header is None else {"X-Simulate-Failure": header}
        return self.c.post("/chat", json={"session_id": sid, "message": message, **kwargs}, headers=headers)

    def test_health(self):
        self.assertEqual(self.c.get("/health").json(),
                         {"status": "ok", "keys_configured": 2, "cache_enabled": True})

    def test_live_cache_different_messages_and_per_request_usage(self):
        live = self.send("s1").json()
        hit = self.send("s2").json()
        different = self.send("s3", "What is 3+3?").json()
        live_m, hit_m, diff_m = [body["metadata"] for body in (live, hit, different)]
        self.assertEqual(live_m["resolution_source"], "LIVE_INFERENCE")
        self.assertEqual(hit_m["resolution_source"], "LLM_CACHE")
        self.assertEqual(diff_m["resolution_source"], "LIVE_INFERENCE")
        self.assertEqual(hit["reply"], live["reply"])
        self.assertEqual(hit["session_id"], "s2")
        self.assertEqual(hit_m["input_tokens"], live_m["input_tokens"])
        self.assertEqual(hit_m["output_tokens"], live_m["output_tokens"])
        self.assertEqual(diff_m["input_tokens"], live_m["input_tokens"])
        self.assertEqual(hit_m["fallback_attempts"], 0)
        self.assertIsNone(hit_m["key_id"])
        self.assertEqual(live_m["key_id"], "key_1")
        self.assertEqual(live_m["model"], "test/model")
        self.assertIs(type(hit_m["latency_ms"]), int)
        self.assertLess(hit_m["latency_ms"], live_m["latency_ms"])
        self.assertEqual(len(self.calls), 2)

    def test_warm_cache_does_not_hide_simulated_failure(self):
        self.send("warm")
        for status in (429, 500, 502, 503):
            with self.subTest(status=status):
                response = self.send(f"failover-{status}", header=f"key_1={status}")
                self.assertEqual(response.status_code, 200)
                metadata = response.json()["metadata"]
                self.assertEqual(metadata["resolution_source"], "LIVE_INFERENCE")
                self.assertEqual(metadata["key_id"], "key_2")
                self.assertEqual(metadata["fallback_attempts"], 1)
                self.assertEqual(self.calls[-1]["api_key"], "test-secret-2")
                self.assertNotIn("test-secret", response.text)
        self.assertEqual(self.send("normal").json()["metadata"]["resolution_source"], "LLM_CACHE")

    def test_regular_provider_errors_fail_over(self):
        for status in (402, 429, 500, 502, 503):
            with self.subTest(status=status):
                self.fail["test-secret-1"] = status
                response = self.send(f"ordinary-{status}", f"fresh prompt {status}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["metadata"]["key_id"], "key_2")
                self.assertEqual(response.json()["metadata"]["fallback_attempts"], 1)
                self.assertEqual(self.calls[-2]["api_key"], "test-secret-1")
                self.assertEqual(self.calls[-1]["api_key"], "test-secret-2")

    def test_no_writes_on_wildcard_or_multiple_failures(self):
        self.send("existing")
        for sid, header in (("existing", "*=429"), ("new", "key_1=429,key_2=500")):
            response = self.send(sid, header=header)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"detail": "all providers unavailable"})
        self.assertEqual(len(self.c.get("/sessions/existing/messages").json()["messages"]), 2)
        self.assertEqual(self.c.get("/sessions/new/messages").status_code, 404)
        self.assertEqual(len(self.calls), 1)

    def test_normal_all_provider_failure(self):
        self.fail.update({"test-secret-1": 429, "test-secret-2": 503})
        self.assertEqual(self.send("never-written").status_code, 503)
        self.assertEqual(self.c.get("/sessions/never-written/messages").status_code, 404)

    def test_request_validation(self):
        payloads = [{}, {"session_id": "s"}, {"session_id": "", "message": "x"},
                    {"session_id": "   ", "message": "x"}, {"session_id": "a"*129, "message": "x"},
                    {"session_id": "s", "message": "  "}, {"session_id": "s", "message": "a"*4001}]
        for payload in payloads:
            response = self.c.post("/chat", json=payload)
            self.assertEqual(response.status_code, 422)
            self.assertIn("detail", response.json())
        malformed = self.c.post("/chat", content="{", headers={"Content-Type": "application/json"})
        self.assertEqual(malformed.status_code, 422)
        self.assertEqual(self.send("s", header="key_1=403").status_code, 422)
        self.assertEqual(self.c.get("/sessions/s/messages").json(), {"detail": "session not found"})
        self.assertEqual(self.calls, [])

    def test_multiturn_prompt_and_restart(self):
        self.send("s", "  Call me Alice.  ", system_prompt="Keep answers short.")
        restarted = self.client()
        response = restarted.post("/chat", json={"session_id": "s", "message": "What is my name?"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[-1]["messages"], [
            {"role": "system", "content": "Keep answers short."},
            {"role": "user", "content": "Call me Alice."},
            {"role": "assistant", "content": "reply to Call me Alice."},
            {"role": "user", "content": "What is my name?"}
        ])
        self.assertGreater(response.json()["metadata"]["input_tokens"],
                           self.c.get("/sessions/s/messages").json()["messages"][1]["metadata"]["input_tokens"])
        self.send("isolated", "What is my name?")
        self.assertEqual(len(self.calls[-1]["messages"]), 2)
        history = restarted.get("/sessions/s/messages").json()
        self.assertEqual(history["session_id"], "s")
        self.assertEqual([m["role"] for m in history["messages"]], ["user", "assistant", "user", "assistant"])
        self.assertNotIn("Keep answers short.", str(history))
        self.assertNotIn("metadata", history["messages"][0])
        self.assertEqual(history["messages"][-1]["metadata"], response.json()["metadata"])

    def test_full_context_and_system_prompt_are_in_cache_key(self):
        self.send("s1", "same question", system_prompt="prompt A")
        one = self.send("s2", "same question", system_prompt="prompt B").json()
        self.assertEqual(one["metadata"]["resolution_source"], "LIVE_INFERENCE")
        two = self.send("s3", "same question", system_prompt="prompt A").json()
        self.assertEqual(two["metadata"]["resolution_source"], "LLM_CACHE")
        three = self.send("s1", "same question", system_prompt="prompt A").json()
        self.assertEqual(three["metadata"]["resolution_source"], "LIVE_INFERENCE")
        model_b = self.client(replace(self.settings, model="test/other"))
        result = model_b.post("/chat", json={"session_id": "s4", "message": "same question",
                                            "system_prompt": "prompt A"}).json()
        self.assertEqual(result["metadata"]["resolution_source"], "LIVE_INFERENCE")
        self.assertEqual(result["metadata"]["model"], "test/other")

    def test_simulation_off(self):
        c = self.client(replace(self.settings, simulation=False))
        result = c.post("/chat", json={"session_id": "s", "message": "Hi"}, headers={"X-Simulate-Failure": "*=429"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["metadata"]["key_id"], "key_1")

    def test_cors_configuration(self):
        result = self.c.options("/chat", headers={
            "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["Access-Control-Allow-Origin"], "http://localhost:3000")


if __name__ == "__main__":
    unittest.main()
