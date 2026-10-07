# hermes-plugin-intern-discovery

A tiny [Hermes](https://github.com/NousResearch/hermes-agent) plugin that stops
`discovery-api.intern-ai.org.cn` from being mistaken for a dead account.

It is a single `transform_api_error_classification` hook — no core patch, so
`hermes update` cannot revert it.

## The bug it fixes

The endpoint reports a spent free-tier **quota window** as:

```
HTTP 429
{"error": {"message": "quota exceeded", "type": "quota_exceeded", "code": "quota_exceeded"}}
```

Hermes' built-in classifier reads a bare "quota" wall on a 429 as **billing**
(`retryable=False`), so:

- the turn aborts after the first attempt with *"add credits / retrying won't help"*,
- every credential in the pool is benched for about an hour (`failure_reason=billing`).

But the limit is transient. Observed 2026-10-07 on a pool of two keys for this
endpoint:

| time  | what happened                                                            |
|-------|--------------------------------------------------------------------------|
| 11:45 | key1 → 429 `quota exceeded`, rotated to key2 → key2 → 429 as well          |
| 11:45 | both marked `failure_reason=billing`, turn aborted as non-retryable        |
| 12:24 | both keys answer `HTTP 200` again — with no top-up, no plan change        |

Nothing was wrong with the account; the keys simply could not take a
Hermes-sized request while the window was spent. With this plugin the same
situation resolves itself: the turn backs off (`Rate limited. Waiting 2.0s
(attempt 2/3)…`), the pool rotates, and the request succeeds on the next key
that still has allowance.

## Install

```bash
hermes plugins install SingleVirgin/hermes-plugin-intern-discovery
hermes plugins enable intern-discovery
```

Then restart the process that talks to the endpoint (the CLI picks it up on the
next launch; a running gateway needs a restart).

Verify the hook is live — the next 429 from this endpoint logs:

```
agent.error_classifier: API error classified by plugin hook: rate_limit (provider=custom, status=429)
```

and the pool rotates instead of aborting:

```
agent.credential_pool: credential pool: marking key1 exhausted (status=429), rotating
run_agent: Credential 429 (rate limit) — rotated to pool entry <id>
```

## Scope and limitations

- **Only `429`.** Any other status is declined, so this plugin can never touch
  401/403/404/5xx handling.
- **Endpoint first, code second.** The hook payload has no `base_url`, so the
  endpoint is matched on the SDK exception's request URL. When an error arrives
  re-wrapped without a request object, the fallback matches the exact structured
  code (`quota_exceeded`) **and** only for routes without a first-party provider
  name (`` or `custom`) — i.e. `custom_providers` entries.
- **Never the word "quota" alone.** OpenAI/Anthropic quota walls that really are
  billing keep their built-in classification.
- The plugin only changes *classification*; it does not add credentials,
  raise quotas, or bypass anything the endpoint is willing to serve.

## Covering another endpoint

Edit `ENDPOINTS` in `__init__.py` (and `QUOTA_CODES` if the platform uses a
different structured code), then restart. Please also open an issue/PR upstream
if the misclassification is generic rather than endpoint-specific.

## Tests

```bash
python -m pytest tests -q
```

The classifier has no Hermes dependency, so the suite runs standalone. (Hermes
itself imports plugins by path; these tests load the module the same way.)

## Related

- Upstream proposal: [NousResearch/hermes-agent#134352](https://github.com/NousResearch/hermes-agent/pull/134352)
  — forward `base_url` to `transform_api_error_classification`, which would let a
  plugin scope itself by route instead of by error code.

## License

MIT — see [LICENSE](LICENSE).
