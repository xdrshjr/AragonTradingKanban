"""Security primitives: principals, permissions, passwords, policy, rate limits (spec §24.3).

Dependency direction (spec E-13, enforced by `tests/test_boundaries.py`):

* `services/` may import `security/`.
* `security/` must **not** import `services/` or `routers/` — with exactly one
  exception, `security/deps.py`, which is the HTTP adapter (it resolves a cookie
  into a `Principal` through `services.session_service`) and therefore sits on the
  router side of the line. Routers import `security.deps`; nothing in `services/`
  does.

This package deliberately does **not** import its submodules eagerly: importing
`app.security.passwords` from a service must not drag the FastAPI dependency layer
in with it (that would be an import cycle through `services.session_service`).
"""
