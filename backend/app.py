from asyncio import Lock
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import os
from time import perf_counter
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints
from starlette.concurrency import run_in_threadpool

from gateway import Gateway, InvalidSimulation, Unavailable
from storage import Storage

log = logging.getLogger("lyftr.api")
SessionId = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"\S")]
Prompt = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ChatInput(BaseModel):
    session_id: SessionId
    message: Prompt
    system_prompt: Prompt | None = None


@dataclass(frozen=True)
class Settings:
    keys: tuple[str, ...]
    database_url: str = "sqlite:///./data/app.db"
    origins: tuple[str, ...] = ("http://localhost:3000",)
    simulation: bool = False
    model: str = "groq/openai/gpt-oss-20b"
    system_prompt: str = "You are a helpful assistant. Prefer clear, concise answers."
    timeout: float = 12
    max_tokens: int = 1024

    @classmethod
    def from_env(cls):
        split = lambda var, default="": tuple(
            part.strip() for part in os.getenv(var, default).split(",") if part.strip()
        )
        return cls(
            keys=split("LLM_API_KEYS"),
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            origins=split("ALLOWED_ORIGINS", cls.origins[0]),
            simulation=os.getenv("ENABLE_FAILURE_SIMULATION", "false").lower() == "true",
            model=os.getenv("LLM_MODEL", cls.model),
            system_prompt=os.getenv("DEFAULT_SYSTEM_PROMPT", cls.system_prompt),
            timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", str(cls.timeout))),
            max_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", str(cls.max_tokens))),
        )


def create_app(settings: Settings | None = None, gateway: Gateway | None = None):
    settings = settings or Settings.from_env()
    store = Storage(settings.database_url)

    @asynccontextmanager
    async def lifespan(api):
        if len(set(settings.keys)) < 2 or any(key.startswith("replace-me") for key in settings.keys):
            raise RuntimeError("LLM_API_KEYS must contain at least two distinct provider keys.")
        await run_in_threadpool(store.initialize)
        api.state.gateway = gateway or Gateway(
            settings.keys, settings.model, settings.timeout, settings.max_tokens
        )
        # A bounded set of locks prevents turn reordering under concurrent sends.
        api.state.locks = [Lock() for _ in range(128)]
        yield

    api = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    api.add_middleware(
        CORSMiddleware, allow_origins=list(settings.origins), allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Simulate-Failure"],
    )

    @api.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": [
                {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
                for error in exc.errors()
            ]},
        )

    @api.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        log.error("Application failure: %s", type(exc).__name__)
        headers = {}
        origin = request.headers.get("origin")
        if origin and origin in settings.origins:
            headers = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
        return JSONResponse(status_code=500, content={"detail": "internal server error"}, headers=headers)

    @api.get("/health")
    async def health(request: Request):
        await run_in_threadpool(store.ping)
        return {"status": "ok", "keys_configured": len(request.app.state.gateway.keys), "cache_enabled": True}

    @api.get("/sessions/{session_id}/messages")
    async def history(session_id: Annotated[str, Path(min_length=1, max_length=128, pattern=r"\S")]):
        _, stored = await run_in_threadpool(store.read, session_id)
        if not stored:
            raise HTTPException(404, "session not found")
        return {"session_id": session_id, "messages": stored}

    @api.post("/chat")
    async def chat(payload: ChatInput, request: Request,
                   simulation: Annotated[str | None, Header(alias="X-Simulate-Failure")] = None):
        started = perf_counter()
        model_gateway = request.app.state.gateway
        try:
            faults = model_gateway.parse_simulation(simulation) if settings.simulation else {}
        except InvalidSimulation as exc:
            raise HTTPException(422, str(exc)) from None
        locks = request.app.state.locks
        async with locks[hash(payload.session_id) % len(locks)]:
            previous_prompt, stored = await run_in_threadpool(store.read, payload.session_id)
            prompt = payload.system_prompt or previous_prompt or settings.system_prompt
            context = [
                {"role": "system", "content": prompt},
                *({"role": item["role"], "content": item["content"]} for item in stored),
                {"role": "user", "content": payload.message},
            ]
            try:
                result = await model_gateway.answer(context, faults)
            except Unavailable:
                raise HTTPException(503, "all providers unavailable") from None
            metadata = {
                "resolution_source": "LLM_CACHE" if result.key_id is None else "LIVE_INFERENCE",
                "model": result.model,
                "latency_ms": round((perf_counter() - started) * 1000),
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "key_id": result.key_id,
                "fallback_attempts": result.fallback_attempts,
            }
            await run_in_threadpool(
                store.save_turn, payload.session_id, prompt, payload.message, result.reply, metadata
            )
        return {"session_id": payload.session_id, "reply": result.reply, "metadata": metadata}

    return api


app = create_app()
