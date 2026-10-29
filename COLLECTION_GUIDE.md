# How to collect posts by hand

Use this for **Facebook** and **TikTok** (no usable API) and for **Instagram** until the API route is set up. You can type posts into the **Add data** tab of the app (`python chatbot/server.py`) or fill in the spreadsheet template and import it there. Follow the same rules as everyone else, or the numbers cannot be compared.

## The rules

1. **Look at public brand pages only.** Do not use scrapers, bots, browser extensions that export data, or fake accounts. They break the platforms' terms and can get your account banned. Typing numbers you can see into a spreadsheet is fine.
2. **One collector, one brand, one platform.** You are assigned something like *Khaadi on Instagram*.
3. **Take the 30 newest posts that are at least 7 days old.** Brand-new posts have not collected their engagement yet and would look unfairly weak. Skip pinned posts (they are not the newest). Do not choose the best-looking posts.
4. **Read all the numbers within one or two days**, and write the day you read them in `collected_on`. Counts keep changing.
5. **Copy what you see. Never estimate.** If a number is hidden, leave the cell **blank**. Blank means "not shown". Zero means the page shows 0.
6. **No usernames and no comment text.** We count comments, we do not copy them.

## The columns

| Column | What to write |
|--------|---------------|
| `collector` | Your name |
| `platform` | `instagram`, `facebook` or `tiktok` |
| `brand` | Exactly as in `config/social_brands.json` (for example `Gul Ahmed`) |
| `post_url` | The link to that one post (Instagram: `instagram.com/p/...` or `/reel/...`; TikTok: `tiktok.com/@brand/video/...`). Not the profile |
| `post_date` | `YYYY-MM-DD`, for example `2026-10-19`. Instagram shows the date when you open the post |
| `post_time_pkt` | 24-hour `HH:MM` in Pakistan time, or blank. Facebook shows it when you hover over the date. Instagram's website does not show it, so leave it blank |
| `media_type` | `image`, `carousel` (several photos), `reel`, `video`, `text`, `link`, `other`. TikTok posts are `video` |
| `caption` | The full caption, any language (Urdu, Roman Urdu or English) |
| `likes` | Instagram and TikTok: the like count. Facebook: the **total reactions** number (all reaction types together) |
| `comments` | The comment count |
| `shares` | Facebook or TikTok share count. Blank for Instagram |
| `views` | Plays shown on a video or reel. For TikTok, the plays number. Blank for photos |
| `followers_on_page` | The follower count on the brand's profile that day. `1.2M` and `45.3K` are fine |
| `collected_on` | The day you read the numbers, `YYYY-MM-DD` |
| `notes` | Anything odd, for example "looks like a paid ad" |

Numbers may be written as `1,234`, `1.2K` or `3M`.

## Example row

```
Ali,instagram,Khaadi,https://www.instagram.com/p/ABC123/,2026-10-12,,carousel,Winter has arrived #khaadi,2310,41,,,1.2M,2026-10-21,
```

## Saving the file

1. Start from `templates/post_collection_template.csv`.
2. In Excel use **Save As, CSV UTF-8 (Comma delimited)**. The plain "CSV" option damages Urdu text.
3. Type dates as `2026-10-19`. Excel may reformat them, so check after saving.
4. Name the file `yourname_brand_platform.csv` and put it in `data/manual/`.

## Checking your work

```bash
python merge_manual.py
```

The script keeps the good rows and writes every bad row, with the reason and its row number, to `data/raw/rejects_manual_<date>.csv`. Fix those rows in your file and run it again. It never changes your numbers for you.

## How much work is this?

About 1.5 minutes per post, so 30 posts takes roughly 45 minutes. Ten brands on two platforms at 30 posts each is 600 posts, which is 20 sessions. For the lecture's 2,000 records, ask each collector for 100 posts, or add more collectors. A second person should re-read 5 random rows from each file and compare. Large mismatches mean a rule was misunderstood.
