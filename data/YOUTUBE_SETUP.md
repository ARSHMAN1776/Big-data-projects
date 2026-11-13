# YouTube Data API v3 — Setup Guide

## Overview
YouTube Data API v3 is free up to **10,000 quota units/day**. Each video fetch costs ~1 unit.  
With `--max 50` you use ~50 units per channel run, so you can easily run all 5 brands in one day.

---

## Step 1 — Create a Google Cloud Project

1. Go to [https://console.cloud.google.com/](https://console.cloud.google.com/)
2. Click **Select a project → New Project**
3. Name it `content-detective` (or any name) → **Create**
4. Wait a few seconds for the project to be created

---

## Step 2 — Enable YouTube Data API v3

1. In the left sidebar go to **APIs & Services → Library**
2. Search for `YouTube Data API v3`
3. Click on it → **Enable**

---

## Step 3 — Create an API Key

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → API key**
3. Copy the key shown — save it securely
4. *(Optional)* Click **Restrict Key**:
   - Application restrictions: **IP addresses** (add your server IP)
   - API restrictions: **Restrict key → YouTube Data API v3**
   - Click **Save**

---

## Step 4 — Add the Key to Your Environment

Create a file `.env` in the project root (already in `.gitignore`):

```
YOUTUBE_API_KEY=AIza...your_key_here...
```

The collector loads this automatically via `common.load_env()`.

---

## Step 5 — Run the Collector

```bash
# Collect up to 50 videos per channel for all configured brands
python collect.py --max 50

# Collect for a single brand only
python collect.py --brand "Gul Ahmed" --max 100

# Dry-run (shows what would be collected without saving)
python collect.py --max 50 --dry-run
```

Output is saved to `data/raw/videos_<date>.csv`.

---

## Step 6 — Resolve Sapphire's Channel ID

Sapphire's handle `@SapphirePakistan` is confirmed but their numeric channel ID needs a one-time API lookup:

```python
import os, requests
key = os.environ["YOUTUBE_API_KEY"]
r = requests.get(
    "https://www.googleapis.com/youtube/v3/channels",
    params={"part": "id,snippet", "forHandle": "SapphirePakistan", "key": key}
)
print(r.json()["items"][0]["id"])   # paste result into config/brands.json top5[1].channel_id
```

---

## Quota Reference

| Action | Quota cost |
|---|---|
| `channels.list` (get channel info) | 1 unit |
| `search.list` (find videos) | 100 units |
| `videos.list` (get video stats) | 1 unit per video |
| `playlistItems.list` (channel uploads) | 1 unit |

**Recommended approach** (`collect.py` already does this):
1. Use `playlistItems.list` to get video IDs from the uploads playlist (~1 unit per page of 50)
2. Use `videos.list` to fetch stats in batches of 50 (~1 unit per 50 videos)

So `--max 50` for 5 brands ≈ **15–20 units total** — well within the 10,000/day free tier.

---

## Troubleshooting

| Error | Fix |
|---|---|
| `403 quotaExceeded` | Wait until midnight Pacific Time — quota resets daily |
| `403 forbidden` | Check API key restrictions, make sure YouTube Data API v3 is enabled |
| `channel not found` | Verify handle in `config/brands.json` — handles are case-sensitive |
| `No items in response` | Channel may have no public uploads playlist |

---

## Channel Reference (Top 5 Brands)

| Brand | Handle | Channel ID | Subscribers |
|---|---|---|---|
| Khaadi | @khaadi | UCosHRi7jeBb0JGZfFlOHYUA | ~47K |
| Sapphire | @SapphirePakistan | *(resolve via API — see Step 6)* | ~15K |
| Gul Ahmed | @gulahmedofficial | UCsAIxl3qvpy1DofpwDEx6Ow | ~1.48M |
| Maria B | @mariabofficial | UCJmdNAyevIdbLN2rJPTUEqw | ~71K |
| Sana Safinaz | @SanaSafinazOfficial | UC7gd_nNZvz4DK-ZrZ4FUoAw | ~54K |
