# LMArenaBridge

LMArenaBridge is a local FastAPI bridge that exposes Arena (formerly LMArena)
models through OpenAI-compatible chat/model endpoints and an Anthropic-style
messages endpoint. It includes a small web dashboard for managing the upstream
Arena authentication token, bridge API keys, model refreshes, and usage stats.

> **Unofficial project:** Arena does not publish a documented public developer
> API for this workflow. The bridge depends on Arena's internal web endpoints,
> so upstream changes can break it without notice. Use only your own Arena
> account/session, follow Arena's terms, and respect upstream rate limits.

## Verification status

The repository's automated tests are offline tests. They use mocks and local
ASGI requests; they do **not** prove that a live Arena chat request works on a
particular day or account.

Use these two checks to distinguish local readiness from live verification:

| Check | What it proves | Command |
|---|---|---|
| Offline doctor | Python/packages, fetched Camoufox browser, and local JSON state are plausible | `python -m src.doctor` |
| Live smoke | One real request completed through your running bridge and Arena account | `python scripts/live_smoke.py` |

Do not treat a green offline test suite as proof of live upstream
compatibility. `scripts/live_smoke.py` is intentionally not run in CI.

## Requirements

- Python 3.10 or newer
- The pinned dependencies in `requirements.txt`
- A one-time Camoufox browser download (`python -m camoufox fetch`)
- Your own authenticated Arena session/token
- Optional: Chrome/Chromium if you use the bridge's Chrome fetch transport

The pinned set was verified with the offline test suite on Python 3.11. In
particular, Camoufox 0.5.8 requires `playwright<1.63`, so do not casually
float or upgrade Playwright independently.

## Installation

Clone this fork and create an isolated environment:

```bash
git clone https://github.com/Warzonesiddiki/LMArenaBridge-UPDATEDNEW.git
cd LMArenaBridge-UPDATEDNEW
python -m venv .venv
```

Activate the environment:

```bash
# bash/zsh
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Install and fetch the browser runtime:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m camoufox fetch
python -m src.doctor
```

Run the server from the repository root. By design, runtime state is stored in
`config.json` and `models.json` relative to the current working directory.

```bash
python -m src.main
```

Then open:

- Login: <http://localhost:8000/login>
- Dashboard: <http://localhost:8000/dashboard>
- API base: <http://localhost:8000/api/v1>
- Health: <http://localhost:8000/api/v1/health>

## First-time configuration

1. Log in to the dashboard. The retained upstream default password is
   `admin`; change it in `config.json` before exposing the service to anyone
   else.
2. Add **your own** Arena `arena-auth-prod-v1` token in the dashboard. Treat it
   like a password: never commit it, paste it into an issue, or include it in
   logs shared with other people.
3. Use **Refresh Tokens & Models** in the dashboard to populate/refresh the
   model list and Cloudflare clearance state.
4. Create at least one bridge API key in the dashboard.
5. Run the local checks again:

   ```bash
   python -m src.doctor --check-server
   ```

6. When you are ready to make one real request, run the live smoke script
   described below.

The bridge binds to `0.0.0.0:8000` by default. That behavior was intentionally
retained for compatibility, but it means the service may be reachable from
other devices on your network. Use a firewall, VPN, SSH tunnel, or trusted
reverse proxy if you do not want LAN access.

## Bridge API-key behavior

API clients can authenticate with any of these forms:

- `Authorization: Bearer <bridge-api-key>`
- `Authorization: <bridge-api-key>` (legacy raw form)
- `x-api-key: <bridge-api-key>`

When at least one bridge API key is configured, a missing or invalid key is
rejected with HTTP 401 by default. This prevents clients that omit a key from
silently using another configured key.

Two compatibility exceptions remain:

| Situation | Behavior |
|---|---|
| No bridge API keys are configured | Anonymous bootstrap access is allowed so the first setup can be completed. Create a key as soon as setup works. |
| `allow_missing_api_key=true` in `config.json` | A missing key falls back to the first configured key. This is only for legacy empty-key clients and should remain `false` for normal use. |

## API examples

Set a bridge API key in your shell. Do not hard-code it in source files.

```bash
# bash/zsh
export LMAB_API_KEY="<bridge-api-key>"
```

```powershell
# Windows PowerShell
$env:LMAB_API_KEY = "<bridge-api-key>"
```

List models:

