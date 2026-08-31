"""WhatsApp subsystem (§37 Phase 13, file 18).

`linking.py`'s `WhatsAppLinkService` is the thin DB-backed layer over the WhatsApp
columns on `users` that `app/api/routes/whatsapp.py` (pairing-code generation) and,
from file 18 prompt 2, the webhook route both call into -- same split as every other
subsystem package here (`app/auth`, `app/push`, `app/memory`): a service module
wrapping `Session`, never a route touching SQLAlchemy models directly (§41 Rule 7).

Linking exists because WhatsApp identifies a sender by phone number, not by
username/password: there is no bearer token on an inbound webhook to run through
`get_current_user`, so the *only* thing turning "+60... messaged us" into a `User` is
a pairing the user themselves initiated while already logged in. That is why an
unknown number gets `UNLINKED_REPLY` and nothing else -- never an auto-created
account. `app/auth/service.py`'s docstring already states the same posture for HTTP
("no public register route"), and a webhook anyone can send a message to would be a
much wider signup surface than the one deliberately not built there.

The adapter and webhook route themselves (`app/platforms/whatsapp.py`,
`app/api/routes/whatsapp_webhook.py`, file 18 prompt 2) live outside this package, and
nothing here imports `httpx` or talks to Meta's Graph API -- the same
store-versus-deliver split as `app/push`'s `service.py`/`sender.py`.
"""
