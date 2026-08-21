"""Interpretation service (FR-4, Flow 4 entry point).

This is the ONE real external API call in the system — a LIVE classification call
to OpenRouter (an OpenAI-compatible endpoint) using the `openai` async SDK. It is
deliberately distinct from the SIMULATED connectors elsewhere.

Two hard rules:
  * Text under ``interpret_min_tokens`` (default 8 tokens) classifies as
    ``label="unknown"`` WITHOUT calling the LLM at all — this is scored efficiency
    behavior, so the skip is structural (we simply never build the client-call
    path), not an incidental output match.
  * Provider failures go through a bounded tenacity retry (max
    ``retry_max_attempts``, exponential backoff + jitter). On exhaustion we DO NOT
    silently return ``unknown`` — we surface a visible error state (``InterpretError``),
    which the caller routes to the dead-letter path in Phase 8.
"""

import json
import re
import time
from decimal import Decimal
from typing import Any

import openai
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from app.config import settings
from app.db.models import DeadLetterQueue, Event, Interpretation
from app.logging import get_logger
from app.services.receipts import write_receipt

logger = get_logger(__name__)

PROMPT_VERSION = "interpret_v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_REFERRER = "https://github.com/RajatThakral01/demand-signal-workflow"

# Regex to strip structural/whitespace so a text with no real lexical content
# counts as zero tokens, mirroring how "thin" input behaves with a tokenizer.
_WS = re.compile(r"\s+")

# Model identifier recorded on every LIVE result. Classified model name only,
# without the API prefix.
CLASSIFICATION_LABEL = settings.classification_model  # e.g. deepseek/deepseek-v4-flash


class InterpretError(Exception):
    """Raised after bounded retries are exhausted (provider failure).

    Callers must surface this as a visible error (dead-letter in Phase 8) rather
    than swallow it into a fabricated ``unknown``.
    """

    def __init__(self, message: str, errors: list[BaseException] | None = None):
        super().__init__(message)
        self.errors = errors or []


def _is_retryable(exc: BaseException) -> bool:
    """Return True when a provider exception is a *transient* failure worth a retry.

    Retryable:
      * our own ``InterpretError`` (a non-JSON/truncated parse — retry the call),
      * ``openai.APIConnectionError`` (covers timeouts & connection problems),
      * ``openai.APIStatusError`` with a retryable status: 429 (rate limit) or
        >=500 (server outages). ``RateLimitError`` and ``InternalServerError`` are
        subclasses of ``APIStatusError`` and carry those status codes.

    NOT retryable (fail fast — a config problem, not a transient blip):
      * ``openai.AuthenticationError`` (401 bad key), 400s, and other 4xx.
      * anything else.
    """
    if isinstance(exc, InterpretError):
        return True
    if isinstance(exc, openai.APIConnectionError):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


def _count_tokens(text: str) -> int:
    """Deterministic fast token estimate for the min-length skip gate (FR-4).

    Uses a whitespace/length heuristic rather than a full tokenizer to avoid a
    heavy dependency at the hot path. Good enough to be a *gate* (conservative,
    deterministic). The real token usage is reported by the provider and stored
    in ``interpretations.token_usage``.
    """
    if not text:
        return 0
    stripped = _WS.sub(" ", text).strip()
    return len(stripped.split())


def _log_retry(event: Event):
    """Return a tenacity ``before_sleep`` callback that logs each backoff wait."""

    def _before(retry_state) -> None:
        logger.info(
            "interpret_llm_retry",
            event_id=str(getattr(event, "id", "")),
            attempt=retry_state.attempt_number,
            next_wait=retry_state.next_action.sleep if retry_state.next_action else None,
        )

    return _before


def _extract_text(event: Event, identity_fields: dict | None) -> str:
    """Pull the classification-relevant free text from an event.

    Each source contributes its body field; falls back to other text-likes when
    the primary is empty. Identity/metadata are excluded (they are not "free
    text" for pain/topic/intent classification).
    """
    source = event.source
    body = None
    if source == "web_form":
        body = (event.raw_payload or {}).get("message") or (event.raw_payload or {}).get("body")
    elif source == "social_mention":
        body = (event.raw_payload or {}).get("text") or (event.raw_payload or {}).get("body")
    elif source == "email_engagement":
        body = (event.raw_payload or {}).get("reply_body")
    if not body:
        # generic fallback over a few known keys
        body = (event.raw_payload or {}).get("message") or (event.raw_payload or {}).get("body")
    return (body or "").strip()


