"""
Thin wrapper around OrcaRouter's OpenAI-compatible API
(https://api.orcarouter.ai/v1, Bearer auth, model ids like
"openai/gpt-4o-mini" / "anthropic/claude-opus-4.7").

`complete_json` tries the cheap model first, and only pays for the strong
model when the cheap one is unsure, fails to return valid JSON, or errors
out. Every attempt is returned to the caller so it can be written to the
audit log — nothing here is a silent retry.
"""
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Type, TypeVar

from openai import OpenAI, APIError, APIConnectionError, APITimeoutError, RateLimitError
from pydantic import BaseModel, ValidationError

from app.config import settings

# Transient errors are worth retrying against the *same* model a couple of
# times (network blip, momentary rate limit) before we give up on it and
# escalate to the next model tier — escalation is for "this model can't
# handle this input", not for "the network hiccuped once".
RETRYABLE_EXCEPTIONS = (APIConnectionError, APITimeoutError, RateLimitError)
MAX_RETRIES_PER_MODEL = 2
BASE_BACKOFF_SECONDS = 0.5

logger = logging.getLogger("kanjoai.orcarouter")

T = TypeVar("T", bound=BaseModel)

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.orcarouter_api_key,
            base_url=settings.orcarouter_base_url,
        )
    return _client


@dataclass
class LLMAttempt:
    model: str
    ok: bool
    raw_text: str = ""
    error: str = ""


@dataclass
class LLMResult:
    """Everything the caller needs to log to the audit trail."""
    parsed: Optional[BaseModel]
    attempts: list = field(default_factory=list)  # list[LLMAttempt]
    final_model: Optional[str] = None
    escalated: bool = False


def _dry_run_stub(schema: Type[T]) -> T:
    """Deterministic stand-in response used when DRY_RUN=true, so the
    whole pipeline (and a demo) works before a real OrcaRouter key exists.
    """
    stub = {
        "vendor_name": "Sample Vendor K.K.",
        "invoice_number": "INV-2026-0913",
        "po_number": "PO-1001",
        "amount": 128000.0,
        "currency": "JPY",
        "invoice_date": "2026-09-15",
        "confidence": 0.9,
        "notes": "DRY_RUN stub — no OrcaRouter call was made.",
    }
    return schema.model_validate(stub)


def _call_model(model: str, messages: list, image_url: Optional[str] = None) -> LLMAttempt:
    if settings.dry_run:
        return LLMAttempt(model=model, ok=True, raw_text="{}")

    client = _get_client()
    if image_url:
        # Vision-style content block, OpenAI-compatible format.
        messages = [
            {
                "role": m["role"],
                "content": [
                    {"type": "text", "text": m["content"]},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ] if m["role"] == "user" else m["content"],
            }
            for m in messages
        ]

    last_error = ""
    for retry_num in range(MAX_RETRIES_PER_MODEL + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=30,
            )
            text = resp.choices[0].message.content or ""
            return LLMAttempt(model=model, ok=True, raw_text=text)
        except RETRYABLE_EXCEPTIONS as e:
            last_error = str(e)
            if retry_num < MAX_RETRIES_PER_MODEL:
                backoff = BASE_BACKOFF_SECONDS * (2 ** retry_num)
                logger.warning(
                    "Transient error calling model=%s (attempt %d/%d): %s — retrying in %.1fs",
                    model, retry_num + 1, MAX_RETRIES_PER_MODEL + 1, e, backoff,
                )
                time.sleep(backoff)
                continue
            logger.warning("model=%s exhausted retries after transient errors: %s", model, e)
        except APIError as e:
            # Non-transient API error (bad request, auth, model doesn't
            # exist, content filtered, ...) — retrying won't help; fail
            # fast so the caller can escalate to a different model.
            logger.warning("OrcaRouter call failed for model=%s: %s", model, e)
            return LLMAttempt(model=model, ok=False, error=str(e))
        except Exception as e:  # anything else unexpected
            logger.warning("Unexpected error calling model=%s: %s", model, e)
            return LLMAttempt(model=model, ok=False, error=str(e))

    return LLMAttempt(model=model, ok=False, error=f"transient error after retries: {last_error}")


def complete_json(
    system_prompt: str,
    user_prompt: str,
    schema: Type[T],
    image_url: Optional[str] = None,
    cheap_model: Optional[str] = None,
    strong_model: Optional[str] = None,
) -> LLMResult:
    """Ask a model for JSON matching `schema`. Tries the cheap model first;
    escalates to the strong model if the cheap model errors, returns
    invalid JSON, or reports confidence below the configured threshold.
    This escalate-on-uncertainty behaviour is the agent's core autonomy
    loop, not a single classification call.
    """
    cheap_model = cheap_model or settings.model_cheap
    strong_model = strong_model or settings.model_strong
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    result = LLMResult(parsed=None)

    if settings.dry_run:
        stub = _dry_run_stub(schema)
        result.attempts.append(LLMAttempt(model=cheap_model, ok=True, raw_text="dry-run"))
        result.parsed = stub
        result.final_model = cheap_model
        return result

    for attempt_num, model in enumerate([cheap_model, strong_model]):
        attempt = _call_model(model, messages, image_url=image_url)
        result.attempts.append(attempt)

        if not attempt.ok:
            continue  # network/API failure -> escalate

        try:
            data = json.loads(attempt.raw_text)
            parsed = schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            attempt.ok = False
            attempt.error = f"schema validation failed: {e}"
            continue  # malformed output -> escalate

        confidence = getattr(parsed, "confidence", 1.0)
        if confidence >= settings.confidence_escalation_threshold or attempt_num == 1:
            result.parsed = parsed
            result.final_model = model
            result.escalated = attempt_num > 0
            return result
        # else: cheap model was unsure -> fall through to strong model

    return result
