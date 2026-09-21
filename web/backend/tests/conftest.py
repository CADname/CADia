from __future__ import annotations

import os
import tempfile


os.environ.setdefault("NEXIS_ENV", "test")
os.environ.setdefault("NEXIS_DATA_ROOT", tempfile.mkdtemp(prefix="nexis-web-tests-"))
os.environ.setdefault("NEXIS_SESSION_SECRET", "nexis-web-test-session-secret")
os.environ.setdefault("NEXIS_COOKIE_SECURE", "false")
os.environ.setdefault("NEXIS_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")
