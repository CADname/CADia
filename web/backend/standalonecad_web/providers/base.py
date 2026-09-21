from __future__ import annotations

from typing import Any, Protocol


class AIProvider(Protocol):
    def complete_json(
        self,
        prompt: str,
        *,
        output_schema: dict[str, Any],
        model: str | None = None,
        effort: str | None = None,
        on_event=None,
    ) -> dict[str, Any]: ...

    def cancel(self) -> None: ...
