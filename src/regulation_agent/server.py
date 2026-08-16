from __future__ import annotations

import json
import os
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .model_runtime import default_config_path
from .resources import static_dir
from .service import ApplicationService
from .settings import default_state_dir


def make_server(host: str, port: int, service: ApplicationService | None = None) -> ThreadingHTTPServer:
    app_service = service or ApplicationService()

    class Handler(RegulationAgentHandler):
        service = app_service

    return ThreadingHTTPServer((host, port), Handler)


def configure_regpilot_runtime_environment(state_dir: str | Path | None = None) -> Path:
    root = Path(state_dir).expanduser() if state_dir else default_state_dir()
    formfill_state_dir = root / "formfill"
    os.environ.setdefault("FORMFILL_GUARD_STATE_DIR", str(formfill_state_dir))
    return Path(os.environ["FORMFILL_GUARD_STATE_DIR"]).expanduser()


def run_server(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = True) -> None:
    configure_regpilot_runtime_environment()
    model_config = Path(os.environ.get("REGULATION_AGENT_MODEL_CONFIG") or default_config_path()).expanduser()
    enable_model = str(os.environ.get("REGULATION_AGENT_ENABLE_MODEL_INTENT") or "1").strip().lower() not in {"0", "false", "no", "off"}
    server = make_server(
        host,
        port,
        ApplicationService(model_config_path=model_config, enable_model_intent=enable_model),
    )
    url = f"http://{host}:{server.server_port}"
    print(f"Regulation Agent UI: {url}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping Regulation Agent UI.")
    finally:
        server.server_close()


