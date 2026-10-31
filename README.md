# Caseworker Task Manager

A small task management application for the HMCTS DTS developer challenge. It
uses Python's standard library for the HTTP API and SQLite persistence, with a
responsive browser interface served by the same application.

## Requirements

- Python 3.10 or later
- No third-party Python packages

## Run locally

From this directory:

```powershell
python app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Tasks are stored in
`tasks.db` in this directory and remain available after restarting the server.
The database path, host, and port can be configured with `TASKS_DB_PATH`,
`TASKS_HOST`, and `TASKS_PORT`.

## Run tests

```powershell
python -m unittest discover -s tests -v
```

The tests use temporary SQLite databases and an ephemeral local HTTP server.

## API

All API responses use JSON except `DELETE`, which returns an empty `204`
response. Errors have the shape `{"error": "message"}`. Timestamps must be ISO
8601 date-times, for example `2026-11-01T09:30` or
`2026-11-01T09:30:00+00:00`.

| Method | Endpoint | Description | Success |
|---|---|---|---|
| `POST` | `/api/tasks` | Create a task | `201` with the task |
| `GET` | `/api/tasks` | List tasks ordered by due date | `200` with `{"tasks": [...]}` |
| `GET` | `/api/tasks/{id}` | Retrieve a task | `200` with the task |
| `PATCH` | `/api/tasks/{id}/status` | Update a task's status | `200` with the updated task |
| `DELETE` | `/api/tasks/{id}` | Delete a task | `204` |

Example create request:

```json
{
  "title": "Review case documents",
  "description": "Check the latest submission",
  "status": "To Do",
  "due_at": "2026-11-01T09:30"
}
```

`description` is optional and defaults to an empty string. `status` defaults to
`To Do` when creating a task and accepts `To Do`, `In Progress`, or `Done`.
Titles are required (up to 200 characters), descriptions are limited to 2,000
characters, and invalid input returns `400`. Requests for unknown task IDs
return `404`.

## User interface

The page supports creating tasks, viewing and filtering the task list, changing
task status, and deleting tasks. It is responsive and uses accessible form
labels and status/error announcements.
