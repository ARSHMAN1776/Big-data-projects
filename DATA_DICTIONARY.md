# Data dictionary

One row = **one published post** (the observational unit). Times are in UTC and in Pakistan time (PKT, UTC+5).

**Blank means not collected, hidden or not applicable. It never means zero.**

## Instagram, Facebook, TikTok and YouTube posts

Files: `posts_instagram_api_<date>.csv` (API) and `posts_manual_<date>.csv` (hand entry). Same columns in both, so they can be stacked with one `pd.concat`.

| Column | Type | Meaning | Blank when |
|--------|------|---------|------------|
| `platform` | category | `instagram`, `facebook`, `tiktok` or `youtube` | never |
| `brand` | text | Brand name from `config/social_brands.json` | never |
| `post_id` | text | Instagram: the post code or API id. TikTok: the video number. Facebook: the cleaned post link | never |
| `url` | text | Link to the post | API field missing |
| `caption` | text | The brand's own caption, any language | the post has none |
| `hashtags` | text | `#tags` found in the caption, lower case, joined by `|` | none found |
| `published_at_utc` | datetime | Exact publish time in UTC | hand entry without a time |
| `date_pkt` | date | Publish date in Pakistan time | API field missing |
| `weekday_pkt` | category | Day name in Pakistan time | API field missing |
| `hour_pkt` | int 0-23 | Hour in Pakistan time | hand entry without a time (always so for Instagram by hand) |
| `media_type` | category | `image`, `carousel`, `reel`, `video`, `text`, `link`, `other`. In API mode, reels show as `video` if Meta does not return the product type | never |
| `likes` | int | Instagram and TikTok: likes. Facebook: **total reactions of all types** | the owner hides likes, or the page does not show them |
| `comments` | int | Comment count | not shown |
| `shares` | int | Facebook or TikTok shares | always blank on Instagram |
| `views` | int | Plays on a video or reel | photos, or not shown |
| `followers` | int | Followers of the account on the collection day | not shown |
| `source` | category | `api`, `manual`, or `demo` (made-up data from the app's Demo mode, which is never saved in `data/raw`) | never |
| `collector` | text | Who entered it by hand | API rows |
| `collected_at_utc` | datetime/date | When the numbers were read. API rows hold a timestamp, hand-entered rows hold the day | never |

Notes for analysis:
- Likes on Facebook (all reactions) and Instagram (hearts) are not the same thing. Compare platforms by **rates within each platform** and be careful about the wording of the conclusion.
- Engagement per follower is `(likes + comments) / followers`. Rows without `followers` or `likes` cannot be used for it.
- Counts change over time. Always compare posts collected on similar days and of similar age.

## `accounts_instagram_api_<date>.csv`

| Column | Meaning |
|--------|---------|
| `brand`, `instagram` | Brand and Instagram username |
| `followers` | Follower count |
| `media_count` | Total posts the account holds |
| `posts_collected` | How many this run fetched |
| `collected_at_utc` | When collected |

## `rejects_manual_<date>.csv`

Rows from hand-entry files that failed validation: `file`, `row` (the row number in your spreadsheet), `collector`, `brand`, `post_url`, and `problems` (every reason, in plain words).

## `manifest_*.json`

Provenance for each run: source, timestamp, settings, row counts, and whether the run finished or stopped early (expired token, rate limit).

## Parked: YouTube files

`videos_<mode>_<date>.csv`, `channels_api_<date>.csv` and `comments_api_<date>.csv` come from `collect.py`. The 120-video file from 21 Oct 2026 holds each brand's newest 15 videos with views only. Columns are `brand`, `channel_id`, `video_id`, `url`, `title`, `description`, `published_at_utc`, `date_pkt`, `weekday_pkt`, `hour_pkt`, `format` (`short` or `video`), `duration_sec`, `views`, `likes`, `comment_count`, `rss_rating_count`, `hashtags`, `tags`, `default_language`, `source`, `collected_at_utc`. Likes, comment counts, duration and tags are blank in the feed-only file.
