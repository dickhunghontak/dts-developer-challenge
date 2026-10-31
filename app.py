"""SQLite-backed task manager API and web application using only the standard library."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


APP_DIR = Path(__file__).resolve().parent
INDEX_FILE = APP_DIR / "static" / "index.html"
STATUSES = ("To Do", "In Progress", "Done")
MAX_BODY_SIZE = 64 * 1024
TASK_ID_PATTERN = re.compile(r"^/api/tasks/([1-9][0-9]*)(?:/(status))?$")


class APIError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class TaskDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL
                        CHECK(status IN ('To Do', 'In Progress', 'Done')),
                    due_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, object]:
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "due_at": row["due_at"],
        }

    def create_task(
        self, title: str, description: str, status: str, due_at: str
    ) -> dict[str, object]:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tasks (title, description, status, due_at)
                VALUES (?, ?, ?, ?)
                """,
                (title, description, status, due_at),
            )
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return self._serialize(row)

    def list_tasks(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY due_at ASC, id ASC"
            ).fetchall()
            return [self._serialize(row) for row in rows]

    def get_task(self, task_id: int) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return self._serialize(row) if row else None

    def update_status(self, task_id: int, status: str) -> dict[str, object] | None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status = ? WHERE id = ?", (status, task_id)
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return self._serialize(row)

    def delete_task(self, task_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM tasks WHERE id = ?", (task_id,)
            )
            return cursor.rowcount > 0


def _validate_due_at(value: object) -> str:
    if not isinstance(value, str) or "T" not in value:
        raise APIError(400, "due_at must be an ISO 8601 date and time.")
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError as error:
        raise APIError(400, "due_at must be a valid ISO 8601 date and time.") from error
    return parsed.isoformat()


def _validate_create(payload: object) -> tuple[str, str, str, str]:
    if not isinstance(payload, dict):
        raise APIError(400, "Request body must be a JSON object.")

    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise APIError(400, "title is required and must not be blank.")
    title = title.strip()
    if len(title) > 200:
        raise APIError(400, "title must be 200 characters or fewer.")

    description = payload.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise APIError(400, "description must be a string.")
    if len(description) > 2000:
        raise APIError(400, "description must be 2000 characters or fewer.")

    status = payload.get("status", "To Do")
    if status not in STATUSES:
        raise APIError(400, f"status must be one of: {', '.join(STATUSES)}.")

    due_at = _validate_due_at(payload.get("due_at"))
    return title, description.strip(), status, due_at


def _validate_status(payload: object) -> str:
    if not isinstance(payload, dict):
        raise APIError(400, "Request body must be a JSON object.")
    status = payload.get("status")
    if status not in STATUSES:
        raise APIError(400, f"status must be one of: {', '.join(STATUSES)}.")
    return status


def make_handler(database: TaskDatabase) -> type[BaseHTTPRequestHandler]:
    class TaskRequestHandler(BaseHTTPRequestHandler):
        server_version = "CaseworkerTasks/1.0"

        def _send_json(self, status_code: int, data: object | None) -> None:
            self.send_response(status_code)
            if data is not None:
                body = json.dumps(data).encode("utf-8")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_header("Content-Length", "0")
                self.end_headers()

        def _read_json(self) -> object:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError as error:
                raise APIError(400, "Content-Length must be a valid integer.") from error
            if length <= 0:
                raise APIError(400, "A JSON request body is required.")
            if length > MAX_BODY_SIZE:
                raise APIError(413, "Request body is too large.")
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise APIError(400, "Request body must contain valid JSON.") from error

        def _dispatch(self, method: str) -> None:
            try:
                self._route(method)
            except APIError as error:
                self._send_json(error.status_code, {"error": error.message})
            except sqlite3.Error as error:
                self.log_error("Database operation failed: %s", error)
                self._send_json(500, {"error": "A database error occurred."})

        def _route(self, method: str) -> None:
            path = urlsplit(self.path).path
            if method == "GET" and path in ("/", "/index.html"):
                try:
                    body = INDEX_FILE.read_bytes()
                except OSError as error:
                    raise APIError(500, "The application page could not be loaded.") from error
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/api/tasks":
                if method == "GET":
                    self._send_json(200, {"tasks": database.list_tasks()})
                    return
                if method == "POST":
                    task = database.create_task(*_validate_create(self._read_json()))
                    self._send_json(201, task)
                    return

            match = TASK_ID_PATTERN.fullmatch(path)
            if match:
                task_id = int(match.group(1))
                is_status_route = match.group(2) == "status"
                if is_status_route and method == "PATCH":
                    task = database.update_status(
                        task_id, _validate_status(self._read_json())
                    )
                    if task is None:
                        raise APIError(404, "Task not found.")
                    self._send_json(200, task)
                    return
                if not is_status_route and method == "GET":
                    task = database.get_task(task_id)
                    if task is None:
                        raise APIError(404, "Task not found.")
                    self._send_json(200, task)
                    return
                if not is_status_route and method == "DELETE":
                    if not database.delete_task(task_id):
                        raise APIError(404, "Task not found.")
                    self._send_json(204, None)
                    return

            self._send_json(404, {"error": "Endpoint not found."})

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PATCH(self) -> None:
            self._dispatch("PATCH")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

        def log_message(self, format: str, *args: object) -> None:
            print(f"{self.address_string()} - {format % args}")

    return TaskRequestHandler


def create_server(host: str, port: int, database_path: str | Path) -> ThreadingHTTPServer:
    database = TaskDatabase(database_path)
    return ThreadingHTTPServer((host, port), make_handler(database))


if __name__ == "__main__":
    host = os.environ.get("TASKS_HOST", "127.0.0.1")
    port = int(os.environ.get("TASKS_PORT", "8000"))
    database_path = os.environ.get("TASKS_DB_PATH", str(APP_DIR / "tasks.db"))
    server = create_server(host, port, database_path)
    print(f"Task manager running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
