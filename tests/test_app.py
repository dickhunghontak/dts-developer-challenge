import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import TaskDatabase, create_server


class TaskDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = TaskDatabase(Path(self.temp_dir.name) / "tasks.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_and_list_orders_tasks_by_due_date(self):
        later = self.database.create_task(
            "Later", "", "To Do", "2026-11-02T10:00:00"
        )
        earlier = self.database.create_task(
            "Earlier", "Prepare papers", "In Progress", "2026-11-01T10:00:00"
        )

        self.assertEqual(self.database.list_tasks(), [earlier, later])
        self.assertEqual(earlier["description"], "Prepare papers")

    def test_update_and_delete_task(self):
        task = self.database.create_task(
            "Review case", "", "To Do", "2026-11-01T10:00:00"
        )

        updated = self.database.update_status(task["id"], "Done")
        self.assertEqual(updated["status"], "Done")
        self.assertTrue(self.database.delete_task(task["id"]))
        self.assertIsNone(self.database.get_task(task["id"]))
        self.assertFalse(self.database.delete_task(task["id"]))


class TaskAPITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.server = create_server(
            "127.0.0.1", 0, Path(self.temp_dir.name) / "tasks.sqlite3"
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with urlopen(request) as response:
                body = response.read()
                return response.status, json.loads(body) if body else None
        except HTTPError as error:
            body = error.read()
            return error.code, json.loads(body) if body else None

    def test_task_lifecycle(self):
        status, created = self.request(
            "POST",
            "/api/tasks",
            {
                "title": "Contact applicant",
                "description": "Confirm availability",
                "due_at": "2026-11-01T09:30",
            },
        )
        self.assertEqual(status, 201)
        task_id = created["id"]

        self.assertEqual(self.request("GET", f"/api/tasks/{task_id}"), (200, created))
        self.assertEqual(self.request("GET", "/api/tasks")[1]["tasks"], [created])

        status, updated = self.request(
            "PATCH", f"/api/tasks/{task_id}/status", {"status": "In Progress"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["status"], "In Progress")

        self.assertEqual(self.request("DELETE", f"/api/tasks/{task_id}"), (204, None))
        self.assertEqual(self.request("GET", f"/api/tasks/{task_id}")[0], 404)

    def test_invalid_task_is_rejected(self):
        status, response = self.request(
            "POST",
            "/api/tasks",
            {"title": " ", "due_at": "not-a-date"},
        )

        self.assertEqual(status, 400)
        self.assertIn("error", response)
        self.assertEqual(self.request("PATCH", "/api/tasks/1/status", {"status": "Other"})[0], 400)

    def test_missing_task_returns_not_found(self):
        self.assertEqual(self.request("GET", "/api/tasks/999")[0], 404)
        self.assertEqual(self.request("DELETE", "/api/tasks/999")[0], 404)

    def test_web_page_is_served(self):
        with urlopen(self.base_url) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"Caseworker task manager", response.read())


if __name__ == "__main__":
    unittest.main()
