# sb-backend

Production-ready FastAPI backend for a kids storytelling mobile application.

## Architecture

The project follows a clean repository-service-route structure:

- `app/entity`: SQLAlchemy ORM entities
- `app/model/request`: request DTOs and validation
- `app/model/response`: API response DTOs
- `app/repository`: database access
- `app/service`: business use cases
- `app/routes`: versioned REST endpoints
- `app/core`: config, database, security, logging, exceptions
- `app/middleware`: request tracing and sanitized auth context

All API responses use:

```json
{
  "success": true,
  "message": "Operation successful",
  "data": {},
  "error": null
}
```

## Local Setup

Use Python `3.11` or `3.12`. Python `3.14` may force native builds for packages such as `pydantic-core` and `greenlet` on Windows.

1. Create and activate a virtual environment.

```bash
python -m venv .venv
.\.venv\Scripts\activate
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies.

```bash
pip install -r requirements.txt
```

3. Create environment file.

```bash
cp .env.example .env
```

Update `DATABASE_URL`, `JWT_SECRET_KEY`, and `GOOGLE_CLIENT_ID`.

4. Start MySQL, then run migrations.

Default local database settings:

```env
MYSQL_USER=root
MYSQL_PASSWORD=root
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DB=storybook
DATABASE_URL=mysql+aiomysql://root:root@127.0.0.1:3306/storybook
```

```bash
alembic upgrade head
```

5. Run the API.

```bash
uvicorn app.main:app --reload
```

## Docker

```bash
cp .env.example .env
docker compose up --build
```

Run migrations inside the API container:

```bash
docker compose exec api alembic upgrade head
```

The API runs at `http://localhost:8000`.

## API Docs

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

## Endpoints

Auth:

- `POST /api/v1/auth/signup`
- `POST /api/v1/auth/verify-email-otp`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/google-login`
- `POST /api/v1/auth/add-phone`
- `POST /api/v1/auth/forgot-password`
- `POST /api/v1/auth/reset-password`
- `POST /api/v1/auth/refresh-token`
- `POST /api/v1/auth/logout`

Children:

- `POST /api/v1/children`
- `GET /api/v1/children`
- `PUT /api/v1/children/{child_id}`
- `POST /api/v1/children/select/{child_id}`

## Security Notes

- Passwords are hashed with bcrypt via Passlib.
- OTPs and refresh tokens are stored as SHA-256 hashes.
- Refresh tokens rotate on every refresh.
- Structured logs intentionally avoid passwords, OTPs, and JWTs.
- Login attempts lock the account after repeated failures.
- Rate limiting is applied to sensitive auth endpoints.

## Provider Integrations

`app/utils/email.py` is the email provider boundary. Replace the placeholder implementation with SES, SendGrid, Mailgun, or another provider.

`app/utils/google_oauth.py` validates Google ID tokens with Google's tokeninfo endpoint and checks the configured audience.