```bash
curl http://localhost:8000/api/v1/models \
  -H "Authorization: Bearer $LMAB_API_KEY"
```

OpenAI-compatible chat completion:

```bash
curl http://localhost:8000/api/v1/chat/completions \
  -H "Authorization: Bearer $LMAB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<model-id-from-/models>",
    "messages": [
      {"role": "user", "content": "Reply with exactly: bridge smoke ok"}
    ],
    "stream": false
  }'
```

Anthropic-style messages request:

```bash
curl http://localhost:8000/api/v1/messages \
  -H "x-api-key: $LMAB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<model-id-from-/models>",
    "max_tokens": 128,
    "messages": [
      {"role": "user", "content": "Reply with exactly: bridge smoke ok"}
    ]
  }'
```

Notes:

- API errors under `/api/v1/*` use an OpenAI-shaped error envelope, except
  `/api/v1/messages`, which uses an Anthropic-shaped envelope.
- Token `usage` values are estimates (roughly four characters per token), not
  authoritative Arena/provider billing counts.
- Some provider-specific request fields are not forwarded upstream; the core
  supported request shape is the model, messages, and streaming mode.
- `/api/v1/health` returns HTTP 200 only when models, durable upstream auth,
  and `cf_clearance` are present. Otherwise it returns HTTP 503 with the
  individual readiness checks.

## Interactive example client

`chat_interactive.py` is an optional terminal client based on the OpenAI
Python library:

```bash
python -m pip install openai
python chat_interactive.py
```

It reads `LMAB_API_KEY` and optional `LMAB_BASE_URL` from the environment.
There is no API key stored in the script.

## Offline diagnostics

The doctor is read-only with respect to bridge state and never prints token or
API-key values:

```bash
python -m src.doctor
python -m src.doctor --check-server
python -m src.doctor --json
```

It checks:

- Python version and platform
- Installed runtime package versions and importability
- The Playwright/Camoufox compatibility ceiling
- Whether a Camoufox executable has been fetched
- Presence/shape of `config.json` and `models.json`
- Whether durable upstream auth and bridge API keys are configured
- Known-field config schema issues (field names/counts only, never values)
- Retained insecure defaults such as the default dashboard password
- Optionally, the already-running server's `/api/v1/health` response

A `WARN` does not necessarily mean the program cannot start, but it can mean
live chat is not ready yet. A `FAIL` causes a non-zero exit code.

## Live smoke test (user machine only)

Start the bridge first, configure your own Arena token and a bridge API key,
then run:

```bash
python scripts/live_smoke.py
```

Useful variants:

```bash
# Choose a model explicitly
python scripts/live_smoke.py --model "<model-id>"

# Exercise streaming instead of a non-streaming completion
python scripts/live_smoke.py --stream

# Check only local health/model listing without sending an upstream chat
python scripts/live_smoke.py --skip-chat
```

The script sends at most one chat request. It prints HTTP statuses, timings,
the selected model, response length, and a short excerpt. It does not print
the bridge API key or request headers. Share only this sanitized output when
asking for help.

If it fails, also share:

```bash
python -m src.doctor --check-server
```

Never share `config.json`, Arena tokens, cookies, bridge API keys, or raw
browser storage.

## Configuration and runtime files

The server currently reads and writes these files in the working directory:

| File | Purpose |
|---|---|
| `config.json` | Dashboard password, Arena tokens/cookies, bridge API keys, Cloudflare clearance, usage stats, and compatibility flags. Contains secrets; do not commit or share it. |
| `models.json` | Cached Arena model metadata used by `/api/v1/models` and chat routing. |

Known configuration fields are schema-normalized on load and save: invalid
booleans, token/API-key lists, usage counts, window modes, and known
timeout/retry values are repaired or bounded, while unknown keys are preserved
for forward compatibility. The doctor reports schema warnings without printing
secret values.

Important configuration keys include:

| Key | Default | Meaning |
|---|---:|---|
| `allow_missing_api_key` | `false` | Legacy fallback for clients that send no bridge API key. Keep `false` normally. |
| `persist_arena_auth_cookie` | `true` | When true, a valid `arena-auth-prod-v1` cookie captured from browser state may be saved into durable `auth_tokens`. When false, it remains only an in-memory fallback. |

Set `LMAB_DEBUG=0` to disable the historical verbose debug logging when the
bridge is exposed beyond a trusted local environment. Secret and prompt values
are redacted/fingerprinted in the hardened log paths, but less logging is still
safer.

