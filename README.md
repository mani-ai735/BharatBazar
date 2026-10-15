# Bharat Bazaar

A B2B wholesale discovery marketplace. Buyers contact sellers directly; the site does not process payment, orders, or delivery.

## Run locally

Requirements: Docker Engine and Docker Compose v2.

1. Copy `.env.example` to `.env` and replace the development secrets.
2. Start everything: `docker compose up --build -d`
3. Open http://localhost:8080. API docs: http://localhost:8000/docs.
4. Seed data is loaded at startup. Demo admin is `admin@example.com` / `ChangeMe123!` (change `SEED_ADMIN_PASSWORD` before deployment).
5. Stop: `docker compose down`. Keep the database volume with this command.

The API runs migrations and seeds idempotently at startup. Readiness: `GET /api/v1/health/ready`; liveness: `GET /api/v1/health/live`.

## Production deployment

Set strong random `JWT_SECRET`, `POSTGRES_PASSWORD`, and `SEED_ADMIN_PASSWORD`; set `CORS_ORIGINS` to the public frontend origin and `VITE_API_URL` to the public API URL (build-time). Configure `SUPPORT_WHATSAPP` in international digits only and `SUPPORT_EMAIL`. Run `docker compose up --build -d`. Put a reverse proxy/load balancer in front, route HTTPS to frontend and API, and enable HSTS/TLS there. Keep uploads and database on private persistent storage; restrict `/uploads` to image content. Back up with `docker compose exec -T db pg_dump -U bazaar -d bharat_bazaar > backup.sql`; restore with `Get-Content backup.sql | docker compose exec -T db psql -U bazaar -d bharat_bazaar` (PowerShell). Protect backup files and test restores regularly.

## Features

- Buyer account registration, login, logout, and password reset. Set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_FROM` to deliver reset codes; without SMTP, the generic response still appears and the development token is written to the API log.
- Public-facing catalog requires login. Approved and published listings only; submitters can see/edit their own requests; admin review is role checked by the API.
- Product search, category/city filters, sort and browser geolocation with manual city fallback; approximate distance from seller coordinates.
- Seller profile/catalog/reviews, buyer reviews and reports, moderation controls, product feature/unpublish/suspend controls.
- Sell form and private request status; image uploads with type/size validation; admin review and explicit publish action.
- Call and prefilled WhatsApp inquiry flow with MOQ validation and confirmation notice.
- Help form, support links, Privacy Policy and Terms.

## API and data

OpenAPI is generated at `/api/v1/openapi.json`; interactive docs at `/docs`. On startup, the initial SQLAlchemy schema is applied idempotently and recorded as `001_initial` in `schema_migrations`; evolve future schema changes with numbered migrations in `backend/migrations/`. Uploaded images live in the `uploads` volume; database in `postgres_data`. No buyer location is stored. Use `.env.example` as the configuration reference. Do not use the demo credentials in production. Configure SMTP for deliverable password reset codes.