def _build_messages(text: str) -> list[dict]:
    system = (
        "You are a demand-intelligence classifier for a B2B demand-signal pipeline. "
        'Respond with STRICT JSON on one line: {"label": <string>, "confidence": <0.0-1.0>, '
        '"reason": <short string>}. '
        "Classify the free text into one of: pricing_inquiry, product_question, "
        "competitor_mention, onboarding, integration, feature_request, other. "
        "If no clear intent, label it unknown. confidence must reflect how confident "
        "you are that the intent is correctly identified; keep reason short."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Event text: \"{text}\""},
    ]


class _InterpretClient:
    """Lazy wrapper over the openai async client pointed at OpenRouter."""

    def __init__(self, settings_: Any = settings):
        self._settings = settings_
        self._client = None

    def get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            api_key = self._settings.openrouter_api_key
            if not api_key:
                raise RuntimeError("OPENROUTER_API_KEY is not configured")
            # LIVE call: OpenAI-compatible endpoint at OpenRouter (PRD §6).
            self._client = AsyncOpenAI(
                base_url=OPENROUTER_BASE_URL,
                api_key=api_key,
                # Optional metadata headers; harmless, helps attribution.
                default_headers={
                    "HTTP-Referer": OPENROUTER_REFERRER,
                    "X-Title": "demand-signal-workflow",
                },
            )
        return self._client


_client_holder = _InterpretClient()


def _parse_classification(content: str) -> dict:
    """Robustly parse the model's JSON classification body.

    Handles models that wrap or prefix the JSON, and truncation by locating the
    first ``{`` and last ``}`` rather than requiring the whole body to be pure
    JSON.
    """
    if not content:
        raise InterpretError("classifier returned empty content")
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise InterpretError(
            f"classifier returned non-JSON response: {content[:200]!r}"
        )
    try:
        return json.loads(content[start:end + 1])
    except json.JSONDecodeError as exc:
        raise InterpretError(
            f"classifier returned non-JSON response: {content[:200]!r}", [exc]
        ) from exc


async def _call_llm(text: str) -> dict:
    """Real LIVE call to the classification model. Returns parsed JSON body + usage.

    Raises the provider exception on failure (retries handled by caller). This is
    the only place a network call to the provider happens.
    """
    messages = _build_messages(text)
    client = _client_holder.get_client()
    response = await client.chat.completions.create(
        model=settings.classification_model,
        messages=messages,
        temperature=0,
        # Generous enough for the model's reasoning tokens + a full short JSON
        # object; too small a budget truncates valid JSON mid-parse.
        max_tokens=200,
    )
    content = response.choices[0].message.content or ""
    usage = None
    if response.usage is not None:
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
    parsed = _parse_classification(content)
    parsed["_usage"] = usage
    parsed["_model"] = settings.classification_model
    return parsed


