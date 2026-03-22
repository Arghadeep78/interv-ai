import os
import logging
from langchain_groq import ChatGroq
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from dotenv import load_dotenv
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
    RetryError,
)

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exception raised when ALL retries + fallbacks are exhausted
# ---------------------------------------------------------------------------

class RateLimitExhaustedError(Exception):
    """Raised when all 5 retries across all fallback models have failed."""
    pass


# ---------------------------------------------------------------------------
# Model configurations
# ---------------------------------------------------------------------------
MODELS = {
    "heavy": "llama-3.3-70b-versatile",
    "medium": "mixtral-8x7b-32768",
    "light": "llama-3.1-8b-instant",
}

FALLBACK_CHAIN = [MODELS["heavy"], MODELS["medium"], MODELS["light"]]
LIGHT_FALLBACK_CHAIN = [MODELS["light"], MODELS["medium"]]

DEFAULT_MAX_TOKENS = {
    "heavy": 1024,
    "light": 256,
}


# ---------------------------------------------------------------------------
# Rate-limit detection helper
# ---------------------------------------------------------------------------
def _is_rate_limit_error(exc: BaseException) -> bool:
    """Check if an exception is a Groq rate-limit (429) error."""
    exc_str = str(exc).lower()
    return any(kw in exc_str for kw in ("rate limit", "429", "rate_limit", "too many requests"))


# ---------------------------------------------------------------------------
# Core factory: returns a configured ChatGroq instance
# ---------------------------------------------------------------------------
def get_llm(
    role: str = "heavy",
    temperature: float = 0.5,
    max_tokens: int | None = None,
) -> ChatGroq:
    """
    Returns a configured ChatGroq instance.

    Args:
        role: 'heavy' for creative/complex tasks (70B),
              'light' for classifiers/decisions (8B).
        temperature: Sampling temperature.
        max_tokens: Max output tokens. Defaults based on role.
    """
    model_name = MODELS.get(role, MODELS["heavy"])
    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS.get(role, 512)

    return ChatGroq(
        groq_api_key=os.environ.get("GROQ_API_KEY"),
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ---------------------------------------------------------------------------
# Retry-wrapped invocation with model fallback
# ---------------------------------------------------------------------------
async def invoke_with_retry(
    messages,
    role: str = "heavy",
    temperature: float = 0.5,
    max_tokens: int | None = None,
) -> BaseMessage:
    """
    Invoke an LLM with automatic retry (5 attempts per model) and fallback
    across the model chain. If all models are exhausted, raises
    RateLimitExhaustedError.

    Args:
        messages: Either a string prompt or a list of message dicts.
        role: 'heavy' or 'light' — determines the starting model.
        temperature: Sampling temperature.
        max_tokens: Max output tokens.
    """
    chain = FALLBACK_CHAIN if role == "heavy" else LIGHT_FALLBACK_CHAIN

    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS.get(role, 512)

    last_error = None

    for model_name in chain:
        llm = ChatGroq(
            groq_api_key=os.environ.get("GROQ_API_KEY"),
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Build a retry-wrapped call for this specific model
        @retry(
            retry=retry_if_exception(_is_rate_limit_error),
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=2, min=2, max=60),
            reraise=True,
        )
        async def _invoke_with_backoff(llm_instance, msgs):
            return await llm_instance.ainvoke(msgs)

        try:
            logger.info(f"Attempting LLM call with model: {model_name}")
            response = await _invoke_with_backoff(llm, messages)
            return response
        except RetryError as e:
            logger.warning(
                f"All 5 retries exhausted for model {model_name}: {e}"
            )
            last_error = e
            continue
        except Exception as e:
            if _is_rate_limit_error(e):
                logger.warning(f"Rate limit hit on {model_name}, trying next fallback...")
                last_error = e
                continue
            else:
                # Non-rate-limit error — don't swallow it
                raise

    # All models exhausted
    raise RateLimitExhaustedError(
        f"All models in fallback chain exhausted after retries. Last error: {last_error}"
    )
