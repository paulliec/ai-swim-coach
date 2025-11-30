# Deployment Guide - Fly.io

This guide covers deploying the SwimCoach AI FastAPI backend to Fly.io.

## Prerequisites

1. **Fly.io Account**
   - Sign up at https://fly.io
   - Install flyctl: `curl -L https://fly.io/install.sh | sh` (Linux/Mac) or `pwsh -Command "iwr https://fly.io/install.ps1 -useb | iex"` (Windows)
   - Login: `fly auth login`

2. **Required Services**
   - Snowflake account (or use mock mode)
   - Cloudflare R2 bucket (or use mock mode)
   - Anthropic API key

## Quick Start

### 1. Initialize Fly.io App

```bash
# From project root
fly launch --no-deploy
```

This reads `fly.toml` and creates the app. Choose:
- **App name**: `swimcoach-api` (or your preference)
- **Region**: `ord` (Chicago)
- **Don't deploy yet**: We need to set secrets first

### 2. Set Secrets

**Required secrets:**

```bash
# Anthropic API
fly secrets set ANTHROPIC_API_KEY="sk-ant-..."

# API Authentication
fly secrets set API_KEYS="prod-key-1,prod-key-2,prod-key-3"

# Snowflake (if using real Snowflake)
fly secrets set SNOWFLAKE_ACCOUNT="your-account.snowflakecomputing.com"
fly secrets set SNOWFLAKE_USER="your-username"

# Option 1: Password authentication
fly secrets set SNOWFLAKE_PASSWORD="your-password"

# Option 2: Key-pair authentication (preferred for production)
# First, base64-encode your private key file:
#   Linux/Mac: cat ~/rsa_key.p8 | base64 -w 0
#   Windows PowerShell: [Convert]::ToBase64String([IO.File]::ReadAllBytes("rsa_key.p8"))
fly secrets set SNOWFLAKE_PRIVATE_KEY_BASE64="MIIEvgIBADANBgk..."

fly secrets set SNOWFLAKE_DATABASE="SWIMCOACH"
fly secrets set SNOWFLAKE_SCHEMA="COACHING"
fly secrets set SNOWFLAKE_WAREHOUSE="COMPUTE_WH"

# Cloudflare R2 (if using real R2)
fly secrets set R2_ACCOUNT_ID="your-account-id"
fly secrets set R2_ACCESS_KEY_ID="your-access-key"
fly secrets set R2_SECRET_ACCESS_KEY="your-secret-key"
fly secrets set R2_BUCKET_NAME="swimcoach-videos"
```

**Optional secrets:**

```bash
# Rate limiting bypass
fly secrets set RATE_LIMIT_BYPASS_KEYS="admin-key-1,trusted-partner-key"

# Mock mode (for testing without external services)
fly secrets set SNOWFLAKE_MOCK_MODE="true"
fly secrets set R2_MOCK_MODE="true"
```

### CORS Configuration

The backend needs to know which frontend domains to allow. Update `fly.toml` with your frontend URL:

```toml
[env]
  # Update with your actual frontend domain(s)
  CORS_ORIGINS = 'https://ai-swim-coach.pages.dev'
  
  # For multiple domains (e.g., custom domain + Cloudflare Pages):
  # CORS_ORIGINS = 'https://swimcoach.app,https://ai-swim-coach.pages.dev'
```

**Common frontend platforms:**
- **Cloudflare Pages**: `https://ai-swim-coach.pages.dev`
- **Vercel**: `https://your-app.vercel.app`
- **Netlify**: `https://your-app.netlify.app`
- **Custom domain**: `https://your-domain.com`

**Important:** Don't use `*` (wildcard) in production - it's insecure and disables credentials.

### Snowflake Key-Pair Authentication (Recommended)

For production, use key-pair authentication instead of passwords for better security.

