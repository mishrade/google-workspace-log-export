# Google Workspace Audit Log Exporter

Export all 13 Google Workspace audit log types to AWS S3 for SOC 2 compliance (1-year retention). Runs as a daily AWS Lambda function using Workload Identity Federation — no service account keys.

Also includes a GCP-native alternative (Cloud Logging → GCS) that requires zero code.

## Blog Post

Read the full write-up: https://medium.com/@mishra.a.deepika/ac24d4f8a396

## Architecture

```
                         Workload Identity
                          Federation (WIF)
                               │
┌──────────────┐    AWS IAM    │    Google Cloud     ┌──────────────────┐
│ EventBridge  │───→ creds ────┼──→ federated token  │ Google Workspace │
│ (daily cron) │               │         │           │ Admin SDK        │
└──────┬───────┘               │    impersonate SA   │ Reports API      │
       │                       │         │           └────────┬─────────┘
       ▼                       │    sign JWT with            │
┌──────────────┐               │    domain delegation        │
│ AWS Lambda   │◄──────────────┘         │                   │
│ Python 3.11  │◄────── audit logs (JSON) ◄──────────────────┘
│ 256 MB / 5m  │
└──────┬───────┘
       │ gzipped JSON
       ▼
┌──────────────┐
│ S3 Bucket    │
│ Standard     │──→ Glacier (90d) ──→ Expire (400d)
│ YYYY/MM/DD/  │
└──────────────┘
```

## Log Types

All 13 SOC 2-relevant audit log types:

| Log Type | SOC 2 | What It Captures |
|---|---|---|
| `admin` | CC8 | Admin console changes, settings |
| `login` | CC6 | Sign-ins, failed logins, MFA |
| `drive` | CC6 | File access, sharing, permissions |
| `token` | CC6 | OAuth app authorizations |
| `groups` | CC6 | Group membership changes |
| `groups_enterprise` | CC6 | Enterprise group changes |
| `saml` | CC6 | SSO sign-in events |
| `user_accounts` | CC6 | Password changes, 2SV |
| `mobile` | CC6 | Device management events |
| `rules` | CC7 | DLP policy violations |
| `meet` | CC6 | Meeting audit trail |
| `chat` | CC6 | Messaging audit trail |
| `calendar` | CC6 | Calendar sharing/events |

## Quick Start

### Prerequisites

- Google Workspace Super Admin account
- AWS account (Lambda, S3, EventBridge)
- Google Cloud project (free tier — no billing needed)

### 1. AWS Setup

Create an S3 bucket with lifecycle rules:
- Standard → Glacier Flexible Retrieval at 90 days
- Expire at 400 days

Create a Lambda function:
- Runtime: Python 3.11
- Memory: 256 MB
- Timeout: 5 minutes
- IAM: `s3:PutObject` on your bucket

### 2. Google Cloud Setup

```bash
export AWS_ACCOUNT_ID="123456789012"
export GCP_PROJECT="workspace-audit-logs"
export SERVICE_ACCOUNT="log-exporter@${GCP_PROJECT}.iam.gserviceaccount.com"
export PROJECT_NUMBER=$(gcloud projects describe $GCP_PROJECT \
  --format='value(projectNumber)')

# Create WIF pool + AWS provider
gcloud iam workload-identity-pools create aws-lambda-pool \
  --project=$GCP_PROJECT --location="global" \
  --display-name="AWS Lambda Pool"

gcloud iam workload-identity-pools providers create-aws aws-lambda-provider \
  --project=$GCP_PROJECT --location="global" \
  --workload-identity-pool="aws-lambda-pool" \
  --account-id="$AWS_ACCOUNT_ID"

# IAM bindings
gcloud iam service-accounts add-iam-policy-binding $SERVICE_ACCOUNT \
  --project=$GCP_PROJECT \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/aws-lambda-pool/*"

gcloud iam service-accounts add-iam-policy-binding $SERVICE_ACCOUNT \
  --project=$GCP_PROJECT \
  --role="roles/iam.serviceAccountTokenCreator" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/aws-lambda-pool/*"

# Generate credential config (NO --service-account flag!)
gcloud iam workload-identity-pools create-cred-config \
  projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/aws-lambda-pool/providers/aws-lambda-provider \
  --aws \
  --output-file=credential-config.json
```

### 3. Domain-Wide Delegation

1. Go to **admin.google.com** → Security → API controls → Domain-wide delegation
2. Add new: Client ID = service account's **Unique ID** (numeric)
3. Scope: `https://www.googleapis.com/auth/admin.reports.audit.readonly`

### 4. Deploy Lambda

```bash
# Build the Lambda layer
./scripts/build_layer.sh

# Package the Lambda code
./scripts/package_lambda.sh
```

Upload `lambda_layer.zip` as a Lambda Layer, then upload `lambda_package.zip` as the function code.

Set environment variables:

| Key | Value |
|---|---|
| `DELEGATE_ADMIN_EMAIL` | `admin@yourdomain.com` |
| `S3_BUCKET` | `your-bucket-name` |
| `SERVICE_ACCOUNT_EMAIL` | `log-exporter@project.iam.gserviceaccount.com` |
| `GOOGLE_CREDENTIAL_CONFIG` | Contents of `credential-config.json` |

Handler: `lambda_function.handler`

### 5. Test

Create a test event with `{}` and run it. Expected output:

```json
{
  "status": "success",
  "results": { "admin": 42, "login": 187, "drive": 1203, ... },
  "total_events": 1627
}
```

### 6. Schedule

Create an EventBridge rule: `cron(0 6 * * ? *)` (daily at 6 AM UTC) targeting the Lambda.

### 7. Backfill

Capture all available history (Google retains ~180 days):

```bash
./scripts/backfill.sh 180
```

## S3 Structure

```
s3://your-bucket/
└── workspace-audit-logs/
    └── 2026/
        └── 03/
            └── 06/
                ├── admin.json.gz
                ├── login.json.gz
                ├── drive.json.gz
                └── ... (13 files per day)
```

## GCP-Native Alternative

Zero-code approach using Cloud Logging → GCS.

**Caveat**: GCP project must be in the same org as the Workspace domain.

## Lambda Invocation Options

```json
{}                                    // fetch yesterday's logs (default)
{"override_lookback_days": 7}         // fetch logs from 7 days ago
{"applications": ["login", "admin"]}  // fetch only specific types
```

## Cost

< $1/month for a typical Workspace deployment.


## License

MIT