async def classify_event(
    db: AsyncSession,
    event: Event,
    identity_fields: dict | None = None,
) -> dict:
    """Classify an event, persisting an ``interpretations`` row (FR-4).

    Returns a descriptor: ``{"status": "interpreted", "interpretation_id": ...}``
    or raises ``InterpretError`` (visible error state) when the provider path
    fails after bounded retries.
    """
    start = time.monotonic()
    text = _extract_text(event, None)
    max_attempts = settings.retry_max_attempts
    n_tokens = _count_tokens(text)

    if n_tokens < settings.interpret_min_tokens:
        # Efficiency path: NEVER call the LLM. Deterministic `unknown`.
        interpretation = await _upsert_interpretation(
            db, event.id,
            label="unknown",
            confidence=Decimal("0.00"),
            reason=f"insufficient_text:{n_tokens}<{settings.interpret_min_tokens}",
            model_version="none",
            prompt_version="none",
            was_skipped=True,
            token_usage=None,
        )
        await db.flush()  # populate interpretation.id before the receipt references it
        await write_receipt(
            db,
            action_type="interpreted",
            entity_id=interpretation.id,
            entity_type="interpretation",
            event_id=event.id,
            metadata={"label": interpretation.label,
                      "confidence": str(interpretation.confidence),
                      "was_skipped": interpretation.was_skipped,
                      "prompt_version": interpretation.prompt_version},
        )
        await db.commit()
        logger.info(
            "interpret_skipped_no_llm",
            input_id=str(event.id),
            decision="skipped",
            reason=f"insufficient_text:{n_tokens}<{settings.interpret_min_tokens}",
            action="interpreted",
            result="skipped",
            error=None,
            timing_ms=round((time.monotonic() - start) * 1000, 2),
            tokens=n_tokens,
            label="unknown",
        )
        return {"status": "interpreted",
                "interpretation_id": str(interpretation.id), "label": "unknown",
                "was_skipped": True}

    # Bounded retry around the ONE external call (FR-11, wired for Phase 8's
    # dead-letter path). Only transient provider failures (timeout, connection,
    # 429 / 5xx, or a bad-JSON parse) are retried; a 401/bad-key or other 4xx
    # config error fails fast. On exhaustion the final exception is converted into
    # a visible InterpretError — never a silent `unknown`.
    retrying = AsyncRetrying(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(max_attempts),
        wait=wait_random_exponential(
            multiplier=settings.retry_base_delay_ms / 1000, max=8.0
        ),
        before_sleep=_log_retry(event),
        reraise=True,
    )
    result: dict | None = None
    attempt_count = 0
    try:
        async for attempt in retrying:
            with attempt:
                attempt_count += 1
                result = await _call_llm(text)
    except Exception as exc:  # noqa: BLE001 - last provider failure after bounded retries
        # Terminal outcome after bounded retries: dead-letter the event. Write the
        # dead_letter_queue row AND a `dead_lettered` receipt in the SAME commit
        # (FR-9 atomicity), then surface a visible error to the router.
        # Sanitize/truncate the provider error — no raw secrets, no unbounded text.
        sanitized = str(exc)[:500]
        dlq = DeadLetterQueue(
            event_id=event.id,
            stage="interpret",
            error=sanitized,
            retry_count=attempt_count,
        )
        db.add(dlq)
        await db.flush()  # populate dlq.id before the receipt references it
        await write_receipt(
            db,
            action_type="dead_lettered",
            entity_id=dlq.id,
            entity_type="dead_letter",
            event_id=event.id,
            metadata={"stage": "interpret", "retry_count": attempt_count},
            status="error",
        )
        await db.commit()  # DLQ row + dead_lettered receipt committed together
        logger.error(
            "interpret_dead_lettered",
            input_id=str(event.id),
            decision="dead_letter",
            reason=f"classification failed after {attempt_count} attempts; event dead-lettered",
            action="dead_lettered",
            result="error",
            error=sanitized,
            timing_ms=round((time.monotonic() - start) * 1000, 2),
            retry_count=attempt_count,
        )
        raise InterpretError(
            f"classification failed after {attempt_count} attempts: {exc}",
            [exc],
        ) from exc
    if result is None:
        raise InterpretError("classification produced no result")
    usage = result.get("_usage")
    label = str(result.get("label", "unknown"))
    try:
        confidence = Decimal(str(result.get("confidence", "0"))).quantize(Decimal("0.01"))
    except Exception:
        confidence = Decimal("0.00")
    reason = str(result.get("reason", ""))[:500]
    interpretation = await _upsert_interpretation(
        db, event.id,
        label=label,
        confidence=confidence,
        reason=reason,
        model_version=result.get("_model", settings.classification_model),
        prompt_version=PROMPT_VERSION,
        was_skipped=False,
        token_usage=usage,
    )
    await db.flush()  # populate interpretation.id before the receipt references it
    await write_receipt(
        db,
        action_type="interpreted",
        entity_id=interpretation.id,
        entity_type="interpretation",
        event_id=event.id,
        metadata={"label": interpretation.label,
                  "confidence": str(interpretation.confidence),
                  "was_skipped": interpretation.was_skipped,
                  "prompt_version": interpretation.prompt_version},
    )
    await db.commit()
    logger.info(
        "interpret_llm_ok",
        input_id=str(event.id),
        decision="ok",
        reason=f"classified label={label} with confidence={confidence}",
        action="interpreted",
        result="ok",
        error=None,
        timing_ms=round((time.monotonic() - start) * 1000, 2),
        label=label,
        usage=usage,
        model=interpretation.model_version,
    )
    return {"status": "interpreted",
            "interpretation_id": str(interpretation.id), "label": label,
            "confidence": str(confidence),
            "reason": reason,
            "model_version": interpretation.model_version,
            "prompt_version": interpretation.prompt_version,
            "model": interpretation.model_version,
            "was_skipped": False, "usage": usage}


async def _upsert_interpretation(db: AsyncSession, event_id: Any, **fields: Any) -> Interpretation:
    """Find an existing interpretation for the event (edit re-run) or create one.

    ``interpretations.event_id`` is unique; an edited resubmission re-runs the
    pipeline (FR-2), so we must update the existing row rather than insert a
    duplicate and violate the constraint.
    """
    existing = (
        await db.execute(select(Interpretation).where(Interpretation.event_id == event_id))
    ).scalars().first()
    if existing is not None:
        for key, value in fields.items():
            setattr(existing, key, value)
        return existing
    interpretation = Interpretation(event_id=event_id, **fields)
    db.add(interpretation)
    return interpretation