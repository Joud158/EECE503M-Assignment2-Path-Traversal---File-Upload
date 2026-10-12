# EECE503M Assignment 2: Path Traversal and File Upload

Lab 2 hardening of [Meridian Service Desk](https://github.com/mohammad0omar/meridian-servicedesk).
This remains a teaching application with unrelated deliberate vulnerabilities.
Run it locally on the configured `127.0.0.1:5001` address, with fictional data only.

## Run

Start Docker Desktop with Linux containers, then run from this directory:

```powershell
docker compose -f lab/docker-compose.yml up --build -d
```

Open http://127.0.0.1:5001. The first build takes several minutes.
Agent login: `rmasri` / `Agent1234!`. Administrator: `admin` / `Admin1234!`.
The database is seeded automatically. Existing uploaded files are not retroactively validated.

## Implemented controls

- nginx's `^~ /media/` location prevents PHP handler selection. `internal`
  blocks direct requests; the application authorizes registered attachments before
  issuing a canonical, URL-encoded `X-Accel-Redirect`.
- Upload responses use `application/octet-stream`, `Content-Disposition: attachment`,
  and `X-Content-Type-Options: nosniff`, with directory listing disabled.
- Validation is enabled. Only PNG, JPEG and UTF-8 text are accepted; missing,
  unknown and multiple extensions are rejected. PDF and Office documents are
  intentionally excluded because this build does not validate their content.
- Images are decoded and verified before storage, capped at 20 million pixels,
  and re-encoded to remove metadata and trailing non-pixel bytes.
- Ticket and knowledge-base attachments receive UUID storage names. Original
  filenames remain display labels, not storage paths.
- The download endpoint resolves paths and enforces media containment, registration
  and ticket/category access. Public ticket downloads require the ticket email and
  secret key and are restricted to public follow-ups.
- nginx rejects requests over 10 MiB and uses a 15-second body inactivity timeout.
  The application limits each file to 5 MiB and total multipart file bytes to 10 MiB.
  Non-file request data and in-memory file buffering are each limited to 1 MiB.
- Stored attachments are capped at 50 MiB per authenticated uploading user.
  Anonymous uploads are grouped by submitted email; knowledge-base attachments
  share a 50 MiB budget. A 500 MiB overall registered-attachment cap provides
  an additional bound. PostgreSQL transaction locking serializes quota checks.
- Replacement and deletion remove old stored files after the database commit.
- Debug mode is disabled; upload errors are generic, and decision logs contain
  safe metadata rather than raw filenames or file contents. API clients cannot
  supply size or MIME metadata to bypass accounting.

Anonymous email grouping is not verified identity. Database quotas measure
registered attachments, not pre-existing orphan files or unrelated disk usage.
The optional quarantine directory and malware-scanning pipeline are not implemented.

## Verify

```powershell
docker compose -f lab/docker-compose.yml exec -T app python manage.py test helpdesk.test_upload_hardening --noinput
docker compose -f lab/docker-compose.yml exec -T app python manage.py check
docker compose -f lab/docker-compose.yml exec -T nginx nginx -t
```

The 19 regression tests cover validation, size/pixel limits, quotas, image
re-encoding, file cleanup, traversal rejection and download authorization.
Tests use a separate test database and temporary attachment directory.
Capture the worksheet's required Burp/browser before-and-after evidence separately.

After editing nginx, test it first and then reload:

```powershell
docker compose -f lab/docker-compose.yml exec nginx nginx -s reload
```

Source files are mounted and reload automatically. To apply changes to `lab.env`:

```powershell
docker compose -f lab/docker-compose.yml up -d --no-deps --force-recreate app
```

Inspect logs or stop the stack:

```powershell
docker compose -f lab/docker-compose.yml logs -f app
docker compose -f lab/docker-compose.yml down
```

The original project documentation is in `README.rst`, and stack documentation
is in `lab/README.md`.
