# ctr

Django API (`backend/`) with a Next.js frontend (`frontend/`).

## Setup

You'll need [Docker](https://docs.docker.com/get-docker/) and `make`. For
working outside the containers (linting, adding dependencies) you'll also want
[uv](https://docs.astral.sh/uv/) and Node 24.

1. Start everything:
   ```sh
   make up
   ```
   The first run creates `.env.dev` from `.env.sample`; edit it to change
   credentials.
2. Create an admin user:
   ```sh
   make superuser
   ```
3. Load the starter train cars, stations and kiosks from
   `backend/core/starter_data.json` (or use the button on the employee page):
   ```sh
   make starter-data
   ```
4. Visit:
   - Frontend: http://localhost:3000
   - API: http://localhost:8000/api/health/
   - API docs: http://localhost:8000/api/docs
   - Admin: http://localhost:8000/admin/
   - Employee portal: http://localhost:3000/employee

Migrations are applied automatically when the `django` container starts. If
port 8000 or 3000 is taken, export `DJANGO_PORT` / `FRONTEND_PORT` in your
shell before running any `make` command.

## Common tasks

Run `make` to list the commands.

| Command             | What it does                                              |
| ------------------- | --------------------------------------------------------- |
| `make up`           | Build and start everything in the background              |
| `make down`         | Stop and remove the containers (keeps the database)       |
| `make logs`         | Follow logs from all services                             |
| `make backend`      | Run Django + Postgres in the foreground                   |
| `make frontend`     | Run the frontend (and the backend it needs) in foreground |
| `make migrations`   | Create migrations from model changes and apply them       |
| `make superuser`    | Create an admin user                                      |
| `make starter-data` | Add starter cars, stations and kiosks (if missing)        |
| `make test`         | Run backend tests: `make test ARGS="-k health"`           |
| `make format`       | Auto-fix lint issues and format backend and frontend      |

`migrations`, `superuser`, `starter-data` and `test` run inside the containers,
so the stack must be up. `format` runs on the host and needs the host
dependencies below.

Anything else goes through compose directly, for example:

```sh
docker compose -f compose.dev.yml exec django python manage.py shell
```

To add a dependency, run it on the host and then rebuild:

```sh
cd backend && uv add <package>            # or: uv add --dev <package>
cd frontend && npm install <package>
make up
```

For editor support, formatting and the pre-commit hooks, install the host
dependencies once:

```sh
cd backend && uv sync && uv run pre-commit install
cd frontend && npm install
```

## How the pieces fit

- The browser only talks to Next.js. `frontend/next.config.ts` proxies
  `/api/*` to Django, so there is no CORS setup and session/CSRF cookies are
  same-origin.
- Server-side code fetches Django directly with `apiFetch` from
  `frontend/src/lib/api.ts`, using the `API_URL` environment variable.
- API endpoints are built with [Django Ninja](https://django-ninja.dev/) and
  require an authenticated session by default; opt an endpoint out with
  `auth=None` (see `backend/core/api.py`). Add routers in `backend/ctr/api.py`.
- `core.User` is a custom user model. Add fields to it rather than swapping
  the user model later.
