# Social Media Manual Data Collection Guide

A step-by-step guide for manually collecting and entering Pakistani fashion brand social media posts (Instagram, Facebook, TikTok) into the Content Detective analytics pipeline.

---

## 1. Overview & Target Brands

This manual collection pipeline gathers organic and promotional content across major Pakistani fashion apparel brands.

### Supported Brands & Official Accounts

| Brand | Instagram Handle | Facebook Page | TikTok Handle |
|---|---|---|---|
| **Khaadi** | `@khaadi` | `KhaadiOfficial` | `@khaadiofficial` |
| **Sapphire** | `@sapphirepk` | `SapphireOfficial` | `@sapphirepk` |
| **Gul Ahmed** | `@gulahmedofficial` | `GulahmedOfficial` | `@gulahmedofficial` |
| **Maria B** | `@mariabofficial` | `mariabofficial` | `@mariab.official` |
| **Sana Safinaz** | `@sanasafinaz` | `SanaSafinazOfficial` | `@sanasafinazofficial` |
| **Nishat Linen** | `@nishatlinen` | `NishatLinen` | `@nishatlinen` |
| **Junaid Jamshed** | `@j.junaidjamshed` | `j.junaidjamshed` | `@junaidjamshedofficial` |
| **Alkaram Studio** | `@alkaramstudio` | `alkaramstudio` | `@alkaramstudio` |
| **Outfitters** | `@outfitters` | `outfitterspk` | `@outfitters_pk` |
| **Zellbury** | `@zellbury` | `zellbury` | `@zellbury` |

*Brand names in the spreadsheet must match the **Brand** column above exactly.*

---

## 2. Template Structure & Columns

Create or open a CSV or Excel (`.xlsx`) spreadsheet inside `data/manual/`, naming it `<collector_name>_<date>.csv` (e.g. `data/manual/ahmed_2026-11-13.csv`).

The header row must contain these exact 15 columns:

```csv
collector,platform,brand,post_url,post_date,post_time_pkt,media_type,caption,likes,comments,shares,views,followers_on_page,collected_on,notes
```

### Column Specifications

| Column | Required? | Accepted Format / Examples | Notes |
|---|---|---|---|
| `collector` | **Yes** | `Ahmed`, `Fatima_K` | Your name or identifier |
| `platform` | **Yes** | `instagram`, `facebook`, `tiktok` | Case-insensitive |
| `brand` | **Yes** | `Khaadi`, `Sapphire`, `Gul Ahmed`, `Maria B`, `Sana Safinaz` | Exact spelling matching config |
| `post_url` | **Yes** | Direct URL to specific post | See URL format guide below |
| `post_date` | **Yes** | `YYYY-MM-DD` (e.g. `2026-10-15`) | Publication date in Pakistan time |
| `post_time_pkt` | No | `HH:MM` 24-hr format (e.g. `18:30`) | Post time in PKT (leave blank if unknown) |
| `media_type` | **Yes** | `image`, `carousel`, `reel`, `video`, `text` | Synonyms accepted: `photo` → `image`, `album` → `carousel` |
| `caption` | No | Full caption text | Include hashtags; emojis are preserved |
| `likes` | No | `1250` or `1.2K` or `1.5M` | Total likes/reactions (leave blank if hidden) |
| `comments` | No | `85` or `1.1K` | Total comment count |
| `shares` | No | `42` | Native shares (Facebook, TikTok; blank for IG) |
| `views` | Cond. | `45000` or `45K` | Video views (mandatory for TikTok, optional for reels) |
| `followers_on_page` | No | `520000` or `520K` | Follower count of account on collection date |
| `collected_on` | **Yes** | `YYYY-MM-DD` (e.g. `2026-11-13`) | Date you recorded this row |
| `notes` | No | Free text (e.g. `Eid collection launch`, `Influencer collab`) | Any relevant context |

---

## 3. Platform-Specific Collection Instructions

### A. Instagram
1. **Post URL**:
   - Right-click post timestamp or click `...` → **Copy link**.
   - Valid format: `https://www.instagram.com/p/CODE/` or `https://www.instagram.com/reel/CODE/`.
2. **Media Type**:
   - Single photo: `image`
   - Multi-photo swipe / carousel: `carousel`
   - 9:16 vertical short video: `reel`
   - Feed video (if not a reel): `video`
3. **Likes & Views**:
   - For reels, Instagram displays view count under the reel; click the view count to see the like count.
   - If likes are hidden by the user ("Liked by X and others"), leave `likes` blank.

### B. Facebook
1. **Post URL**:
   - Click the post's timestamp (e.g. "3 hrs ago", "October 14 at 4:15 PM") to navigate directly to the permalink.
   - Copy URL: `https://www.facebook.com/Brand/posts/123456789` or `https://www.facebook.com/story.php?story_fbid=...`.
2. **Reactions / Likes**:
   - Hover or click the reaction counter to get the total number of reactions. Enter as `likes`.
3. **Shares & Comments**:
   - Facebook publicly shows share and comment counts below each post. Enter both.

### C. TikTok
1. **Post URL**:
   - Click **Copy Link** on web or mobile app.
   - Valid format: `https://www.tiktok.com/@username/video/7281928371928371928` (short links like `vm.tiktok.com/...` are also accepted).
2. **Media Type**:
   - Video clips: `reel` or `video`
   - Photo slides: `carousel` or `image`
3. **Views & Metrics**:
   - TikTok prominently displays views on the video thumbnail and post page. Record in `views`.
   - Record likes, comments, and shares (favorites/bookmarks can be noted in `notes`).

---

## 4. Validating and Merging Your Data

Once your spreadsheet is completed and saved inside `data/manual/`:

1. Open your terminal in the project root:
   ```bash
   python merge_manual.py
   ```

2. Inspect the output summary:
   ```text
   Scanning data/manual for CSV and XLSX files...
   Processed: data/manual/ahmed_2026-11-13.csv
     -> 50 valid posts accepted
     -> 2 rejected (see data/raw/rejects_manual_2026-11-13.csv)
   ```

3. If rows were rejected:
   - Check `data/raw/rejects_manual_<date>.csv` to see exact error reasons (e.g., misspelled brand, broken URL, invalid date format).
   - Fix those rows in your file in `data/manual/`.
   - Run `python merge_manual.py` again. Merging is idempotent and safe to re-run.

4. Validated posts are merged into `data/raw/posts_manual_<date>.csv` and ready for analytics!