**Step 1: Generate RSA key pair** (if you don't have one)

```bash
# Generate private key
openssl genrsa -out rsa_key.p8 2048

# Generate public key
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```

**Step 2: Add public key to Snowflake**

```sql
-- In Snowflake, run:
ALTER USER swimcoach_api SET RSA_PUBLIC_KEY='MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...';

-- Remove the BEGIN/END lines and newlines from rsa_key.pub first
```

**Step 3: Base64-encode the private key**

The private key needs to be base64-encoded for secure storage in environment variables.

**Linux/Mac:**
```bash
cat rsa_key.p8 | base64 -w 0 > rsa_key_base64.txt
```

**Windows PowerShell:**
```powershell
$bytes = [IO.File]::ReadAllBytes("rsa_key.p8")
[Convert]::ToBase64String($bytes) | Out-File -NoNewline rsa_key_base64.txt
```

**Step 4: Set the secret in Fly.io**

```bash
# Copy the base64 string from rsa_key_base64.txt
fly secrets set SNOWFLAKE_PRIVATE_KEY_BASE64="$(cat rsa_key_base64.txt)"
```

**Why base64 encoding?**
- File uploads to cloud platforms are complex (permissions, paths, persistence)
- Environment variables are simple and secure
- Base64 preserves the exact key format (newlines, headers, etc.)
- Works with any key format (PEM, DER, PKCS8)

### 3. Deploy

```bash
fly deploy
```

This will:
1. Build the Docker image
2. Push to Fly.io registry
3. Deploy to your region
4. Run health checks
5. Start serving traffic

### 4. Verify Deployment

```bash
# Check status
fly status

# View logs
fly logs

# Open in browser
fly open

# Check health endpoint
curl https://swimcoach-api.fly.dev/health
```

## Configuration

### Scaling

```bash
# Scale to 2 instances
fly scale count 2

# Scale to specific regions
fly scale count 2 --region ord,iad

# Scale vertically (more RAM/CPU)
fly scale vm shared-cpu-2x --memory 1024
```

### Auto-scaling

The app is configured to:
- **Auto-suspend** when idle (saves money)
- **Auto-start** on incoming requests
- **Min instances**: 0 (can scale to zero)
- **Max instances**: 3

Edit `fly.toml` to adjust:

```toml
[scaling]
  min_count = 1  # Always keep 1 running
  max_count = 5  # Scale up to 5 under load
```

### Regions

Add more regions for lower latency:

```bash
# Add east coast region
fly regions add iad  # Virginia

# Add west coast region  
fly regions add sjc  # San Jose

# List current regions
fly regions list
```

## Monitoring

### Logs

```bash
# Real-time logs
fly logs

# Filter by instance
fly logs --instance <instance-id>

# Save to file
fly logs > logs.txt
```

### Metrics

```bash
# Show metrics
fly status

# Detailed dashboard
fly dashboard
```

### Health Checks

Fly.io automatically monitors `/health` endpoint:
- **Interval**: 30 seconds
- **Timeout**: 5 seconds
- **Unhealthy**: 3 failed checks → restart

## Database Migrations

When updating Snowflake schema:

```bash
# SSH into running instance
fly ssh console

# Run migration script (if you add one)
python scripts/migrate.py

# Exit
exit
```

Or run migrations locally against production Snowflake.

## Rollback

If a deployment fails:

```bash
# List releases
fly releases

# Rollback to previous version
fly releases rollback
```

## Cost Optimization

### Free Tier
- **Included**: 3 shared-cpu-1x VMs with 256MB RAM
- **Cost**: First 160GB outbound transfer free
- **Auto-suspend**: Saves compute hours

### Tips
1. Use `auto_stop_machines = "suspend"` (already configured)
2. Set `min_count = 0` to scale to zero when idle
3. Monitor usage: `fly dashboard metrics`
4. Use mock mode for development/testing

### Estimated Costs
- **Light usage** (<10 analyses/day): Free tier
- **Medium usage** (100 analyses/day): ~$5-10/month
- **Heavy usage** (1000 analyses/day): ~$20-30/month

## Troubleshooting

### Deployment Fails

```bash
# Check build logs
fly logs --verbose

# SSH into instance
fly ssh console

# Run health check manually
curl localhost:8080/health
```

### Connection Issues

```bash
# Check if app is running
fly status

# Restart all instances
fly apps restart

# Check DNS
nslookup swimcoach-api.fly.dev
```

### Secret Issues

```bash
# List secrets (values hidden)
fly secrets list

# Unset a secret
fly secrets unset SECRET_NAME

# Import from .env file
fly secrets import < .env.production
```

## Production Checklist

- [ ] Set all required secrets
- [ ] Test health endpoint
- [ ] Verify Snowflake connection
- [ ] Verify R2 storage
- [ ] Test rate limiting
- [ ] Configure custom domain (optional)
- [ ] Set up monitoring alerts
- [ ] Document API keys for frontend
- [ ] Test end-to-end flow
- [ ] Set up backup strategy

## Custom Domain

```bash
# Add custom domain
fly certs add api.swimcoach.ai

# Get DNS records to configure
fly certs show api.swimcoach.ai

# Add CNAME record at your DNS provider:
# api.swimcoach.ai -> swimcoach-api.fly.dev
```

## CI/CD Integration

Add to `.github/workflows/deploy.yml`:

```yaml
name: Deploy to Fly.io

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - run: flyctl deploy --remote-only
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

## Support

- **Fly.io Docs**: https://fly.io/docs
- **Community**: https://community.fly.io
- **Status**: https://status.fly.io

## Next Steps

1. Deploy frontend to Vercel/Netlify
2. Update frontend `VITE_API_BASE_URL` to point to Fly.io backend
3. Set up monitoring (e.g., Sentry, LogRocket)
4. Configure rate limiting for production
5. Set up automated backups

