# Pakistani Social Media Content Detective

**Business question:** *What type of social-media content gets Pakistani audiences to engage?*

You are a junior digital-marketing data scientist. The goal is not sentiment analysis for its own sake. It is a defensible recommendation for a Pakistani brand: **what should it post next, and how do we know?**

**Industry:** Pakistani fashion and lawn clothing. **Platforms:** Instagram, Facebook and TikTok (YouTube was tried first and is parked). **Brands:** see `config/social_brands.json`.

## How we get real data (and why not just scrape)

Instagram, Facebook and TikTok give no public data feed for other brands' posts, and scraping breaks their terms and gets blocked. Searching for a ready-made dataset of Pakistani brand posts found none. So there are two legitimate routes, and they feed the same dataset:

| Platform | Route | Automatic? | Setup |
|----------|-------|------------|-------|
| Instagram | Official API, "Business Discovery" (`collect_instagram.py`) | Yes | About 30 minutes, once. You need an Instagram Business/Creator account and a free Meta developer app |
| Instagram | By hand (`COLLECTION_GUIDE.md`) | No | None |
| Facebook | By hand (`COLLECTION_GUIDE.md`) | No | None. Facebook offers no equivalent API for other brands' pages without Meta's app review |
| TikTok | By hand (`COLLECTION_GUIDE.md`) | No | None. TikTok's research API is only for approved academic researchers in the US and Europe |

Both routes produce the **same columns** (`DATA_DICTIONARY.md`), so the analysis code does not care where a row came from. Having both platforms for the same brands is what lets us answer "which platform performs better?".

YouTube was tried first and then dropped. Its collector (`collect.py`) and the 120 videos in `data/raw/videos_rss_*.csv` are kept but not part of Project 1.

## The seven questions and what the data can honestly answer

| # | Question | How it is answered | Honest status |
|---|----------|--------------------|---------------|
| 1 | Which platform performs better? | `platform`, comparing engagement per follower for the same brands | Needs both platforms collected for the same brands |
| 2 | Which content type gets more engagement? | `media_type`: image, carousel, reel, video, text | Yes |
| 3 | Which topics and hashtags are popular? | `hashtags`, words in `caption` | Yes |
| 4 | What sentiment generates engagement? | Tone of the brand's **caption** (promotional, emotional, festive, discount) against engagement | We collect counts, not comment text, so this is caption tone, not audience mood. Roman Urdu is hard for standard sentiment tools |
| 5 | What time and day appears better? | `weekday_pkt` (always), `hour_pkt` | Hour needs the API, or Facebook where the time is visible. Instagram's website shows only the date |
| 6 | Which Pakistani audience segment responds? | **Cannot be measured** from public data: no age, city or gender. The nearest honest proxy is caption language (Urdu, Roman Urdu, English), which shows what the brand targets, not who responds | Say so in the report |
| 7 | What should a brand post next? | Synthesis of 1 to 5, then an AI-assisted recommendation | Final step |

Compare brands by **engagement per follower** `(likes + comments) / followers`, never by raw likes: Gul Ahmed and a small label are not comparable otherwise. Compare medians, not averages, because a few viral posts dominate.

## Route A: collect by hand

Read `COLLECTION_GUIDE.md`. In short: copy `templates/post_collection_template.csv`, fill one row per post, save as CSV UTF-8 into `data/manual/`, then:

```bash
pip install -r requirements.txt
python merge_manual.py
```

Clean rows go to `data/raw/posts_manual_<date>.csv`. Rejected rows go to `rejects_manual_<date>.csv` with the exact reason.

## Route B: Instagram API setup

You need a free Instagram Business or Creator account, a Facebook Page connected to it, and a Meta developer app. These steps come from my knowledge of Meta's flow. Meta renames screens often, so if one does not match, follow Meta's current [Business Discovery guide](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/business-discovery).

1. In the Instagram app, switch your account to **Professional** (Business or Creator).
2. Create a Facebook Page and connect it to that Instagram account.
3. At developers.facebook.com create an app (type Business) and add the Instagram product (Instagram API with Facebook Login).
4. In the Graph API Explorer, select your app, generate a user token with `instagram_basic`, `pages_show_list` and `pages_read_engagement`, and exchange it for a **long-lived token** (it lasts about 60 days).
5. Find your Instagram user ID: `GET /me/accounts` gives your Page ID, then `GET /<page-id>?fields=instagram_business_account` gives the ID.
6. Copy `.env.example` to `.env` and fill in `IG_USER_ID` and `IG_ACCESS_TOKEN`. Never share these.
7. Check it works:

