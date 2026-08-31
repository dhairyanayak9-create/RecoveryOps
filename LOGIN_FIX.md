# Login redirect fix

The login/session flow was patched:
- JWT `sub` is encoded as a string.
- JWT lookup uses the current SQLAlchemy session API.
- Authentication runs before role authorization.
- The browser no longer immediately reloads on a protected-request 401.

## Steps
1. Stop the old Flask server.
2. Extract this ZIP.
3. Open the site in Incognito once (or clear site data for localhost).
4. Run `start_backend.bat`.
5. Open http://127.0.0.1:5000
6. Login: `admin` / `admin123`