class RegulationAgentHandler(BaseHTTPRequestHandler):
    service: ApplicationService

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_static("index.html", "text/html; charset=utf-8")
                return
            if parsed.path.startswith("/static/"):
                name = parsed.path.removeprefix("/static/")
                self._send_static(name, _content_type(name))
                return
            if parsed.path == "/api/bootstrap":
                query = parse_qs(parsed.query)
                session_id = query.get("session_id", [""])[0]
                self._send_json(self.service.bootstrap(session_id or None))
                return
            if parsed.path == "/api/settings/provider":
                self._send_json(self.service.settings.public_view())
                return
            if parsed.path == "/api/controlled-chrome":
                self._send_json({"ok": True, "controlled_chrome": self.service.controlled_chrome_status("api")})
                return
            if parsed.path == "/api/checklist":
                query = parse_qs(parsed.query)
                session_id = query.get("session_id", [""])[0]
                self._send_json(self.service.checklist(session_id or None))
                return
            if parsed.path == "/api/sessions":
                query = parse_qs(parsed.query)
                archived = str(query.get("archived", ["0"])[0]).strip().lower() in {"1", "true", "yes", "on"}
                self._send_json(self.service.list_sessions(archived=archived))
                return
            if parsed.path.startswith("/api/sessions/") and not parsed.path.endswith("/events"):
                session_id = unquote(parsed.path.split("/")[3])
                self._send_json(self.service.session_view(session_id))
                return
            if parsed.path.startswith("/api/sessions/") and parsed.path.endswith("/events"):
                session_id = unquote(parsed.path.split("/")[3])
                self._send_sse(self.service.session_events(session_id))
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_error(exc)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/chat/messages":
                self._send_json(
                    self.service.post_user_message(
                        str(payload.get("content") or ""),
                        payload.get("session_id"),
                        client_turn_id=str(payload.get("client_turn_id") or ""),
                    )
                )
                return
            if parsed.path == "/api/chat/messages/stream":
                self._send_chat_stream(payload)
                return
            if parsed.path == "/api/chat/turns/cancel":
                self._send_json(
                    self.service.cancel_agent_turn(
                        str(payload.get("session_id") or ""),
                        str(payload.get("client_turn_id") or ""),
                    )
                )
                return
            if parsed.path == "/api/controlled-chrome/open":
                self._send_json(self.service.open_controlled_chrome())
                return
            if parsed.path == "/api/sessions":
                self._send_json(self.service.create_session(title=str(payload.get("title") or "")))
                return
            session_action = _parse_session_action(parsed.path)
            if session_action is not None:
                session_id, action = session_action
                if action == "archive":
                    self._send_json(self.service.archive_session(session_id))
                    return
                if action == "restore":
                    self._send_json(self.service.restore_session(session_id))
                    return
            if parsed.path.startswith("/api/human-actions/") and parsed.path.endswith("/responses"):
                action_id = unquote(parsed.path.split("/")[3])
                self._send_json(
                    self.service.respond_to_human_action(action_id, str(payload.get("selected_option_id") or ""))
                )
                return
            fill_request = _parse_fill_request(parsed.path)
            if fill_request is not None:
                task_id, operation = fill_request
                if operation == "prepare":
                    self._send_json(self.service.prepare_fill_task(task_id, payload))
                    return
                if operation == "validate":
                    self._send_json(self.service.validate_fill_task_current_step(task_id, payload))
                    return
                if operation == "confirm":
                    self._send_json(self.service.confirm_fill_task_current_step(task_id, payload))
                    return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_error(exc)

    def do_PUT(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/api/settings/provider":
                self._send_json(self.service.save_provider_settings(self._read_json()))
                return
            if path == "/api/checklist":
                payload = self._read_json()
                self._send_json(self.service.update_checklist(str(payload.get("session_id") or ""), payload))
                return
            if path.startswith("/api/sessions/"):
                session_id = unquote(path.split("/")[3])
                payload = self._read_json()
                self._send_json(self.service.rename_session(session_id, str(payload.get("title") or "")))
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_error(exc)

    def do_DELETE(self) -> None:
        try:
            path = urlparse(self.path).path
            if path.startswith("/api/sessions/"):
                session_id = unquote(path.split("/")[3])
                self._send_json(self.service.delete_session(session_id))
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_error(exc)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def _send_static(self, name: str, content_type: str) -> None:
        path = _resolve_static_path(name)
        if path is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_sse(self, events: list[dict[str, Any]]) -> None:
        chunks: list[str] = []
        for event in events:
            chunks.append(f"id: {event['id']}\n")
            chunks.append(f"event: {event['type']}\n")
            chunks.append(f"data: {json.dumps(event['payload'], ensure_ascii=False)}\n\n")
        data = "".join(chunks).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_chat_stream(self, payload: dict[str, Any]) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        requested_session_id = str(payload.get("session_id") or "")
        stream_session_id = {"value": requested_session_id if requested_session_id in self.service.sessions else ""}
        stream_closed = {"value": False}
        provider_delta_seen = {"value": False}

        def emit(event_type: str, data: dict[str, Any], event_id: str | None = None) -> None:
            if stream_closed["value"]:
                return
            try:
                self._write_sse_event(event_type, data, event_id=event_id)
            except OSError:
                stream_closed["value"] = True

        def emit_service_event(session_id: str, event: dict[str, Any]) -> None:
            if stream_session_id["value"] and session_id != stream_session_id["value"]:
                return
            if not stream_session_id["value"]:
                stream_session_id["value"] = session_id
                emit("stream_session", {"session_id": session_id}, event_id=f"{event['id']}_session")
            if event["type"] == "assistant_delta":
                provider_delta_seen["value"] = True
            if event["type"] == "assistant_status":
                content = str((event.get("payload") or {}).get("content") or "")
                if not provider_delta_seen["value"]:
                    for index, character in enumerate(content):
                        emit(
                            "assistant_delta",
                            {"message_event_id": event["id"], "delta": character},
                            event_id=f"{event['id']}_delta_{index}",
                        )
            emit(str(event["type"]), event["payload"], event_id=str(event["id"]))

        try:
            result = self.service.post_user_message(
                str(payload.get("content") or ""),
                payload.get("session_id"),
                event_sink=emit_service_event,
                client_turn_id=str(payload.get("client_turn_id") or ""),
            )
            emit("stream_done", result)
        except Exception as exc:
            emit("stream_error", {"ok": False, "error": str(exc), "code": "invalid_request"})

    def _write_sse_event(self, event_type: str, payload: dict[str, Any], event_id: str | None = None) -> None:
        chunks: list[str] = []
        if event_id:
            chunks.append(f"id: {event_id}\n")
        chunks.append(f"event: {event_type}\n")
        chunks.append(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
        self.wfile.write("".join(chunks).encode("utf-8"))
        self.wfile.flush()

    def _send_error(self, exc: Exception) -> None:
        self._send_json({"ok": False, "error": str(exc), "code": "invalid_request"}, status=HTTPStatus.BAD_REQUEST)


def _parse_fill_request(path: str) -> tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[:2] != ["api", "fill-tasks"]:
        return None
    operation = parts[3]
    if operation not in {"prepare", "validate", "confirm"}:
        return None
    return unquote(parts[2]), operation


def _parse_session_action(path: str) -> tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[:2] != ["api", "sessions"]:
        return None
    action = parts[3]
    if action not in {"archive", "restore"}:
        return None
    return unquote(parts[2]), action


def _resolve_static_path(name: str) -> Path | None:
    normalized = unquote(str(name or "")).replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return None
    if any(part in {".", ".."} or part.startswith(".") for part in parts):
        return None
    root = static_dir().resolve()
    path = (root / Path(*parts)).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.exists() or not path.is_file():
        return None
    return path


def _content_type(name: str) -> str:
    if name.endswith(".css"):
        return "text/css; charset=utf-8"
    if name.endswith(".js"):
        return "text/javascript; charset=utf-8"
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".ico"):
        return "image/x-icon"
    return "application/octet-stream"
