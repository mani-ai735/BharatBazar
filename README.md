# KhetX

A B2B wholesale discovery marketplace. Buyers contact sellers directly; the site does not process payment, orders, or delivery.

## Run locally

Requirements: Docker Engine and Docker Compose v2.

1. Copy `.env.example` to `.env` and replace the development secrets.
2. Start everything: `docker compose up --build -d`
3. Open http://localhost:8081. API docs: http://localhost:8000/docs.
4. Seed marketplace demo listings are loaded at startup. Create an administrator from a server terminal with `docker compose exec api python provision_admin.py`; the command prompts for the admin identity and password and never prints or stores the password in frontend configuration.
5. Stop: `docker compose down`. Keep the database volume with this command.

The API applies its schema idempotently and seeds demo listings at startup. Admins sign in only at `/admin/login`; first sign-in enrolls Google Authenticator-compatible TOTP, and every later sign-in requires the current six-digit code. Admin TOTP secrets are encrypted in the database using a key derived from `JWT_SECRET`; keep that secret stable or admins will need to re-enroll. Admin sessions use an HttpOnly, Secure, SameSite=Lax cookie and expire after 30 minutes of inactivity. Set `ADMIN_COOKIE_SECURE=false` only for local HTTP development if the browser does not accept localhost Secure cookies. To reset a locked-out administrator authenticator from the server shell, run `docker compose exec api python reset_admin_2fa.py`; it requires the admin email and an explicit confirmation. Readiness: `GET /api/v1/health/ready`; liveness: `GET /api/v1/health/live`.

## Production deployment

Set strong random `JWT_SECRET` (at least 32 characters) and `POSTGRES_PASSWORD`; set `CORS_ORIGINS` to the public frontend origin and `VITE_API_URL` to the public API URL (build-time). Keep `ADMIN_COOKIE_SECURE=true` in production. Provision administrators only from the server shell with `docker compose exec api python provision_admin.py`. Configure `SUPPORT_WHATSAPP` in international digits only and `SUPPORT_EMAIL`. Run `docker compose up --build -d`. Put a reverse proxy/load balancer in front, route HTTPS to frontend and API, and enable HSTS/TLS there. Keep uploads and database on private persistent storage; restrict `/uploads` to image content. Back up with `docker compose exec -T db pg_dump -U bazaar -d bharat_bazaar > backup.sql`; restore with `Get-Content backup.sql | docker compose exec -T db psql -U bazaar -d bharat_bazaar` (PowerShell). Protect backup files and test restores regularly.

## Features

- Buyer account registration, login, logout, and password reset. Set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_FROM` to deliver reset codes; without SMTP, the generic response still appears and the development token is written to the API log.
- Public catalog and search show approved, published product cards without login. Buyers must sign in to open product details and seller profiles; anonymous catalog responses omit seller phone, WhatsApp, full address, and pincode. Submitters can see/edit their own requests; admin review is role checked by the API.
- Product search, category/city filters, sort and browser geolocation with manual city fallback; approximate distance from seller coordinates.
- Seller profile/catalog/reviews, buyer reviews and reports, moderation controls, product feature/unpublish/suspend controls.
- Sell form and private request status; image uploads with type/size validation; admin review and explicit publish action.
- Call and prefilled WhatsApp inquiry flow with MOQ validation and confirmation notice.
- Help form, support links, Privacy Policy and Terms.

## Google sign-in setup

Google sign-in is verified by the API and creates buyer accounts only. In Google Cloud Console, create an OAuth 2.0 **Web application** client ID and add these Authorized JavaScript origins: `http://localhost:8081` for local use, and your HTTPS website origin (for example, your current ngrok origin) for public use. Set `GOOGLE_CLIENT_ID` in `.env` to that client ID, then rebuild and restart the stack with `docker compose up --build -d`. The client ID is public configuration; never put a client secret in the frontend or `.env` for this ID-token flow. The backend verifies Google's signed ID token and links accounts only for verified Gmail or Google Workspace identities. New Google accounts start with the Buyer role.

## API and data

OpenAPI is generated at `/api/v1/openapi.json`; interactive docs at `/docs`. On startup, the initial SQLAlchemy schema is applied idempotently and recorded as `001_initial` in `schema_migrations`; evolve future schema changes with numbered migrations in `backend/migrations/`. Uploaded images live in the `uploads` volume; database in `postgres_data`. No buyer location is stored. Use `.env.example` as the configuration reference. Do not use the demo credentials in production. Configure SMTP for deliverable password reset codes.


Account emails are unique, and non-empty account phone numbers are unique after punctuation is removed. The admin dashboard includes an approximate unique signed-in browser count. A random browser identifier is stored as a hash with first/last-seen timestamps; it does not identify a physical device, and clearing browser storage can result in a new count. Only admins can view the aggregate.

Seller dashboard: signed-in submitters can open **My products** from the main navigation or footer to see their listing status, admin notes, published links, and summary counts. Owners may request re-review for suspended listings; this returns the listing to the admin queue but never republishes it automatically. The dashboard includes a non-functional subscription placeholder for a future subscription model; no billing or payment handling is enabled.

Password reset: reset emails contain a single-use link that expires after 30 minutes. Set `PUBLIC_APP_URL` to the website origin used in those emails and configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, and `SMTP_STARTTLS` in `.env`. For Gmail, use `smtp.gmail.com`, port `587`, STARTTLS, and a Google App Password; do not use or share your normal Google account password. After changing these settings, apply them with `docker compose up --build -d api`. Without SMTP configured, reset links are written only to backend development logs and no email is delivered.