### Logging format

Text logging remains the default for compatibility. For machine-readable logs,
set:

```bash
LMAB_LOG_FORMAT=json
LMAB_LOG_LEVEL=INFO
```

JSON mode emits one redacted JSON object per record with timestamp, level,
logger, message, and optional event/fields. `LMAB_LOG_LEVEL` accepts the
standard Python logging level names; invalid values fall back to the level
implied by `LMAB_DEBUG`.

## Security notes and retained defaults

This project was hardened without changing several compatibility defaults.
Understand these risks before exposing it:

- The server binds to `0.0.0.0:8000`.
- The initial dashboard password is `admin` until changed.
- CORS remains permissive for development/client compatibility.
- The userscript/proxy compatibility endpoints do not use a separate shared
  secret.
- `config.json` contains high-value session credentials and bridge keys.
- Before the first bridge API key is created, API bootstrap access is
  anonymous.

Recommended minimum posture:

1. Change the dashboard password.
2. Create a bridge API key and keep `allow_missing_api_key=false`.
3. Do not expose port 8000 directly to the public internet.
4. Restrict access with a firewall, VPN, SSH tunnel, or authenticated reverse
   proxy.
5. Set `LMAB_DEBUG=0` outside local debugging.
6. Protect and back up `config.json` like a credential file; rotate/revoke the
   Arena session and bridge keys if it leaks.

Dashboard login sessions now expire after 24 hours. Dashboard rendering escapes
untrusted names/model fields and masks stored Arena/Cloudflare secrets instead
of printing their prefixes.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `python -m src.doctor` says Camoufox is missing | Browser binary was never fetched | Run `python -m camoufox fetch`, then rerun the doctor. |
| Playwright check fails with `playwright<1.63` | Dependencies were floated/upgraded independently | Reinstall the pinned `requirements.txt` in a clean virtual environment. |
| `/api/v1/health` returns 503 | Models, durable Arena auth, or `cf_clearance` is missing | Run `python -m src.doctor --check-server`; add your own Arena token and refresh models in the dashboard. |
| API returns 401 | Bridge API keys exist, but the client sent no key or the wrong key | Send `Authorization: Bearer <key>` or `x-api-key`. Only use `allow_missing_api_key=true` for a legacy empty-key client. |
| Model list is empty | Model cache was not populated or the Arena session is no longer valid | Refresh tokens/models in the dashboard and confirm your Arena session is still signed in. |
| Live smoke cannot connect | Bridge is not running or is on another base URL | Start `python -m src.main`; pass `--base-url` to the smoke script if needed. |
| Upstream returns 429 | Arena/provider rate limiting | Stop retrying aggressively and wait. Respect `Retry-After` and account limits. |
| Upstream access is denied or a human-verification challenge appears | The account/session/request is not authorized for that automated path | Use Arena normally in your own browser and a valid session. This project does not provide a supported CAPTCHA/anti-bot bypass workflow. |
| Tests create or modify the wrong JSON files | You are on an old checkout or changed test isolation | Update the checkout; current tests point config/model paths at per-test temporary files. |

## Development checks

Install the test/lint tools in your development environment:

```bash
python -m pip install pytest==9.1.1 pytest-asyncio==1.4.0 ruff==0.17.2
```

Run the offline checks:

```bash
python -m pytest tests/ -q
ruff check src/ --select F821
```

Build a wheel to verify packaging:

```bash
python -m pip install build
python -m build --wheel
```

The wheel must contain the `src` package (including `src/main.py` and
`src/transport.py`); CI checks this explicitly.

## Known limitations

- Arena's internal endpoints and page structure can change at any time.
- Live chat cannot be verified by CI because CI has no user Arena account,
  browser session, or permission to spend upstream requests.
- Token usage is estimated locally.
- The bridge keeps chat sessions in memory. They are now bounded per API key
  and per session, but they are not durable across restarts.
- The historical CAPTCHA/vision-solver paths are not a supported repair path
  in this stabilization work. Access-denied and human-verification responses
  should be surfaced honestly rather than hidden or bypassed.
- Structured JSON logging and value-free config schema normalization are
  implemented, but the transport layer remains intentionally conservative:
  only the duplicated streaming browser-fetch selection was deduplicated. A
  larger transport rewrite should wait for repeatable live verification on a
  user machine.

## License

See [`LICENSE`](LICENSE).
