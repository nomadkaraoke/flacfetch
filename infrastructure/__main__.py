"""
Pulumi infrastructure for the Flacfetch audio download service.

Flacfetch's compute moved OFF Google Compute Engine to a Netcup VPS
(flacfetch.nomadkaraoke.com, provisioned by deploy/provision.sh) in the July 2026
GCP-exit migration. This stack now manages only the GCP-side resources flacfetch
still depends on from the VPS (reached via a service-account key on the box):
- Service account (flacfetch-service) + IAM: Secret Manager access, GCS read/write on
  the shared karaoke-gen bucket, and secretVersionManager on the two rotating secrets
  (youtube-cookies, spotify-oauth-token) the credential keeper writes back.
- Secret Manager secrets for tracker / Spotify / account credentials.

The former GCE resources (VM, persistent data disk, static IP, firewall rules, startup
script, VM guest-agent logging IAM) were removed when flacfetch moved to the VPS.
"""
import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import secretmanager, serviceaccount

# Get the current GCP project
project = gcp.organizations.get_project()
project_id = project.project_id

# Region/zone retained for export compatibility (no compute resources remain here).
zone = "us-central1-a"
region = "us-central1"

# =============================================================================
# Service Account
# =============================================================================

flacfetch_sa = serviceaccount.Account(
    "flacfetch-sa",
    account_id="flacfetch-service",
    display_name="Flacfetch Service Account",
    description="Service account for flacfetch torrent/audio download VM",
)

# Grant Secret Manager access (to read API keys)
flacfetch_secrets_iam = gcp.projects.IAMMember(
    "flacfetch-secrets-access",
    project=project_id,
    role="roles/secretmanager.secretAccessor",
    member=flacfetch_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant GCS write permissions (to upload downloaded files)
# Uses the shared karaoke-gen bucket
bucket_name = f"karaoke-gen-storage-{project_id}"

flacfetch_storage_writer = gcp.storage.BucketIAMMember(
    "flacfetch-storage-writer",
    bucket=bucket_name,
    role="roles/storage.objectCreator",
    member=flacfetch_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

flacfetch_storage_reader = gcp.storage.BucketIAMMember(
    "flacfetch-storage-reader",
    bucket=bucket_name,
    role="roles/storage.objectViewer",
    member=flacfetch_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)


# =============================================================================
# Secrets
# =============================================================================
# Note: flacfetch-api-key and flacfetch-api-url are managed by karaoke-gen since
# the backend needs them to communicate with flacfetch. All other secrets below
# are flacfetch-specific.

# RED tracker secrets (for private music tracker access)
red_api_key_secret = secretmanager.Secret(
    "red-api-key",
    secret_id="red-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

red_api_url_secret = secretmanager.Secret(
    "red-api-url",
    secret_id="red-api-url",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# OPS tracker secrets (for private music tracker access)
ops_api_key_secret = secretmanager.Secret(
    "ops-api-key",
    secret_id="ops-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

ops_api_url_secret = secretmanager.Secret(
    "ops-api-url",
    secret_id="ops-api-url",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Spotify secrets (for Spotify Premium audio capture)
spotipy_client_id_secret = secretmanager.Secret(
    "spotipy-client-id",
    secret_id="spotipy-client-id",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

spotipy_client_secret_secret = secretmanager.Secret(
    "spotipy-client-secret",
    secret_id="spotipy-client-secret",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# YouTube cookies secret (for authenticated downloads when required)
youtube_cookies_secret = secretmanager.Secret(
    "youtube-cookies",
    secret_id="youtube-cookies",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Spotify OAuth token secret (cached token for headless server auth)
spotify_oauth_token_secret = secretmanager.Secret(
    "spotify-oauth-token",
    secret_id="spotify-oauth-token",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Pushbullet API key (for credential health check notifications)
pushbullet_api_key_secret = secretmanager.Secret(
    "pushbullet-api-key",
    secret_id="pushbullet-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Flacfetch account credentials (for credential keeper browser automation)
flacfetch_account_email_secret = secretmanager.Secret(
    "flacfetch-account-email",
    secret_id="flacfetch-account-email",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

flacfetch_account_password_secret = secretmanager.Secret(
    "flacfetch-account-password",
    secret_id="flacfetch-account-password",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Grant flacfetch service account permission to add AND destroy versions of the
# spotify-oauth-token secret. secretVersionManager is a superset of
# secretVersionAdder that also grants versions.destroy — required for the
# rotation prune (keep newest 5). Without destroy permission the prune silently
# fails and ENABLED versions accumulate (cost regression, fixed Jun 2026).
spotify_oauth_token_iam = secretmanager.SecretIamMember(
    "spotify-oauth-token-writer",
    secret_id=spotify_oauth_token_secret.id,
    role="roles/secretmanager.secretVersionManager",
    member=flacfetch_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant flacfetch service account permission to add AND destroy versions of the
# youtube-cookies secret (cookie upload endpoint + rotation prune). See note
# above re: secretVersionManager vs secretVersionAdder.
youtube_cookies_iam = secretmanager.SecretIamMember(
    "youtube-cookies-writer",
    secret_id=youtube_cookies_secret.id,
    role="roles/secretmanager.secretVersionManager",
    member=flacfetch_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# =============================================================================
# Exports
# =============================================================================

pulumi.export("project_id", project_id)
pulumi.export("zone", zone)
pulumi.export("region", region)

# Storage exports
pulumi.export("gcs_bucket", bucket_name)

# Service account exports
pulumi.export("service_account_email", flacfetch_sa.email)

# Secret exports (just names, not values)
# Note: flacfetch-api-key and flacfetch-api-url are managed by karaoke-gen
pulumi.export("secrets", {
    "red_api_key": red_api_key_secret.name,
    "red_api_url": red_api_url_secret.name,
    "ops_api_key": ops_api_key_secret.name,
    "ops_api_url": ops_api_url_secret.name,
    "spotipy_client_id": spotipy_client_id_secret.name,
    "spotipy_client_secret": spotipy_client_secret_secret.name,
    "spotify_oauth_token": spotify_oauth_token_secret.name,
    "pushbullet_api_key": pushbullet_api_key_secret.name,
    "youtube_cookies": youtube_cookies_secret.name,
    "flacfetch_account_email": flacfetch_account_email_secret.name,
    "flacfetch_account_password": flacfetch_account_password_secret.name,
})

