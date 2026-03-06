# Plan: Auto-Renew YouTube & Spotify Credentials

## Problem

Flacfetch sends daily Pushbullet notifications that credentials need manual attention. The current workflow requires the user to:
1. Run `flacfetch spotify-auth login` (opens browser for OAuth)
2. Run `flacfetch spotify-auth upload` (pushes token to GCP Secret Manager)
3. For YouTube: export cookies from browser and upload

This is tedious and happens almost every day.

## Root Cause Analysis

### Spotify
The Spotify OAuth flow uses spotipy with refresh tokens. The code at `credential_check.py:88-96` calls `auth_manager.get_cached_token()` which returns `None` when there's no valid cached token. When spotipy refreshes an expired access token, it writes the new token to the cache file on disk (`/opt/flacfetch/.cache`). However:

1. **Token persistence gap**: When the VM restarts, the startup script overwrites `/opt/flacfetch/.cache` with the (possibly stale) token from Secret Manager. If spotipy had refreshed the token on the server since the last Secret Manager upload, that refreshed token is lost.
2. **Refresh token revocation**: Spotify may revoke refresh tokens if they detect suspicious patterns or after extended periods, requiring a full re-authentication via browser.

**Quick fix to try first**: After a successful token refresh on the server, write the updated token back to Secret Manager. This ensures VM restarts don't lose refreshed tokens. This alone may solve the Spotify issue if the refresh token isn't actually being revoked.

### YouTube
YouTube cookies expire naturally (Google session cookies typically last days to weeks). There is no refresh mechanism - the only fix is re-exporting from a logged-in browser. This fundamentally requires browser automation to solve.

## Account Setup

**Dedicated account**: `nomadflacfetch@gmail.com` - used for both YouTube and Spotify.

- Google account has 2FA **disabled** for simpler automation
- Spotify account is configured to allow **"Login with Google"**
- This means a single Google login session in the browser handles both services

## Solution: Browser-Based Credential Keeper

Add a lightweight browser automation service ("credential keeper") that runs on the flacfetch GCE VM alongside the existing service. It maintains a **single persistent Chrome profile** logged into Google, which provides:
- YouTube cookies (directly from the Google session)
- Spotify OAuth tokens (via "Continue with Google" on the Spotify OAuth page)

### Architecture

```
flacfetch GCE VM (e2-small -> e2-medium)
+--------------------------------------------------+
|                                                    |
|  flacfetch API (port 8080)     <-- existing        |
|       |                                            |
|       | uploads via internal API                   |
|       |                                            |
|  credential-keeper (systemd)   <-- new             |
|       |                                            |
|       +-- Patchright (Chromium)                    |
|       |     +-- single persistent profile          |
|       |           (logged into Google)              |
|       |                                            |
|       +-- Xvfb (virtual display)                   |
|                                                    |
|  /mnt/flacfetch-data/browser-profiles/  <-- new    |
|       +-- google/    (single profile)              |
+--------------------------------------------------+
```

### Key Design Decisions

1. **Single browser profile** - Since Spotify uses "Login with Google", one Chrome profile logged into `nomadflacfetch@gmail.com` handles both YouTube and Spotify. No need for separate profiles or contexts.

2. **Patchright over stock Playwright** - Anti-detection is essential for YouTube (Google aggressively detects automation). Spotify is less aggressive but benefits too.

3. **Headed mode via Xvfb** - Same approach as AquaBot. Avoids headless detection.

4. **Persistent browser profile on data disk** - The browser profile (with cookies, localStorage, session data) is stored on the persistent disk (`/mnt/flacfetch-data/browser-profiles/google/`). This means the Google session survives VM restarts without needing to re-login.

5. **No residential proxy needed** - The flacfetch VM already has a static IP. Since this is a single account accessing its own data (not scraping), a datacenter IP should be fine. We can add proxy support later if needed.

