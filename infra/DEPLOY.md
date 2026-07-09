# Deployment Runbook — AI Clinical Scribe

Numbered, do-in-order runbook. AWS console steps are performed by hand; every
command and config file is provided here or in this directory.

**Topology:** one EC2 box (nginx → gunicorn on 127.0.0.1:8001) + one private
RDS Postgres. nginx serves the SPA build and reverse-proxies `/api` and `/ws`.
TLS via Let's Encrypt. Secrets via AWS Secrets Manager through the instance
role — no static AWS keys, no secrets on disk.

**Placeholders used below — substitute your values:**

| Placeholder | Meaning | Example |
|---|---|---|
| `REGION` | AWS region | `us-east-1` |
| `MY_IP` | your laptop's public IP | `203.0.113.7/32` |
| `SCRIBE_HOST` | public hostname | `myscribe.duckdns.org` |
| `RDS_ENDPOINT` | RDS endpoint hostname | `ai-scribe-db.xxxx.us-east-1.rds.amazonaws.com` |

---

## 1. Security groups (console → EC2 → Security Groups)

Create **two** groups in the default VPC. The point of the pair: the database
accepts connections from the app's security group only — not from any IP, not
even yours.

**1a. `app-sg`** (attached to EC2):

| Direction | Type | Port | Source | Why |
|---|---|---|---|---|
| Inbound | HTTP | 80 | 0.0.0.0/0 | certbot challenge + redirect to 443 |
| Inbound | HTTPS | 443 | 0.0.0.0/0 | the app |
| Inbound | SSH | 22 | `MY_IP` | admin access, your IP only |
| Outbound | All | All | 0.0.0.0/0 | default |

**1b. `db-sg`** (attached to RDS):

| Direction | Type | Port | Source | Why |
|---|---|---|---|---|
| Inbound | PostgreSQL | 5432 | **`app-sg` (by security-group ID)** | ONLY the app box can reach the DB |

No other inbound rules on `db-sg`. This is the "psql from laptop times out"
demo in the walkthrough.

## 2. RDS PostgreSQL (console → RDS → Create database)

1. Standard create → PostgreSQL 16 → **Free tier / db.t4g.micro**, 20 GB gp3.
2. DB instance identifier: `ai-scribe-db`.
3. Master username: `scribe_admin`. Generate a strong password — you will
   paste it into Secrets Manager in step 3 and **nowhere else**.
4. Connectivity: default VPC, **Public access: No** ← rubric-critical,
   screenshot this setting for the walkthrough. VPC security group: `db-sg`
   (remove `default`).
5. Additional configuration → Initial database name: `scribe`.
6. Create, wait for *Available*, copy `RDS_ENDPOINT`.

## 3. Secrets Manager (console → Secrets Manager → Store a new secret)

1. Secret type: **Other type of secret**. Key/value (plaintext JSON):

```json
{
  "DATABASE_URL": "postgresql+psycopg://scribe_admin:<MASTER_PASSWORD>@<RDS_ENDPOINT>:5432/scribe",
  "APP_ENV": "production",
  "JWT_SECRET": "<output of: openssl rand -hex 32>"
}
```

The app refuses to start in production if `JWT_SECRET` is missing (guard in
`backend/app/config.py`) — forgetting it fails loudly at boot, not silently.

2. Secret name: **`ai-scribe/production`** (must match `AWS_SECRET_NAME` in
   the systemd unit). No rotation. Store.

> Later phases add keys here (`ANTHROPIC_API_KEY`, `JWT_SECRET`) — same
> secret, restart the service to pick them up.

## 4. IAM role for EC2 (console → IAM → Roles → Create role)

1. Trusted entity: AWS service → **EC2**.
2. Skip the managed-policy screen; create the role as `ai-scribe-ec2-role`,
   then add this **inline policy** (least privilege — read one secret, nothing
   else):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:REGION:*:secret:ai-scribe/production-*"
    }
  ]
}
```

(The trailing `-*` matters: Secrets Manager appends a random suffix to ARNs.)

## 5. EC2 (console → EC2 → Launch instance)

1. Name `ai-scribe-app`; AMI **Ubuntu Server 24.04 LTS**; type `t3.small`
   (t3.micro works but Node builds get slow).
2. Key pair: create/download `ai-scribe.pem` → `chmod 400 ~/ai-scribe.pem`.
   (Never enters the repo — `.gitignore` covers `*.pem`.)
3. Network: default VPC, security group **`app-sg`**.
4. Advanced details → IAM instance profile: **`ai-scribe-ec2-role`**.
5. Launch. Then **Elastic IPs → Allocate → Associate** with the instance
   (survives stop/start; DNS points at it).

## 6. DNS (DuckDNS)

1. https://www.duckdns.org → sign in → add subdomain (e.g. `myscribe`) →
   set its IP to the Elastic IP.
2. Verify from laptop: `dig +short SCRIBE_HOST` returns the Elastic IP.

## 7. Server bootstrap

```bash
ssh -i ~/ai-scribe.pem ubuntu@SCRIBE_HOST

