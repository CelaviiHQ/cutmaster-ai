"""SQLite migrations for the bundled app state database.

Forward-only, numbered ``.sql`` files applied idempotently at Panel
startup. See :mod:`celavii_resolve.migrations.runner` for semantics.

Tables named with the ``studio_`` prefix are reserved for the Celavii
Studio bundle and must not be created by OSS migrations.
"""
