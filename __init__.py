"""intern-ai discovery (discovery-api.intern-ai.org.cn) error quirks.

Hermes ships ``transform_api_error_classification`` so a plugin can own its
provider's error quirks without a core patch.  This one fixes a misclassified
HTTP 429.

The endpoint answers a spent free-tier quota window with

    HTTP 429 {"error": {"message": "quota exceeded",
                        "code": "quota_exceeded", "type": "quota_exceeded"}}

which the built-in classifier reads as *billing* — a bare "quota" wall on a 429
with no reset/Retry-After signal.  Billing is ``retryable=False``, so a turn
aborts after one attempt with "add credits / retrying won't help" copy and every
credential in the pool is benched ~1 h (``failure_reason=billing``), even though
the limit is a *transient window*: observed 2026-10-07, two pooled keys 429'd at
11:45 and both answered 200 again at 12:24 with no top-up.

Add the host to ``ENDPOINTS`` below to cover another endpoint with the same
behaviour; the codes in ``QUOTA_CODES`` are matched only for custom providers
(see the scoping note).
"""

# Hosts whose 429 quota wall is a transient window, not a billing verdict.
ENDPOINTS = ("discovery-api.intern-ai.org.cn",)

# Structured codes that mean "quota window" rather than "account out of money".
QUOTA_CODES = ("quota_exceeded",)

# Code-only fallback is bounded to routes without a first-party provider name:
# a provider declared under ``custom_providers`` reports the literal "custom".
_CUSTOM_PROVIDERS = ("", "custom")

# Recovery hints mirror the built-in rate-limit verdict: retry with backoff,
# rotate the credential, and let the fallback chain take over when the pool is
# empty.  Billing, by contrast, is retryable=False and stops after one attempt.
_RATE_LIMIT = {
    "reason": "rate_limit",
    "retryable": True,
    "should_rotate_credential": True,
    "should_fallback": True,
}


def _request_url(error) -> str:
    """URL of the failing call, from the SDK exception's request object."""
    return str(getattr(getattr(error, "request", None), "url", "") or "")


def classify(provider=None, model=None, status_code=None, error_type=None,
             error_code=None, error_message="", error_body=None, error=None, **kwargs):
    """Claim a transient 429 quota window; return None to decline.

    The hook payload carries no ``base_url``, so the endpoint is matched on the
    exception's own request URL when the SDK supplies one; otherwise the exact
    structured code decides, and only for custom providers — never the loose
    word "quota", which would rewrite genuine billing walls.
    """
    if status_code != 429:
        return None
    url = _request_url(error)
    if url:
        return dict(_RATE_LIMIT) if any(host in url for host in ENDPOINTS) else None
    if str(error_code or "").strip().lower() not in QUOTA_CODES:
        return None
    if str(provider or "").strip().lower() not in _CUSTOM_PROVIDERS:
        return None
    return dict(_RATE_LIMIT)


def register(ctx):
    ctx.register_hook("transform_api_error_classification", classify)
