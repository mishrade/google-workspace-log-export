"""
Google Workspace Audit Log Export to S3

Production Lambda function. Exports all SOC 2 relevant audit log types
from Google Workspace via the Admin SDK Reports API to S3 as gzipped JSON.

Auth: Workload Identity Federation (no service account key required).

Environment Variables:
  DELEGATE_ADMIN_EMAIL      - Google Workspace super admin email
  S3_BUCKET                 - S3 bucket name
  SERVICE_ACCOUNT_EMAIL     - Google service account email
  GOOGLE_CREDENTIAL_CONFIG  - Contents of credential-config.json (WIF config)
  AWS_REGION                - AWS region (default: us-east-1)
  S3_PREFIX                 - S3 key prefix (default: workspace-audit-logs)
  LOOKBACK_DAYS             - Days back to fetch (default: 1)

Lambda Layer: google-auth, google-api-python-client
Handler:      lambda_function.handler
"""

import base64
import gzip
import json
import logging
import os
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3
import google.auth
from google.auth import impersonated_credentials
from google.oauth2.credentials import Credentials as OAuth2Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
DELEGATE_ADMIN_EMAIL = os.environ["DELEGATE_ADMIN_EMAIL"]
S3_BUCKET = os.environ["S3_BUCKET"]
SERVICE_ACCOUNT_EMAIL = os.environ["SERVICE_ACCOUNT_EMAIL"]
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
S3_PREFIX = os.environ.get("S3_PREFIX", "workspace-audit-logs")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "1"))

SCOPES = ["https://www.googleapis.com/auth/admin.reports.audit.readonly"]

# All SOC 2 relevant Google Workspace audit log types
APPLICATIONS = [
    "admin",              # CC8  — Admin console changes, settings modifications
    "login",              # CC6  — User sign-ins, failed logins, MFA events
    "drive",              # CC6  — File access, sharing, downloads, permission changes
    "token",              # CC6  — OAuth app authorizations and revocations
    "groups",             # CC6  — Group membership changes
    "groups_enterprise",  # CC6  — Enterprise group changes
    "saml",               # CC6  — SSO sign-in events
    "user_accounts",      # CC6  — Password changes, 2SV enrollment, account actions
    "mobile",             # CC6  — Device audit events
    "rules",              # CC7  — DLP and alert rule triggers
    "meet",               # CC6  — Meeting audit events
    "chat",               # CC6  — Chat messaging audit trail
    "calendar",           # CC6  — Calendar sharing and event changes
]

s3_client = boto3.client("s3", region_name=AWS_REGION)


# ──────────────────────────────────────────────
# Google Auth (WIF + domain-wide delegation)
# ──────────────────────────────────────────────
def get_reports_service():
    """
    Build Admin SDK Reports API client using Workload Identity Federation.

    Flow:
    1. Lambda's AWS IAM role creds are exchanged for Google federated creds
       via Workload Identity Pool
    2. Federated creds impersonate the Google service account (signBlob)
    3. A JWT with 'sub' claim is signed for domain-wide delegation
    4. JWT is exchanged for a domain-delegated access token
    5. Access token is used to call the Admin SDK Reports API
    """
    # Write WIF credential config to temp file for google-auth
    cred_config = os.environ.get("GOOGLE_CREDENTIAL_CONFIG", "")
    if cred_config:
        cred_file = os.path.join(tempfile.gettempdir(), "credential-config.json")
        with open(cred_file, "w") as f:
            f.write(cred_config)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_file

    # Step 1: Get raw WIF federated credentials (no SA impersonation in config)
    source_credentials, _ = google.auth.default()

    # Step 2: Impersonate the service account (for signBlob capability)
    impersonated_creds = impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=SERVICE_ACCOUNT_EMAIL,
        target_scopes=["https://www.googleapis.com/auth/iam"],
    )

    # Step 3: Build and sign JWT with 'sub' claim for domain-wide delegation
    now = int(time.time())
    jwt_claims = {
        "iss": SERVICE_ACCOUNT_EMAIL,
        "sub": DELEGATE_ADMIN_EMAIL,
        "scope": " ".join(SCOPES),
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }

    header_b64 = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=")
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(jwt_claims).encode()
    ).rstrip(b"=")
    unsigned_jwt = header_b64 + b"." + payload_b64

    # Sign via IAM signBlob API (uses federated creds → serviceAccountTokenCreator)
    signature = impersonated_creds.sign_bytes(unsigned_jwt)
    signature_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=")
    signed_jwt = unsigned_jwt + b"." + signature_b64

    # Step 4: Exchange signed JWT for domain-delegated access token
    token_body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": signed_jwt.decode("utf-8"),
    }).encode("utf-8")

    token_req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=token_body,
        method="POST",
    )
    with urllib.request.urlopen(token_req) as resp:
        token_response = json.loads(resp.read())

    access_token = token_response["access_token"]
    logger.info("Obtained domain-delegated access token via WIF")

    # Step 5: Build the API service
    delegated_creds = OAuth2Credentials(token=access_token)
    return build("admin", "reports_v1", credentials=delegated_creds, cache_discovery=False)


