"""Client for the Meta Model API (OpenAI-compatible chat completions).

Every call:
  * sends text plus images as data URLs,
  * asks for one JSON object and validates it against a pydantic schema,
  * repairs a malformed reply once, then fails clearly,
  * retries rate limits and server errors with backoff,
  * drops optional parameters (reasoning_effort, response_format) if the API rejects them,
  * is written to the trace log with model, purpose, latency and a cloud/mock tag.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Callable, Iterable, Optional, Type, TypeVar

import httpx
from PIL import Image
from pydantic import BaseModel, ValidationError

from . import images as imgs
from .config import Settings
from .trace import Tracer

T = TypeVar("T", bound=BaseModel)

OPTIONAL_PARAMS = ("reasoning_effort", "response_format")


class MuseError(RuntimeError):
    """A model call failed in a way the user should hear about."""


class _ModelRejected(Exception):
    """The API does not know this model ID; the caller may fall back to another model."""


def extract_json(text: str) -> Any:
    """Pull the first JSON object out of a reply, tolerating code fences and stray prose."""
    if text is None:
        raise ValueError("empty reply")
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("no JSON object in reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : i + 1])
    raise ValueError("unterminated JSON object in reply")


def _to_anthropic_content(content: Any) -> Any:
    """Convert OpenAI-style content parts (text, image_url data URLs) to Messages API parts."""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if part.get("type") == "image_url":
            url = part["image_url"]["url"]
            header, b64 = url.split(",", 1)
            media = header.split(";")[0].removeprefix("data:") or "image/jpeg"
            parts.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}})
        else:
            parts.append({"type": "text", "text": part.get("text", "")})
    return parts


def _message_text(data: dict) -> str:
    """Read the reply text from a chat-completions (or messages-style) response."""
    if "choices" in data and data["choices"]:
        content = data["choices"][0].get("message", {}).get("content")
    else:
        content = data.get("content")
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


class MuseClient:
    def __init__(self, settings: Settings, tracer: Tracer, mock_responder: Optional[Callable[..., dict]] = None):
        self.s = settings
        self.tracer = tracer
        self.mock_responder = mock_responder
        self._dropped: set[str] = set()
        self._fallback: dict[str, str] = {}  # role -> model used after the configured one was rejected
        self._detected_style: Optional[str] = None  # set when auto mode finds chat completions missing
        self._http: Optional[httpx.AsyncClient] = None
        self._day, self._calls_today = "", 0

    def _spend_one_call(self) -> None:
        """Counts calls to the paid API per UTC day and stops at MAX_MODEL_CALLS_PER_DAY."""
        limit = self.s.max_model_calls_per_day
        if limit <= 0:
            return
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self._day:
            self._day, self._calls_today = today, 0
        if self._calls_today >= limit:
            raise MuseError("This ShowMe demo has used today's AI allowance. Please try again tomorrow "
                            "(or run ShowMe on your own computer with your own key, see the README).")
        self._calls_today += 1

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.s.request_timeout)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def model_name(self, role: str) -> str:
        if role in self._fallback:
            return self._fallback[role]
        return {"spark": self.s.spark_model, "wonder": self.s.wonder_model, "sam": self.s.sam_model}[role]

    @property
    def style(self) -> str:
        """openai = /chat/completions, anthropic = /messages. 'auto' starts with openai and switches if missing."""
        if self.s.api_style in ("openai", "anthropic"):
            return self.s.api_style
        return self._detected_style or "openai"

    def _headers(self) -> dict[str, str]:
        if not self.s.api_key:
            raise MuseError("MODEL_API_KEY is not set. Add it to .env, or set APPRENTICE_MOCK=1 to try the app offline.")
        headers = {"Authorization": f"Bearer {self.s.api_key}", "Content-Type": "application/json"}
        if self.style == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
        return headers

    # ------------------------------------------------------------------ public

    async def json(
        self,
        *,
        role: str,
        purpose: str,
        system: str,
        text: str,
        schema: Type[T],
        images: Iterable[Image.Image] = (),
        effort: str = "",
        ctx: Optional[dict] = None,
        mock_args: Optional[dict] = None,
    ) -> T:
        model = self.model_name(role)
        images = list(images)
        if self.s.mock:
            return await self._mock(role, model, purpose, schema, len(images), ctx or {}, mock_args or {})

        content: list[dict] = [{"type": "text", "text": text}]
        for im in images:
            url = imgs.to_data_url(imgs.fit(im, self.s.max_image_side))
            content.append({"type": "image_url", "image_url": {"url": url}})
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]

        try:
            reply_text = await self._chat(model, purpose, messages, effort, len(images), len(text), ctx or {})
        except _ModelRejected as exc:
            # Spark's jobs fall back to Wonder and Wonder's to Spark, so one unavailable model doesn't stop the app.
            fallback = self.s.wonder_model if role == "spark" else self.s.spark_model
            if role not in ("spark", "wonder") or model == fallback:
                raise MuseError(f"The API does not accept the model ID {model}: {exc}. Check {role.upper()}_MODEL in .env.") from exc
            self._fallback[role] = fallback  # remembered for the rest of this run
            model = fallback
            reply_text = await self._chat(model, purpose, messages, effort, len(images), len(text), ctx or {})
        try:
            return schema.model_validate(extract_json(reply_text))
        except (ValueError, ValidationError) as first_error:
            repair = (
                "Your last reply could not be used: "
                f"{str(first_error)[:400]}. Reply again with only the JSON object, in the exact shape requested."
            )
            messages += [{"role": "assistant", "content": reply_text}, {"role": "user", "content": repair}]
            reply_text = await self._chat(model, purpose + ":repair", messages, effort, 0, len(repair), ctx or {})
            try:
                return schema.model_validate(extract_json(reply_text))
            except (ValueError, ValidationError) as second_error:
                raise MuseError(f"{model} did not return usable JSON for {purpose}: {str(second_error)[:300]}") from second_error

    async def post_raw(self, path: str, body: dict, *, purpose: str, model: str, ctx: Optional[dict] = None) -> dict:
        """POST any JSON body to the Meta Model API (used for SAM 3.1), traced like chat calls."""
        url = self.s.base_url + path
        started = time.perf_counter()
        self._spend_one_call()
        try:
            resp = await self.http.post(url, headers=self._headers(), json=body)
            data = resp.json() if resp.content else {}
            ok = resp.status_code < 400
            self._trace(model, purpose, "cloud", started, ok, resp.status_code, resp, data, 1, 0, 0, ctx or {},
                        None if ok else str(data)[:300])
            if not ok:
                raise MuseError(f"{model} {path} returned {resp.status_code}: {str(data)[:300]}")
            return data
        except httpx.HTTPError as exc:
            self._trace(model, purpose, "cloud", started, False, None, None, {}, 1, 0, 0, ctx or {}, repr(exc))
            raise MuseError(f"Could not reach {url}: {exc}") from exc

    # ------------------------------------------------------------------ internals

    def _body(self, model: str, messages: list[dict], effort: str) -> tuple[str, dict[str, Any]]:
        if self.style == "anthropic":
            system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
            converted = [{"role": m["role"], "content": _to_anthropic_content(m["content"])}
                         for m in messages if m["role"] != "system"]
            body = {"model": model, "system": system, "messages": converted, "max_tokens": self.s.max_tokens}
            return self.s.base_url + self.s.messages_path, body
        body = {"model": model, "messages": messages}
        if effort and "reasoning_effort" not in self._dropped:
            body["reasoning_effort"] = effort
        if self.s.json_mode and "response_format" not in self._dropped:
            body["response_format"] = {"type": "json_object"}
        return self.s.base_url + self.s.chat_path, body

    async def _chat(self, model, purpose, messages, effort, n_images, n_chars, ctx) -> str:
        attempt, backoff = 0, 1.5
        while True:
            url, body = self._body(model, messages, effort)
            started = time.perf_counter()
            self._spend_one_call()
            try:
                resp = await self.http.post(url, headers=self._headers(), json=body)
            except httpx.HTTPError as exc:
                self._trace(model, purpose, "cloud", started, False, None, None, {}, n_images, n_chars, 0, ctx, repr(exc))
                attempt += 1
                if attempt > 3:
                    raise MuseError(f"Could not reach the Meta Model API at {url}: {exc}") from exc
                await asyncio.sleep(backoff ** attempt)
                continue

            try:
                data = resp.json()
            except ValueError:
                data = {"raw": resp.text[:500]}

            if resp.status_code == 400:
                rejected = [p for p in OPTIONAL_PARAMS if p in body and p in json.dumps(data)]
                if rejected:
                    self._dropped.update(rejected)
                    self._trace(model, purpose, "cloud", started, False, 400, resp, data, n_images, n_chars, 0, ctx,
                                f"dropped unsupported parameter(s): {', '.join(rejected)}")
                    continue
            blob = json.dumps(data).lower()
            if resp.status_code in (400, 404) and "model" in blob and any(
                    w in blob for w in ("model_not_found", "not found", "does not exist", "unknown model", "invalid model",
                                        "unsupported model", "no such model")):
                self._trace(model, purpose, "cloud", started, False, resp.status_code, resp, data, n_images, n_chars, 0, ctx,
                            f"model {model} rejected")
                raise _ModelRejected(str(data)[:200])
            if resp.status_code in (404, 405) and self.s.api_style == "auto" and self.style == "openai":
                self._detected_style = "anthropic"
                self._trace(model, purpose, "cloud", started, False, resp.status_code, resp, data, n_images, n_chars, 0, ctx,
                            "chat completions not available; switching to the Messages API")
                continue
            if resp.status_code in (401, 403):
                self._trace(model, purpose, "cloud", started, False, resp.status_code, resp, data, n_images, n_chars, 0, ctx, "auth")
                raise MuseError("The Meta Model API rejected the key (HTTP %d). Check MODEL_API_KEY in .env." % resp.status_code)
            if resp.status_code == 429 or resp.status_code >= 500:
                self._trace(model, purpose, "cloud", started, False, resp.status_code, resp, data, n_images, n_chars, 0, ctx, "retrying")
                attempt += 1
                if attempt > 3:
                    raise MuseError(f"{model} kept failing with HTTP {resp.status_code}: {str(data)[:300]}")
                retry_after = resp.headers.get("retry-after")
                await asyncio.sleep(float(retry_after) if retry_after and retry_after.isdigit() else backoff ** attempt)
                continue
            if resp.status_code >= 400:
                self._trace(model, purpose, "cloud", started, False, resp.status_code, resp, data, n_images, n_chars, 0, ctx, str(data)[:300])
                raise MuseError(f"{model} returned HTTP {resp.status_code} for {purpose}: {str(data)[:300]}")

            text = _message_text(data)
            self._trace(model, purpose, "cloud", started, True, resp.status_code, resp, data, n_images, n_chars, len(text), ctx, None)
            return text

    async def _mock(self, role, model, purpose, schema, n_images, ctx, mock_args):
        if self.mock_responder is None:
            raise MuseError("Mock mode is on but no mock responder is installed.")
        started = time.perf_counter()
        await asyncio.sleep(0.05)
        payload = self.mock_responder(purpose, **mock_args)
        result = schema.model_validate(payload)
        self._trace(model, purpose, "mock", started, True, 200, None, {"model": model}, n_images, 0, 0, ctx, None)
        return result

    def _trace(self, model, purpose, tag, started, ok, status, resp, data, n_images, n_in, n_out, ctx, error):
        usage = data.get("usage") if isinstance(data, dict) else None
        request_id = None
        if resp is not None:
            request_id = resp.headers.get("x-request-id") or resp.headers.get("x-fb-request-id")
        if not request_id and isinstance(data, dict):
            request_id = data.get("id")
        self.tracer.record(
            model=(data.get("model") if isinstance(data, dict) and data.get("model") else model),
            purpose=purpose,
            tag=tag,
            ok=ok,
            status=status,
            latency_ms=round((time.perf_counter() - started) * 1000),
            images=n_images,
            input_chars=n_in,
            output_chars=n_out,
            usage=usage,
            request_id=request_id,
            error=error,
            **{k: v for k, v in ctx.items() if v is not None},
        )
