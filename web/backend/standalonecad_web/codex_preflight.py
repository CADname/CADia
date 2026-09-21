from __future__ import annotations

import tempfile
from pathlib import Path

from standalonecad_web.providers.app_server import AppServerClient


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="codex-preflight-") as tmp:
        root = Path(tmp)
        client = AppServerClient(
            "preflight",
            root / "codex-home",
            root / "workspace",
        )
        try:
            client.start()
            account = client.account()
            if not isinstance(account, dict):
                raise RuntimeError("account/read did not return an object")
            print("CODEX_APP_SERVER_PROTOCOL_OK")
        finally:
            client.stop()


if __name__ == "__main__":
    main()