# ──────────────────────────────────────────────
# Fetch logs (with pagination)
# ──────────────────────────────────────────────
def fetch_logs(service, app_name, start_time, end_time):
    """Fetch all audit events for an application, handling pagination."""
    all_items = []
    page_token = None

    while True:
        try:
            kwargs = {
                "userKey": "all",
                "applicationName": app_name,
                "startTime": start_time,
                "endTime": end_time,
                "maxResults": 1000,
            }
            if page_token:
                kwargs["pageToken"] = page_token

            resp = service.activities().list(**kwargs).execute()
            items = resp.get("items", [])
            all_items.extend(items)

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        except HttpError as e:
            if e.resp.status == 400:
                logger.warning(
                    "App '%s' not available for this Workspace edition — skipping",
                    app_name,
                )
                break
            raise

    logger.info("Fetched %d events for '%s'", len(all_items), app_name)
    return all_items


# ──────────────────────────────────────────────
# Upload to S3 (gzipped JSON)
# ──────────────────────────────────────────────
def upload_to_s3(logs, app_name, date_str):
    """Compress and upload logs to S3 as YYYY/MM/DD partitioned gzipped JSON."""
    if not logs:
        logger.info("No logs for '%s' on %s — skipping upload", app_name, date_str)
        return 0

    year, month, day = date_str.split("-")
    key = f"{S3_PREFIX}/{year}/{month}/{day}/{app_name}.json.gz"

    body = gzip.compress(json.dumps(logs, default=str, ensure_ascii=False).encode("utf-8"))

    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=body,
        ContentType="application/gzip",
        Metadata={
            "source": "google-workspace-reports-api",
            "application": app_name,
            "log-date": date_str,
            "event-count": str(len(logs)),
        },
    )
    size_kb = round(len(body) / 1024, 1)
    logger.info("Uploaded %d events → s3://%s/%s (%s KB)", len(logs), S3_BUCKET, key, size_kb)
    return len(logs)


# ──────────────────────────────────────────────
# Lambda Handler
# ──────────────────────────────────────────────
def handler(event, context):
    """
    Lambda entry point. Triggered daily by EventBridge.

    Manual invocation options:
      {}                                — fetch yesterday's logs (default)
      {"override_lookback_days": 7}     — fetch logs from 7 days ago
      {"applications": ["login"]}       — fetch only specific log types
    """
    lookback = event.get("override_lookback_days", LOOKBACK_DAYS) if event else LOOKBACK_DAYS
    apps = event.get("applications", APPLICATIONS) if event else APPLICATIONS

    now = datetime.now(timezone.utc)
    start_time = now - timedelta(days=lookback)
    end_time = now - timedelta(days=max(0, lookback - 1))

    start_iso = start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_iso = end_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    date_str = start_time.strftime("%Y-%m-%d")

    logger.info("Collecting logs from %s to %s for %d app(s)", start_iso, end_iso, len(apps))

    service = get_reports_service()

    results = {}
    errors = []

    for app in apps:
        try:
            logs = fetch_logs(service, app, start_iso, end_iso)
            count = upload_to_s3(logs, app, date_str)
            results[app] = count
        except Exception as e:
            logger.error("FAILED '%s': %s", app, e, exc_info=True)
            errors.append({"application": app, "error": str(e)})

    summary = {
        "status": "completed_with_errors" if errors else "success",
        "date": date_str,
        "window": {"start": start_iso, "end": end_iso},
        "results": results,
        "total_events": sum(results.values()),
        "applications_processed": len(results),
        "errors": errors,
    }

    logger.info("Export summary: %s", json.dumps(summary, indent=2))
    return summary
