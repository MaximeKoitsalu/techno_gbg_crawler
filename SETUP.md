# Techno GBG Crawler — Setup Guide

## What you'll end up with
A GitHub Action that runs every morning at 08:00, scrapes RA and progek.se
for Gothenburg events, and appends new ones to a Google Sheet you review.

---

## Step 1 — Create the GitHub repo

1. Go to github.com → New repository
2. Name it `techno-gbg-crawler`, set to Private
3. Upload these files to the repo:
   - `scraper.py`
   - `requirements.txt`
   - `.github/workflows/crawl.yml`

---

## Step 2 — Create a Google Sheet

1. Go to sheets.google.com → create a new sheet
2. Name it exactly: **Techno GBG Events**
3. Inside, rename the first tab to: **Events**
4. Leave it blank — the scraper will add headers automatically

---

## Step 3 — Create a Google Service Account

This is the "robot user" that writes to your sheet. Takes ~5 minutes.

1. Go to console.cloud.google.com
2. Create a new project (call it "techno-gbg" or anything)
3. Enable these two APIs:
   - **Google Sheets API**
   - **Google Drive API**
4. Go to **IAM & Admin → Service Accounts → Create Service Account**
   - Name: `techno-gbg-crawler`
   - Role: Editor
5. Click the service account → **Keys → Add Key → JSON**
6. A `.json` file downloads — keep this safe, you'll need it in step 4

---

## Step 4 — Share the Sheet with the service account

1. Open the downloaded JSON file in a text editor
2. Copy the `client_email` value (looks like `techno-gbg-crawler@...iam.gserviceaccount.com`)
3. Open your Google Sheet → Share → paste that email → Editor → Share

---

## Step 5 — Add the credentials to GitHub

1. Open the downloaded JSON file, select ALL the text, copy it
2. Go to your GitHub repo → Settings → Secrets and variables → Actions
3. Click **New repository secret**
   - Name: `GOOGLE_CREDENTIALS`
   - Value: paste the entire JSON content
4. Click **Add secret**

---

## Step 6 — Test it manually

1. Go to your GitHub repo → Actions tab
2. Click **Techno GBG Event Crawler** → **Run workflow** → **Run workflow**
3. Watch the logs — it should finish in ~30 seconds
4. Check your Google Sheet — new events should appear

---

## Your Sheet columns

| Column | Description |
|--------|-------------|
| Event name | Uppercased, ready to paste into the flyer generator |
| Date | DD-MM-YY display format |
| Date (raw) | YYYY-MM-DD for dedup |
| Source | RA or progek.se |
| Genre hint | Auto-detected: techno, melodic, dnb, psytrance, deep house |
| Suggested song | One suggestion per genre — change it freely |
| Status | Change to `ready` when you want to post, `skip` to ignore |
| Added | Date the row was added |

---

## After setup — your daily workflow

1. Morning: new events appear in the sheet automatically
2. You: review, fix names, pick songs, set status to `ready`
3. You: open the flyer generator, paste the lines, download light + dark
4. You: post to Instagram, pick the song in the app

---

## Troubleshooting

**No events appear:**
- Check the Actions logs for errors
- RA and progek.se occasionally change their HTML — open an issue or re-run with debug

**Authentication error:**
- Make sure the sheet is shared with the service account email
- Make sure the JSON is pasted correctly in GitHub secrets (no extra spaces)

**Duplicate events:**
- The scraper deduplicates by name + date — if the same event appears twice it means
  the name changed slightly between sources. Delete one row manually.
