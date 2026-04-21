"""SQLite migrations for the bundled app state database.

Forward-only, numbered ``.sql`` files applied idempotently at Panel
startup. See :mod:`cutmaster_ai.migrations.runner` for semantics.

Tables named with the ``studio_`` prefix are reserved for the CutMaster
Studio bundle and must not be created by OSS migrations.
"""
