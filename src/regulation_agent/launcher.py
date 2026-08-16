from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from urllib.request import urlopen

from .resources import release_model_config_path
from .server import configure_regpilot_runtime_environment, make_server
from .service import ApplicationService


HOST = "127.0.0.1"
PREFERRED_PORT = 8765


class RegPilotServerController:
    def __init__(self, host: str = HOST, preferred_port: int = PREFERRED_PORT) -> None:
        self.host = host
        self.preferred_port = preferred_port
        self.server = None
        self.thread: threading.Thread | None = None
        self.url = ""
        self.formfill_state_dir: Path | None = None
        self.model_config_path: Path | None = None

    def start(self) -> str:
        if self.server is not None:
            return self.url
        self.formfill_state_dir = configure_regpilot_runtime_environment()
        self.model_config_path = release_model_config_path()
        if self.model_config_path.exists():
            os.environ.setdefault("REGULATION_AGENT_MODEL_CONFIG", str(self.model_config_path))
        enable_model = str(os.environ.get("REGULATION_AGENT_ENABLE_MODEL_INTENT") or "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        service = ApplicationService(model_config_path=self.model_config_path, enable_model_intent=enable_model)
        try:
            server = make_server(self.host, self.preferred_port, service)
        except OSError:
            server = make_server(self.host, 0, service)
        self.server = server
        self.url = f"http://{self.host}:{server.server_port}"
        self.thread = threading.Thread(target=server.serve_forever, name="RegPilotServer", daemon=True)
        self.thread.start()
        return self.url

    def stop(self) -> None:
        server = self.server
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=3)
        self.server = None
        self.thread = None


def _write_smoke_log(message: str) -> None:
    raw_path = os.environ.get("REGPILOT_SMOKE_LOG")
    if not raw_path:
        return
    path = Path(raw_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def smoke_check() -> int:
    controller = RegPilotServerController(preferred_port=0)
    try:
        _write_smoke_log("starting")
        url = controller.start()
        _write_smoke_log(f"started {url}")
        with urlopen(f"{url}/", timeout=5) as response:
            data = response.read().decode("utf-8", errors="replace")
        _write_smoke_log(f"response contains RegPilot: {'RegPilot' in data}")
        return 0 if "RegPilot" in data else 1
    except Exception as exc:
        _write_smoke_log(f"error {type(exc).__name__}: {exc}")
        return 1
    finally:
        controller.stop()
        _write_smoke_log("stopped")


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if "--smoke" in args:
        raise SystemExit(smoke_check())
    from .launcher_ui import RegPilotLauncherWindow

    RegPilotLauncherWindow().run()


if __name__ == "__main__":
    main()
