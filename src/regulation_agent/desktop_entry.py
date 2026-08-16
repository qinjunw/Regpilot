from __future__ import annotations

import os
from pathlib import Path


def _write_boot_log(message: str) -> None:
    raw_path = os.environ.get("REGPILOT_SMOKE_LOG")
    if not raw_path:
        return
    path = Path(raw_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


try:
    _write_boot_log("desktop entry import launcher")
    from regulation_agent.launcher import main
    _write_boot_log("desktop entry imported launcher")
except Exception as exc:  # pragma: no cover - only visible in packaged smoke diagnostics
    _write_boot_log(f"desktop entry import error {type(exc).__name__}: {exc}")
    raise


if __name__ == "__main__":
    _write_boot_log("desktop entry main")
    main()