```bash
python collect_instagram.py --check
```

That tests every brand's username, shows follower counts, and shows **how many posts each account holds**, so you will know whether 2,000 records is realistic. Fix any username it reports as not found in `config/social_brands.json` (my guesses are unverified). Then collect:

```bash
python collect_instagram.py               # 250 newest posts per brand
python collect_instagram.py --max-posts 0 # every post
```

Output: `data/raw/posts_instagram_api_<date>.csv`, `accounts_instagram_api_<date>.csv`, and a `manifest_*.json` recording source, time and settings (your provenance note).

Not yet confirmed: Meta's page does not list which fields it returns for other accounts' posts, and I do not know if your app needs Meta's app review. The collector tries the full field list first and falls back to a shorter one. The API code is tested against stubbed replies, never live, so watch the first real run.

## The app: overview, questions, and adding data

```bash
python chatbot/server.py
```

Then open http://127.0.0.1:8765. It has three tabs:

- **Overview** shows everything at once: headline numbers, what is loaded for each platform (YouTube, Instagram, Facebook, TikTok), and a card for each question (best brands, best platform, best format, best day, best time, hashtags, topics, top posts, what to test next). Each card explains itself in plain words, shows the evidence, and keeps cautions one click away.
- **Ask a question** is a chat. Type, for example, *Which brand performs best?*, *Shorts vs long videos*, *Compare Khaadi and Gul Ahmed*, or click a starter.
- **Add data** lets you type in one post at a time from an Instagram, Facebook or TikTok page, or import a filled-in spreadsheet. Bad rows are refused with the reason, duplicates are caught, and the overview updates at once.

The filters at the top (platform, brand) apply to the overview and the chat. **My data / Demo data** switches between your files and a made-up demo set (fictional "Demo ..." brands on all four platforms, every caption marked [DEMO]) that exists only so you can see how the screens look. A red banner shows whenever demo data is on, and demo data is read-only and never mixed with yours.

How it works, so you can explain it in class:

- **It is not an AI model.** A question is matched to one of a fixed set of analyses (brand ranking, formats, days and times, hashtags, themes, top posts, brand profile, brand comparison, platform comparison, recommendation) and the analysis is run on your files. Every number is calculated, none is invented. Questions outside that list get a "try one of these" answer.
- **Every answer shows its evidence:** the number of posts, the metric, and the date range, in the tag at the bottom. Small groups are marked in red.
- **It tests whether a gap could be chance.** Values are shuffled only among posts of the same brand, so a big brand cannot create a pattern on its own. Groups under 8 posts get no verdict, and the recommendation needs a strict p < 0.01.
- **It picks the best available metric and says which.** Engagement per 100 followers if followers and likes exist, then raw likes + comments, then views. With views only it warns that views are not engagement.
- **It reads the newest file of each kind in `data/raw`** and reloads by itself when a collector writes a new one. If the same post is in several files, the API file wins over the hand-entered file, which wins over the feed file.
- **It listens on this computer only** and rejects requests from other websites.

Right now the only real data collected is the 120 YouTube videos, so "My data" shows those, and the Instagram, Facebook and TikTok cards say "No data yet" with an Add button. Once posts are added (in the app, or as `posts_instagram_api_*.csv` / `posts_manual_*.csv` in `data/raw`), they appear with no restart.

## Files

| Path | Purpose |
|------|---------|
| `chatbot/server.py` | Starts the chat page |
| `chatbot/engine.py` | Loads the data and answers the questions |
| `chatbot/static/` | The page itself (HTML, CSS, JavaScript) |
| `collect_instagram.py` | Instagram collector (official API) |
| `merge_manual.py` | Validates and merges hand-collected posts |
| `COLLECTION_GUIDE.md` | The rules for collecting by hand |
| `templates/post_collection_template.csv` | Blank sheet to fill in |
| `config/social_brands.json` | Brands and Instagram usernames |
| `DATA_DICTIONARY.md` | Every column and when it is blank |
| `common.py` | Shared helpers and the post schema |
| `collect.py`, `config/brands.json` | Parked YouTube collector |
| `tests/` | Offline tests: `python -m unittest discover -s tests` |

## Ethics and limits

- Only public brand pages, through the official API or by a person reading the page.
- No usernames and no comment text are collected.
- Blank means *not collected or hidden*, never zero.
- Results describe these brands on these two platforms, not "Pakistani social media" in general.
- Likes and reach can be boosted by paid promotion, which public data does not reveal.
