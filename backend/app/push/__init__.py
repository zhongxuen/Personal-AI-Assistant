"""Web push subscription subsystem (browser notifications).

`service.py`'s `PushSubscriptionService` is the thin DB-backed layer over the
`push_subscriptions` table that `app/api/routes/push.py` calls into -- same split as
every other subsystem package here (`app/auth`, `app/tasks`, `app/memory`): a service
module wrapping `Session`, never a route importing SQLAlchemy models directly
(§41 Rule 7).

`sender.py`'s `WebPushSender` is the other half: actually *delivering* a push (signing
a VAPID JWT with `settings.vapid_private_key`, encrypting the payload, POSTing to each
stored endpoint via `pywebpush`). It was deliberately left unbuilt until something
needed to send (§41 Rule 1); `ReminderScheduler` (`app/tasks/service.py`'s sibling,
`app/tasks/scheduler.py`) is that caller -- see the "ReminderScheduler is
multi-channel" section of docs/architecture.md.

The store/deliver split is the point: routes and the scheduler depend on whichever
half they actually need, and `sender.py` is the only module anywhere that touches
`pywebpush` or the VAPID private key.
"""
