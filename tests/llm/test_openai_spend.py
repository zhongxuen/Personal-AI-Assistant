"""
OpenAI spend rules (OPENAI-KEY-RULES.md rules 4-6): two model tiers, the prices table,
the pre-send budget check, the cost log, `max_output_tokens`, and billing errors that
must surface instead of failing quietly.

Same stub-client seam as test_openai_provider.py: only `responses.create` is faked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import pytest

from app.config.settings import Settings
from app.core.assistant import AssistantCore
from app.database.models import LLMUsage
from app.llm import openai_guard as openai
from app.llm.ai_router import NO_PROVIDER_AVAILABLE_ERROR_TYPE, AIRouter
from app.llm.base import LLMRequest, LLMResult
from app.llm.openai import OPENAI_PRICES, OpenAIProvider, price_for
from app.llm.quota_manager import QuotaManager

_REQUEST = httpx2.Request("POST", f"{openai.OPENAI_BASE_URL}/responses")


def _settings(**overrides) -> Settings:
    defaults: dict = dict(
        openai_api_key="test-api-key",
        openai_model_bulk="gpt-6-luna",
        openai_model_quality="gpt-6.1-terra",
        openai_max_retries=2,
        openai_retry_base_delay_seconds=0.0,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _provider(create: AsyncMock, settings: Settings | None = None, db=None) -> OpenAIProvider:
    provider = OpenAIProvider(settings=settings or _settings(), db=db)
    provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))
    return provider


def _response(text="ok", *, input_tokens=100, output_tokens=20, status="completed", reason=None):
    output = [SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=text)])] if text else []
    return SimpleNamespace(
        status=status,
        output=output,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
    )


def _status_error(cls, status_code: int, code: str | None, message: str = "boom"):
    body = {"message": message, "code": code}
    return cls(message, response=httpx2.Response(status_code, request=_REQUEST), body=body)


# --- tiers ------------------------------------------------------------------------------


def test_default_tiers_are_luna_and_terra():
    settings = Settings(_env_file=None)
    assert settings.openai_model_bulk == "gpt-6-luna"
    assert settings.openai_model_quality == "gpt-6.1-terra"
    assert settings.ai_monthly_budget_usd == 8.0


@pytest.mark.asyncio
async def test_each_tier_uses_its_own_model_and_quality_is_the_default(test_db):
    session = test_db()
    create = AsyncMock(return_value=_response())
    provider = _provider(create, db=session)

    await provider.generate(LLMRequest(message="hi"))
    assert create.await_args.kwargs["model"] == "gpt-6.1-terra"

    await provider.generate(LLMRequest(message="sort these", tier="bulk", job="sort_tasks"))
    assert create.await_args.kwargs["model"] == "gpt-6-luna"

    rows = session.query(LLMUsage).order_by(LLMUsage.id).all()
    assert [(r.model, r.job) for r in rows] == [
        ("gpt-6.1-terra", "assistant_chat"),
        ("gpt-6-luna", "sort_tasks"),
    ]


@pytest.mark.asyncio
async def test_every_call_caps_output_tokens_and_sends_no_temperature():
    create = AsyncMock(return_value=_response())
    await _provider(create, settings=_settings(openai_max_output_tokens=1234)).generate(LLMRequest(message="hi"))
    kwargs = create.await_args.kwargs
    assert kwargs["max_output_tokens"] == 1234
    assert "temperature" not in kwargs and "top_p" not in kwargs


# --- prices + cost log ----------------------------------------------------------------


def test_prices_table_covers_both_tiers_and_unknown_models_cost_the_most():
    assert OPENAI_PRICES["gpt-6-luna"] == (0.10, 0.10, 0.50)
    assert OPENAI_PRICES["gpt-6.1-terra"] == (1.00, 1.00, 5.00)
    assert price_for("gpt-6-luna-2026-10-01") == OPENAI_PRICES["gpt-6-luna"]
    dearest = (max(p[0] for p in OPENAI_PRICES.values()), max(p[1] for p in OPENAI_PRICES.values()),
               max(p[2] for p in OPENAI_PRICES.values()))
    assert price_for("gpt-7-something-new") == dearest
    # A variant isn't its base model -- it must not inherit the cheaper price.
    assert price_for("gpt-6-luna-pro") == dearest


@pytest.mark.asyncio
async def test_cost_log_uses_the_tier_models_price(test_db):
    session = test_db()
    create = AsyncMock(return_value=_response(input_tokens=1_000_000, output_tokens=1_000_000))

    await _provider(create, db=session).generate(LLMRequest(message="x", tier="bulk"))
    await _provider(create, db=session).generate(LLMRequest(message="x"))

    luna, terra = session.query(LLMUsage).order_by(LLMUsage.id).all()
    assert luna.cost_usd == pytest.approx(0.10 + 0.50)
    assert terra.cost_usd == pytest.approx(1.00 + 5.00)


# --- budget ---------------------------------------------------------------------------


def _spend(session, usd: float, status: str = "SUCCESS") -> None:
    session.add(LLMUsage(provider="openai", model="gpt-6.1-terra", cost_usd=usd, status=status))
    session.commit()


@pytest.mark.asyncio
async def test_monthly_cap_stops_the_call_before_it_is_sent(test_db):
    session = test_db()
    _spend(session, 8.00)
    create = AsyncMock(return_value=_response())
    settings = _settings(ai_monthly_budget_usd=8.0, openai_daily_budget_usd=100.0)

    result = await _provider(create, settings=settings, db=session).generate(LLMRequest(message="hi"))

    create.assert_not_awaited()
    assert (result.status, result.error_type) == ("QUOTA_EXHAUSTED", "app_budget_exhausted")
    assert "AI_MONTHLY_BUDGET_USD" in result.spend_stop


@pytest.mark.asyncio
async def test_monthly_cap_also_stops_a_streamed_call(test_db):
    session = test_db()
    _spend(session, 9.00)
    create = AsyncMock()
    provider = _provider(create, settings=_settings(openai_daily_budget_usd=100.0), db=session)

    chunks = [c async for c in provider.generate_stream(LLMRequest(message="hi"))]

    create.assert_not_awaited()
    assert chunks[-1].final.spend_stop


def test_failed_calls_that_were_billed_still_count_toward_the_usd_cap(test_db):
    session = test_db()
    _spend(session, 5.0)
    _spend(session, 3.0, status="PERMANENT_ERROR")  # e.g. incomplete:max_output_tokens
    manager = QuotaManager(settings=_settings(openai_daily_budget_usd=100.0, ai_monthly_budget_usd=8.0), db=session)
    assert manager.monthly_usage("openai") == pytest.approx(8.0)
    assert manager.within_budget("openai") is False


# --- billing errors -------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (_status_error(openai.RateLimitError, 429, "credit_balance_exhausted"), "billing:credit_balance_exhausted"),
        (_status_error(openai.RateLimitError, 429, "insufficient_quota"), "billing:insufficient_quota"),
        (_status_error(openai.BadRequestError, 400, "billing_hard_limit_reached"), "billing:billing_hard_limit_reached"),
        # An unfamiliar code is still caught by its message.
        (_status_error(openai.PermissionDeniedError, 403, "something_new", "Project hard limit reached"), "billing:something_new"),
    ],
)
async def test_billing_errors_surface_loudly_and_are_never_retried(error, error_type, caplog):
    create = AsyncMock(side_effect=error)
    with caplog.at_level("ERROR"):
        result = await _provider(create).generate(LLMRequest(message="hi"))
    assert (result.status, result.error_type) == ("QUOTA_EXHAUSTED", error_type)
    assert result.spend_stop and "platform.openai.com" in result.spend_stop
    assert create.await_count == 1
    assert "billing stop" in caplog.text


@pytest.mark.asyncio
async def test_streamed_billing_failure_surfaces_too():
    failed = SimpleNamespace(
        type="response.failed",
        response=SimpleNamespace(error=SimpleNamespace(code="credit_balance_exhausted", message="no credit")),
    )

    class _Stream:
        def __aiter__(self):
            async def gen():
                yield failed
            return gen()

    create = AsyncMock(return_value=_Stream())
    chunks = [c async for c in _provider(create).generate_stream(LLMRequest(message="hi"))]
    assert chunks[-1].final.error_type == "billing:credit_balance_exhausted"
    assert "credit balance" in chunks[-1].final.spend_stop


def test_a_plain_rate_limit_is_not_a_billing_stop():
    provider = _provider(AsyncMock())
    assert provider._classify_status_error(
        _status_error(openai.RateLimitError, 429, "rate_limit_exceeded", "Rate limit reached"), "m"
    ) == ("QUOTA_EXHAUSTED", "rate_limit_exceeded")


# --- reasoning ate the cap -------------------------------------------------------------


@pytest.mark.asyncio
async def test_incomplete_reply_with_no_text_is_an_error_but_its_cost_is_logged(test_db):
    session = test_db()
    create = AsyncMock(
        return_value=_response("", output_tokens=4096, status="incomplete", reason="max_output_tokens")
    )

    result = await _provider(create, db=session).generate(LLMRequest(message="hi"))

    assert (result.status, result.error_type) == ("PERMANENT_ERROR", "incomplete:max_output_tokens")
    row = session.query(LLMUsage).one()
    assert row.response_tokens == 4096
    assert row.cost_usd == pytest.approx((100 * 1.0 + 4096 * 5.0) / 1_000_000)


@pytest.mark.asyncio
async def test_incomplete_reply_that_has_text_is_still_an_answer():
    create = AsyncMock(return_value=_response("partial", status="incomplete", reason="max_output_tokens"))
    result = await _provider(create).generate(LLMRequest(message="hi"))
    assert (result.status, result.text) == ("SUCCESS", "partial")


# --- the reply says why -----------------------------------------------------------------


class _StubProvider:
    supports_tool_loop = False

    def __init__(self, name: str, result: LLMResult) -> None:
        self.name = name
        self._result = result

    def is_available(self) -> bool:
        return True

    async def generate(self, request, *, fallback_used=False):
        return self._result


class _Chain:
    def __init__(self, *providers) -> None:
        self._providers = list(providers)

    def get_chain(self):
        return self._providers


class _Quota:
    def __init__(self, over: set[str]) -> None:
        self._over = over

    def within_budget(self, name: str) -> bool:
        return name not in self._over


@pytest.mark.asyncio
async def test_router_carries_a_billing_stop_onto_the_exhausted_result():
    stop = LLMResult(status="QUOTA_EXHAUSTED", error_type="billing:credit_balance_exhausted", spend_stop="top up")
    router = AIRouter(provider_manager=_Chain(_StubProvider("openai", stop)), quota_manager=_Quota(set()))
    result = await router.route(LLMRequest(message="hi"))
    assert result.error_type == NO_PROVIDER_AVAILABLE_ERROR_TYPE
    assert result.spend_stop == "top up"


@pytest.mark.asyncio
async def test_router_explains_an_openai_skip_for_the_app_budget():
    unused = LLMResult(status="SUCCESS", text="never")
    router = AIRouter(provider_manager=_Chain(_StubProvider("openai", unused)), quota_manager=_Quota({"openai"}))
    chunks = [c async for c in router.route_stream(LLMRequest(message="hi"))]
    assert "AI_MONTHLY_BUDGET_USD" in chunks[-1].final.spend_stop


def test_assistant_reply_includes_the_spend_stop():
    core = AssistantCore.__new__(AssistantCore)  # only the pure reply builder is used
    result = LLMResult(
        status="PERMANENT_ERROR", error_type=NO_PROVIDER_AVAILABLE_ERROR_TYPE, spend_stop="Top up OpenAI."
    )
    reply = core._llm_unavailable_response(result)
    assert "Top up OpenAI." in reply.text