# Ubuntu 24.04 ships Python 3.12 — matches local dev exactly
sudo apt update && sudo apt install -y \
  python3.12-venv nginx certbot python3-certbot-nginx git

# Node 20 LTS (build-only; Node never runs in production)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

## 8. App checkout + build

```bash
sudo mkdir -p /srv/ai-scribe && sudo chown ubuntu:ubuntu /srv/ai-scribe
git clone <REPO_URL> /srv/ai-scribe
cd /srv/ai-scribe

# Backend
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
# NOTE: no .env is created in production — config comes from Secrets Manager.

# Frontend (static build; nginx serves dist/)
cd ../frontend
npm ci
npm run build
```

## 9. Database migration (from the EC2 box — the only place that can reach RDS)

```bash
cd /srv/ai-scribe/backend
AWS_SECRET_NAME=ai-scribe/production AWS_DEFAULT_REGION=REGION \
  .venv/bin/alembic upgrade head
# Expect: "Running upgrade -> <rev>, baseline (empty)..."
# This command doubles as proof that the instance role + secret + RDS
# networking all work before the service ever starts.

# Demo data (idempotent — safe to re-run):
AWS_SECRET_NAME=ai-scribe/production AWS_DEFAULT_REGION=REGION \
  .venv/bin/python -m app.seed
```

## 10. systemd service

```bash
sudo cp /srv/ai-scribe/infra/systemd/ai-scribe.service /etc/systemd/system/
# Edit if your region isn't us-east-1:
sudo sed -i 's/us-east-1/REGION/' /etc/systemd/system/ai-scribe.service

sudo systemctl daemon-reload
sudo systemctl enable --now ai-scribe
systemctl status ai-scribe               # expect: active (running)
curl -s http://127.0.0.1:8001/api/health # expect: {"status":"ok","database":"ok"}
```

`"database":"ok"` here proves the Secrets Manager fetch AND the RDS
connection through the pooled engine.

## 11. nginx + HTTPS

```bash
sudo cp /srv/ai-scribe/infra/nginx/ai-scribe.conf /etc/nginx/sites-available/ai-scribe
sudo sed -i 's/scribe.example.com/SCRIBE_HOST/' /etc/nginx/sites-available/ai-scribe
sudo ln -s /etc/nginx/sites-available/ai-scribe /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# TLS — certbot rewrites the server block and adds the 80→443 redirect
sudo certbot --nginx -d SCRIBE_HOST --redirect -m you@example.com --agree-tos
```

## 12. Verification (Phase 0 definition of done)

From your **laptop browser**:

1. `https://SCRIBE_HOST` shows the app shell with a valid padlock.
2. `https://SCRIBE_HOST/api/health` returns `{"status":"ok","database":"ok"}`.
3. Click **Start stream test** → numbers 1–20 appear **one at a time over
   ~4 seconds**. If they appear all at once, `proxy_buffering off` is not
   taking effect — re-check step 11.
4. Repeat with DevTools → Network → throttling "Fast 3G": still progressive.

RDS privacy proof (walkthrough material):

```bash
# From laptop — must HANG and time out (db-sg has no rule for you):
psql "postgresql://scribe_admin@RDS_ENDPOINT:5432/scribe" -c 'select 1'
# From EC2 — connects (via app-sg membership). Uses the same URL the app got
# from Secrets Manager; nothing typed from memory:
ssh -i ~/ai-scribe.pem ubuntu@SCRIBE_HOST
sudo apt install -y postgresql-client
psql "$(aws secretsmanager get-secret-value --secret-id ai-scribe/production \
  --region REGION --query SecretString --output text \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["DATABASE_URL"].replace("+psycopg",""))')" -c 'select 1'
```

## 13. Redeploying a new version

```bash
ssh -i ~/ai-scribe.pem ubuntu@SCRIBE_HOST
cd /srv/ai-scribe && git pull
cd backend && .venv/bin/pip install -r requirements.txt \
  && AWS_SECRET_NAME=ai-scribe/production AWS_DEFAULT_REGION=REGION .venv/bin/alembic upgrade head
cd ../frontend && npm ci && npm run build
sudo systemctl restart ai-scribe
```

(No nginx restart needed — it serves the new `dist/` immediately.)
