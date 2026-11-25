"""Ordered LiteLLM credential failover and exact-response caching."""
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Callable, Protocol

log = logging.getLogger("lyftr.gateway")


class Unavailable(Exception):
    pass


class InvalidSimulation(ValueError):
    pass


class CacheStore(Protocol):
    def get_cache(self, key: str): ...
    def set_cache(self, key: str, value, **kwargs): ...


@dataclass(frozen=True)
class Answer:
    reply: str
    model: str
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int


class Gateway:
    def __init__(
        self, keys: tuple[str, ...], model: str, timeout: float, max_tokens: int,
        *, complete: Callable | None = None, cache: CacheStore | None = None,
    ):
        self.keys, self.model = keys, model
        self.timeout, self.max_tokens = timeout, max_tokens
        if complete is None:
            from litellm import acompletion
            complete = acompletion
        if cache is None:
            from litellm.caching.in_memory_cache import InMemoryCache
            cache = InMemoryCache(max_size_in_memory=256)
        self.complete, self.cache = complete, cache

    def parse_simulation(self, header: str | None) -> dict[str, int]:
        if header is None:
            return {}
        faults = {}
        allowed = {"*", *(f"key_{i}" for i in range(1, len(self.keys) + 1))}
        for pair in header.split(","):
            key, _, code = pair.strip().partition("=")
            if key not in allowed or key in faults or code not in {"429", "500", "502", "503"}:
                raise InvalidSimulation("Invalid X-Simulate-Failure header")
            faults[key] = int(code)
        return faults

    async def answer(self, messages: list[dict], faults: dict[str, int]) -> Answer:
        identity = json.dumps(
            {"model": self.model, "messages": messages, "max_tokens": self.max_tokens},
            sort_keys=True, ensure_ascii=True, separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        # Fault-injection exercises a real gateway attempt even if this prompt is warm.
        if not faults:
            cached = self.cache.get_cache(fingerprint)
            if cached is not None:
                return Answer(**cached, key_id=None, fallback_attempts=0)

        for index, secret in enumerate(self.keys, start=1):
            key_id = f"key_{index}"
            simulated_status = faults.get(key_id, faults.get("*"))
            if simulated_status is not None:
                log.info("Simulated provider failure: %s (%s)", key_id, simulated_status)
                continue
            try:
                response = await self.complete(
                    model=self.model, messages=messages, api_key=secret, num_retries=0,
                    timeout=self.timeout, max_tokens=self.max_tokens,
                    caching=False,
                )
                reply = response.choices[0].message.content
                usage = response.usage
                input_tokens, output_tokens = usage.prompt_tokens, usage.completion_tokens
                if not isinstance(reply, str) or not reply.strip():
                    raise ValueError("Provider returned no text")
                if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
                    raise ValueError("Provider returned incomplete token counts")
            except Exception as exc:
                # Includes LiteLLM's normalized 429/quota, timeout and 5xx exceptions.
                # Never log exception text: upstream errors can include user or key data.
                log.warning("Provider attempt failed: %s (%s)", key_id, type(exc).__name__)
                continue
            data = dict(reply=reply, model=self.model, input_tokens=input_tokens, output_tokens=output_tokens)
            if not faults:
                self.cache.set_cache(fingerprint, data, ttl=900)
            return Answer(**data, key_id=key_id, fallback_attempts=index - 1)
        raise Unavailable
