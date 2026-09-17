"""How the edge PROVES to the core who is calling (ADR-0022).

Until this existed the edge simply stated it — `x-actor-id` in a header — and
the core believed. The metadata is text: whoever could reach the core's gRPC
port declared themselves any actor of any account, and the only thing in the
way was a NetworkPolicy, which is a property of the deployment.

Two proofs travel now, and they answer different questions:

* the PERSON's token, forwarded as `authorization: Bearer …`. Its signature is
  the identity provider's — an authority neither the edge nor the core controls
  — so it is the strongest proof of WHO, and it costs nothing: the edge already
  verified it a moment ago;
* an ASSERTION signed by the edge, `x-dop-assertion`. It carries what the token
  does not — the active account, the session, the actor's kind — and it is the
  only proof available on calls with no person at all, like resolving a subject
  into a user before an actor exists.

The signature covers the CLAIM, not just the caller's presence: actor, account
and expiry are inside what was signed. A shared secret in a header would prove
only that whoever sent it knew the secret, and would let them claim anything.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

# The wire format is the same one `internal/platform/callauth` reads, field for
# field: base64url(payload) + "." + hex(hmac-sha256). Fields separated by `|`,
# and a field carrying one is a bug at the source — never escaped, because
# escaping is where the two ends drift apart.
_SEPARATORS = "|."

# How long an assertion is worth. Short because it needs to survive one call,
# not a session: it is minted per request.
TTL_SECONDS = 120


class AssertionError_(ValueError):
    """A field that cannot be signed — an id carrying a separator."""


def sign(
    key: str,
    *,
    caller: str = "bff",
    actor_id: str = "",
    actor_kind: str = "",
    account_id: str = "",
    session_id: str = "",
    now: float | None = None,
) -> str:
    """Produces the value of `x-dop-assertion`.

    An EMPTY actor_id is legitimate and deliberate: the resolver asks the core
    who a subject is before any actor exists. Refusing it here would push
    whoever wrote that call into inventing an id.
    """
    expires = int((now if now is not None else time.time()) + TTL_SECONDS)
    fields = [caller, actor_id, actor_kind, account_id, session_id, str(expires)]
    for field in fields:
        if any(sep in field for sep in _SEPARATORS):
            raise AssertionError_(f"field with a separator: {field!r}")
    payload = "|".join(fields).encode()
    mac = hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode() + "." + mac
