# Google Sheets Setup Guide

## Step 1: Create Google Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use existing)
3. Enable the Google Sheets API:
   - Search for "Sheets API"
   - Click "Enable"
4. Create Service Account:
   - Go to "Service Accounts"
   - Click "Create Service Account"
   - Name: "crypto-scanner-bot"
   - Skip optional steps
5. Generate JSON Key:
   - Click on created service account
   - Go to "Keys" tab
   - Click "Add Key" → "Create new key"
   - Choose JSON
   - Download the JSON file

## Step 2: Create Google Sheet

1. Go to [Google Sheets](https://sheets.google.com)
2. Create new spreadsheet
3. Rename to: **"Crypto Scanner Dashboard"** (exactly this name)
4. The sheets will be created automatically by the bot

## Step 3: Share Sheet with Service Account

1. Open the JSON file you downloaded
2. Find the "client_email" field (looks like: xxx@xxx.iam.gserviceaccount.com)
3. Go to Google Sheet
4. Click "Share" button (top right)
5. Paste the email address
6. Give "Editor" access
7. Click Share

## Step 4: Configure Railway

1. Go to your Railway project
2. Click on your service/environment
3. Go to Variables section
4. Create new variable:
   - **Name**: `GOOGLE_CREDENTIALS`
   - **Value**: (Paste the entire JSON file content)
   
   The value should look like:
   ```json
   {"type":"service_account","project_id":"xxx","private_key_id":"xxx",...}
   ```

## Step 5: Deploy Bot

1. Push changes to GitHub (or your deployment method)
2. Railway will automatically deploy
3. Check logs for:
   - `[GOOGLE_SHEETS] Connected successfully` - connection worked
   - `[GOOGLE_SHEETS] Created sheet: Signals` - sheets created
   - `[GOOGLE_SHEETS] Buffer flush thread started` - ready to log

## Step 6: Verify Working

1. Let bot run for a few minutes
2. Go to Google Sheet
3. Check if:
   - Signals appear in "Signals" sheet
   - Debug entries in "Debug" sheet
   - Stats updated in "Stats" sheet (after 60 minutes)

## Troubleshooting

### No data appearing in sheets?

**Check:**
1. Google Sheets API enabled in Cloud Console
2. Service account email has editor access to sheet
3. Sheet is named exactly "Crypto Scanner Dashboard"
4. GOOGLE_CREDENTIALS env var set correctly
5. Check Railway logs for errors starting with "[GOOGLE_SHEETS]"

### "GOOGLE_CREDENTIALS not found" error?

- Verify environment variable is set in Railway
- Restart the service/environment
- Check variable name is exactly "GOOGLE_CREDENTIALS"

### Connection timeout?

- Check internet connection
- Verify Google Sheets API is enabled
- Try regenerating service account key

### Data appears but stops after a while?

- Check Railway logs for quota errors
- Verify service account still has sheet access
- Restart the bot

## Sheet Access After Setup

Once configured, you can:
1. View live signal data
2. Analyze trade results
3. Check rejection reasons
4. Monitor performance metrics
5. Create dashboards in Google Data Studio (optional)

## Security Notes

- GOOGLE_CREDENTIALS contains sensitive info - keep Railway env var private
- Service account only has access to shared sheets
- No personal Google account permissions needed
- Can revoke access anytime by removing service account from sheet

## JSON Key Format Reference

Your GOOGLE_CREDENTIALS should contain something like:

```json
{
  "type": "service_account",
  "project_id": "your-project",
  "private_key_id": "xxx",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
  "client_email": "crypto-scanner-bot@xxx.iam.gserviceaccount.com",
  "client_id": "xxx",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/..."
}
```

Paste the **entire JSON** (not individual fields) into the GOOGLE_CREDENTIALS variable.