6. **No email 2FA handling needed** - Google account has 2FA disabled, and Spotify uses "Login with Google" (bypassing Spotify's own email 2FA entirely). Gmail IMAP is kept as a **fallback** in case Spotify ever requires its own login.

7. **Python implementation** - The credential keeper is written in Python (matching flacfetch) using `patchright` (the Python package). No Node.js dependency.

8. **VM size upgrade** - The current `e2-small` (0.5 vCPU, 2GB RAM) is tight for running Chromium. Upgrade to `e2-medium` (1 vCPU, 4GB RAM).

## Implementation Plan

### Phase 1: Spotify Token Persistence Fix (quick win)

**Goal**: Fix the token persistence gap so refreshed Spotify tokens survive VM restarts.

**Changes**:
- `flacfetch/providers/spotify.py` or `credential_check.py`: After a successful token refresh, write the updated token back to GCP Secret Manager
- Add `google-cloud-secret-manager` to dependencies (or use `gcloud` CLI subprocess)
- This alone may eliminate daily Spotify notifications

**Files to modify**:
- `flacfetch/api/services/credential_check.py` - Add token writeback after refresh
- `pyproject.toml` - Add `google-cloud-secret-manager` dependency (if using Python SDK)

### Phase 2: Credential Keeper Service

**Goal**: Build the browser automation service that maintains a logged-in Google session and uses it for both YouTube cookies and Spotify OAuth.

#### 2a: Core Browser Infrastructure

**New files**:
- `flacfetch/credential_keeper/__init__.py`
- `flacfetch/credential_keeper/__main__.py` - Entry point for `python -m flacfetch.credential_keeper`
- `flacfetch/credential_keeper/browser.py` - Patchright browser lifecycle management
- `flacfetch/credential_keeper/keeper.py` - Main keeper orchestrator

**`browser.py` responsibilities**:
- Launch Chromium via Patchright in headed mode
- Use a persistent browser profile directory (`/mnt/flacfetch-data/browser-profiles/google/`)
- Manage browser lifecycle (launch, keep alive, restart on crash)

**`keeper.py` responsibilities**:
- Scheduling loop (refresh every N hours)
- Orchestrate: check Google login -> refresh YouTube cookies -> refresh Spotify token
- Write status to status file
- Report failures via Pushbullet

#### 2b: Google Login

**New file**: `flacfetch/credential_keeper/google_login.py`

**Purpose**: Log into Google when needed (first run or session expired).

**Flow**:
1. Navigate to `accounts.google.com`
2. Check if already logged in (look for profile avatar or account info)
3. If not logged in:
   a. Enter email (`nomadflacfetch@gmail.com`)
   b. Click Next
   c. Enter password
   d. Click Next
   e. Handle any "verify it's you" prompts (recovery email, etc.)
4. Verify login succeeded

**Login detection**: Navigate to `myaccount.google.com` - if it shows account info, we're logged in. If it redirects to login page, we're not.

**When this runs**: Only when the keeper detects the Google session is no longer valid (cookie extraction fails or YouTube shows "Sign in").

#### 2c: YouTube Cookie Keeper

**New file**: `flacfetch/credential_keeper/youtube.py`

**Flow**:
1. Navigate to `youtube.com` using the shared browser context (already logged into Google)
2. Verify logged in (check for avatar/user menu, absence of "Sign in" button)
3. If not logged in: trigger Google login (2b), then retry
4. Extract cookies from browser context in Netscape format
5. Upload to flacfetch via `POST /config/youtube-cookies` (localhost)
6. Also update GCP Secret Manager directly

**Cookie extraction**: Patchright exposes `context.cookies()` which returns all cookies. Convert to Netscape format:
```python
async def extract_netscape_cookies(context) -> str:
    """Extract cookies from browser context in Netscape format for yt-dlp."""
    cookies = await context.cookies(["https://www.youtube.com", "https://www.google.com"])
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        secure = "TRUE" if c.get("secure") else "FALSE"
        http_only = "TRUE"  # Default for yt-dlp compatibility
        expiry = str(int(c.get("expires", 0)))
        domain = c["domain"]
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        lines.append(f"{domain}\t{include_subdomains}\t{c['path']}\t{secure}\t{expiry}\t{c['name']}\t{c['value']}")
    return "\n".join(lines)
```

**Schedule**: Every 6-12 hours (Google cookies typically last 2+ weeks, so this is very conservative).

#### 2d: Spotify Token Keeper

**New file**: `flacfetch/credential_keeper/spotify.py`

**Flow**:
1. Using the same browser context (already logged into Google):
2. Build the Spotify OAuth authorize URL (same parameters spotipy uses):
   ```
   https://accounts.spotify.com/authorize?
     client_id=...&
     response_type=code&
     redirect_uri=http://127.0.0.1:8888/callback&
     scope=user-read-playback-state+user-modify-playback-state+streaming
   ```
3. Navigate to it in the browser
4. Spotify shows the authorization page - click **"Continue with Google"**
5. Google auto-approves since the browser is already logged into the correct Google account
6. Spotify auto-approves since the user has previously authorized the app
7. Browser redirects to `http://127.0.0.1:8888/callback?code=...`
8. Capture the authorization code from the redirect URL
9. Exchange code for access + refresh token using spotipy's `SpotifyOAuth.get_access_token(code)`
10. Save token to flacfetch cache file (`/opt/flacfetch/.cache`) and upload to Secret Manager

**Key detail**: The redirect to `localhost:8888/callback` will fail (nothing is listening there), but that's fine - we just need to capture the URL from the browser's navigation event before the page fails to load. Patchright can intercept this via `page.expect_navigation()` or by watching `page.url` after the redirect.

**Schedule**: Every 12-24 hours (safety net; Phase 1 token writeback handles normal refresh).

**Fallback - email 2FA**: If Spotify ever doesn't accept "Continue with Google" and requires direct login with email 2FA, keep a minimal IMAP reader as fallback:

```python
# flacfetch/credential_keeper/email.py (fallback only)
import imaplib
import email
import re
import time

def get_spotify_verification_code(
    gmail_address: str,
    app_password: str,
    timeout_seconds: int = 120,
    poll_interval: int = 5,
) -> str:
    """Poll Gmail IMAP for a Spotify verification code. Fallback for direct Spotify login."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(gmail_address, app_password)
        mail.select("INBOX")
        _, messages = mail.search(None, '(FROM "spotify" UNSEEN)')
        if messages[0]:
            msg_ids = messages[0].split()
            _, msg_data = mail.fetch(msg_ids[-1], "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            body = msg.get_payload(decode=True).decode() if not msg.is_multipart() else ""
            match = re.search(r'\b(\d{6})\b', body)
            if match:
                mail.store(msg_ids[-1], '+FLAGS', '\\Seen')
                mail.logout()
                return match.group(1)
        mail.logout()
        time.sleep(poll_interval)
    raise TimeoutError("No Spotify verification code received")
```

#### 2e: GCP Secrets for Account Credentials

**New secrets** (store in GCP Secret Manager):
- `flacfetch-account-email` - `nomadflacfetch@gmail.com`
- `flacfetch-account-password` - Google account password
- `gmail-app-password` - Gmail app password for IMAP (fallback only, but good to have)

Only 3 secrets needed since Spotify uses "Login with Google" (no separate Spotify password).

### Phase 3: Deployment & Infrastructure

**Changes to infrastructure**:

1. **VM upgrade**: `e2-small` -> `e2-medium` in `infrastructure/__main__.py`

2. **Startup script additions**:
   - Install Xvfb: `apt-get install -y xvfb`
   - Install Patchright: `pip install patchright && patchright install chromium`
   - Create browser profile directory on persistent disk
   - Fetch account credentials from Secret Manager
   - Create `credential-keeper` and `xvfb` systemd services

3. **New systemd services**:
   ```ini
   # /etc/systemd/system/xvfb.service
   [Unit]
   Description=Xvfb Virtual Display
   After=network.target

   [Service]
   Type=simple
   ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x720x24 -nolisten tcp
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

   ```ini
   # /etc/systemd/system/credential-keeper.service
   [Unit]
   Description=Flacfetch Credential Keeper (browser automation)
   After=network.target xvfb.service flacfetch.service
   Requires=xvfb.service

   [Service]
   Type=simple
   User=root
   WorkingDirectory=/opt/flacfetch
   Environment="DISPLAY=:99"
   Environment="FLACFETCH_API_KEY=..."
   Environment="FLACFETCH_ACCOUNT_EMAIL=nomadflacfetch@gmail.com"
   Environment="FLACFETCH_ACCOUNT_PASSWORD=..."
   Environment="GMAIL_APP_PASSWORD=..."
   Environment="SPOTIPY_CLIENT_ID=..."
   Environment="SPOTIPY_CLIENT_SECRET=..."
   Environment="BROWSER_PROFILE_DIR=/mnt/flacfetch-data/browser-profiles"
   ExecStart=/opt/flacfetch/venv/bin/python -m flacfetch.credential_keeper
   Restart=always
   RestartSec=30

   [Install]
   WantedBy=multi-user.target
   ```

4. **New GCP secrets**: Add secret resources for account credentials in Pulumi

5. **IAM**: Ensure service account can write to `spotify-oauth-token` secret (already has `secretVersionAdder` for `youtube-cookies`; add same for `spotify-oauth-token`)

6. **pyproject.toml**: Add `keeper` extras group:
   ```toml
   [project.optional-dependencies]
   keeper = ["patchright"]
   ```

### Phase 4: Monitoring & Reliability

1. **Update credential check**: Modify `credential_check.py` to also check credential-keeper status (is the browser running? when was the last successful refresh?)

2. **Status file**: Keeper writes status to `/mnt/flacfetch-data/keeper-status.json`:
   ```json
   {
     "google_session": {
       "logged_in": true,
       "last_login": "2026-03-06T14:00:00Z"
     },
     "youtube": {
       "last_refresh": "2026-03-06T14:00:00Z",
       "last_refresh_status": "ok",
       "cookies_uploaded": true
     },
     "spotify": {
       "last_refresh": "2026-03-06T14:00:00Z",
       "last_refresh_status": "ok",
       "token_uploaded": true,
       "used_google_login": true
     }
   }
   ```

3. **Logging**: Credential keeper logs to `/var/log/flacfetch-credential-keeper.log`

4. **Failure handling**:
   - Browser crash: systemd auto-restarts the service
   - Google session expired: re-login using stored credentials (no 2FA)
   - Login fails: send Pushbullet notification with details
   - Spotify "Continue with Google" doesn't work: fall back to IMAP 2FA flow, notify

5. **Health endpoint**: Add `GET /credentials/keeper-status` to flacfetch API, reads from status file

## Implementation Order

1. **Phase 1** - Spotify token writeback (1 file change, quick win)
2. **Phase 2a** - Browser infrastructure (Patchright + Xvfb setup)
3. **Phase 2b** - Google login automation
4. **Phase 2c** - YouTube cookie keeper (highest value - no other solution exists)
5. **Phase 2d** - Spotify token keeper (via "Continue with Google")
6. **Phase 3** - Infrastructure/deployment updates
7. **Phase 4** - Monitoring

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Google detects automation, blocks login | Medium | Patchright anti-detection + headed mode + persistent profile (looks like returning user). Only logs in once, then maintains session. |
| Google session expires despite periodic visits | Low | Sessions typically last weeks/months with periodic activity. Keeper visits YouTube every 6-12h which counts as activity. |
| Spotify "Continue with Google" flow changes | Low | Standard Google SSO integration. If it breaks, fall back to direct Spotify login with IMAP 2FA. |
| VM memory pressure with Chromium | Low | Upgrade to e2-medium (4GB). Chromium uses ~300-500MB. Only one browser instance. |
| Browser profile corruption | Low | Keeper detects and recreates profile. Re-login uses stored credentials (no 2FA). |
| Spotify OAuth redirect capture fails | Low | Multiple ways to capture: page URL watch, route interception, navigation events. |
| YouTube cookie format changes | Very Low | Netscape format is standard and widely supported by yt-dlp. |

## Cost Impact

- VM upgrade `e2-small` -> `e2-medium`: ~$12/month additional
- No other cost changes (no proxy, no external services)

## One-Time Setup Steps (manual)

Before deploying, the user needs to:
1. **Gmail**: Enable IMAP in `nomadflacfetch@gmail.com` settings (for fallback)
2. **Gmail**: Generate an app password (Google Account > Security > App passwords) (for fallback)
3. **Spotify**: Create Spotify account using `nomadflacfetch@gmail.com` (done)
4. **Spotify**: Enable "Login with Google" (done)
5. **GCP**: Store credentials in Secret Manager:
   - `flacfetch-account-email` = `nomadflacfetch@gmail.com`
   - `flacfetch-account-password` = Google account password
   - `gmail-app-password` = Gmail app password (fallback for IMAP)
6. **Spotify**: Do initial OAuth authorization (one-time) to grant the flacfetch Spotify app permissions for the new account - this can be done as part of the first credential keeper run
