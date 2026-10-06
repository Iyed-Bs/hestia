"""
HTTP security headers on every response.

- Content-Security-Policy: only this origin may provide scripts, styles,
  images and connections; no inline scripts; the page cannot be framed.
- X-Content-Type-Options / Referrer-Policy / Permissions-Policy /
  Cross-Origin-Opener-Policy: the usual hardening set (OWASP Secure Headers).
- Strict-Transport-Security when cookies are marked Secure, i.e. when the
  gateway is served over HTTPS (behind the Caddy proxy in deploy/).
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)


class SecurityHeaders:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.headers = [
            (b"content-security-policy", CSP.encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
            (b"cross-origin-opener-policy", b"same-origin"),
            (b"x-frame-options", b"DENY"),
        ]
        if hsts:
            self.headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [
                    (k, v) for k, v in self.headers if k not in existing
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)
