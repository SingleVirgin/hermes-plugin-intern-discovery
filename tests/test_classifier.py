"""Standalone tests for the classifier — no Hermes import needed.

The plugin module has no Hermes dependency, so these run with plain pytest:

    python -m pytest tests -q
"""

import importlib.util
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_plugin():
    spec = importlib.util.spec_from_file_location("intern_discovery_plugin", _ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plugin = _load_plugin()


class _Req:
    def __init__(self, url):
        self.url = url


class _FakeError(Exception):
    """Minimal stand-in for an SDK status error."""

    def __init__(self, url=None):
        super().__init__("boom")
        if url is not None:
            self.request = _Req(url)
        self.status_code = 429


def _classify(body_code, url=None, provider="custom", message="quota exceeded"):
    return plugin.classify(
        provider=provider,
        status_code=429,
        error_code=body_code,
        error_message=message,
        error=_FakeError(url),
    )


DISCOVERY = "https://discovery-api.intern-ai.org.cn/v1/chat/completions"


def test_claims_the_endpoint_quota_window():
    verdict = _classify("quota_exceeded", url=DISCOVERY)
    assert verdict == {
        "reason": "rate_limit",
        "retryable": True,
        "should_rotate_credential": True,
        "should_fallback": True,
    }


def test_other_endpoint_billing_wall_is_left_alone():
    # A real credit-exhaustion body on another host must stay *billing*.
    assert _classify("insufficient_quota", url="https://api-inference.modelscope.cn/v1") is None


def test_code_only_fallback_for_custom_providers():
    # Re-wrapped stream errors carry no request object: the exact code decides,
    # and only because the route has no first-party provider name.
    assert _classify("quota_exceeded") is not None
    assert _classify("quota_exceeded", provider="openai") is None
    assert _classify("insufficient_balance") is None


def test_non_429_is_never_claimed():
    assert plugin.classify(provider="custom", status_code=500, error_code="quota_exceeded") is None
    assert plugin.classify(provider="custom", status_code=None, error_code="quota_exceeded") is None


def test_register_wires_the_hook():
    seen = {}

    class _Ctx:
        def register_hook(self, name, callback):
            seen[name] = callback

    plugin.register(_Ctx())
    assert seen["transform_api_error_classification"] is plugin.classify
