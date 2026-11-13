"""
Generate a realistic demo dataset for the Pakistani Content Detective project.
Produces data/demo/posts_demo_2026-11-13.csv with ~525 rows.

Usage:
    python data/generate_demo.py
"""
from __future__ import annotations

import csv
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

PKT = timezone(timedelta(hours=5), "PKT")

# POST_COLUMNS matching common.py exactly
POST_COLUMNS = [
    "platform", "brand", "post_id", "url", "caption", "hashtags",
    "published_at_utc", "date_pkt", "weekday_pkt", "hour_pkt",
    "media_type", "likes", "comments", "shares", "views", "followers",
    "source", "collector", "collected_at_utc",
]

BRANDS = {
    "Khaadi": {
        "instagram": {"followers": 520_000, "likes": (2000, 18000), "comments": (50, 500)},
        "hashtags_pool": ["#Khaadi", "#KhaadiStyle", "#KhaadiLawn", "#PakistaniFashion",
                          "#LawnCollection", "#EidCollection", "#KhaadiEid", "#SummerLawn",
                          "#KhaadiUnstitched", "#FashionPakistan", "#KhaadiPret"],
        "caption_templates": [
            "Introducing our newest lawn prints — crafted for the season ahead. ✨ {extra}",
            "Step into elegance with Khaadi's latest collection. {extra}",
            "Because every day deserves a beautiful outfit. Shop now at Khaadi. {extra}",
            "New arrivals just dropped! 🌿 Explore Khaadi's signature prints. {extra}",
            "From our looms to your wardrobe — timeless Khaadi style. {extra}",
            "Celebrate in style with Khaadi this season. {extra}",
        ],
        "extra_eid": "✨ Eid Mubarak from Khaadi! Celebrate festivities with our exclusive edit. #EidCollection #KhaadiEid",
        "extra_lawn": "🌸 Lawn season is officially here — fresh prints, vibrant colours. #LawnCollection #SummerLawn",
    },
    "Sapphire": {
        "instagram": {"followers": 850_000, "likes": (5000, 35000), "comments": (100, 800)},
        "hashtags_pool": ["#Sapphire", "#SapphirePK", "#SapphireStyle", "#PakistaniFashion",
                          "#SapphireLawn", "#SapphireEid", "#LuxuryFashion", "#LawnSeason",
                          "#SapphireWomen", "#FashionGoals", "#SapphireUnstitched"],
        "caption_templates": [
            "Luxury meets tradition — Sapphire's new collection is here. {extra}",
            "Redefine your wardrobe with Sapphire. ✨ {extra}",
            "New arrivals: because you deserve the finest. {extra}",
            "Sapphire's signature prints, now available online. 🌸 {extra}",
            "Dressed in Sapphire — always a statement. {extra}",
            "Explore our latest collection. Elegance, redefined. {extra}",
        ],
        "extra_eid": "🌙 Eid blessings from Sapphire! Shop our exclusive Eid edit. #SapphireEid #EidMubarak",
        "extra_lawn": "🌿 Lawn fever is real — and Sapphire is here for it. #SapphireLawn #LawnSeason",
    },
    "Gul Ahmed": {
        "instagram": {"followers": 1_200_000, "likes": (3000, 22000), "comments": (80, 600)},
        "hashtags_pool": ["#GulAhmed", "#GulAhmedStyle", "#GulAhmedLawn", "#PakistaniFashion",
                          "#LawnCollection", "#EidCollection", "#GulAhmedEid", "#IdeasByGulAhmed",
                          "#WinterUnstitched", "#FashionPakistan", "#GulAhmedSale"],
        "caption_templates": [
            "GulAhmed — where every thread tells a story. {extra}",
            "New season, new prints. GulAhmed collection now live! 🌸 {extra}",
            "Timeless elegance from GulAhmed. Explore now. {extra}",
            "Because tradition never goes out of style. ✨ {extra}",
            "GulAhmed's heritage of craftsmanship continues. {extra}",
            "Shop the season's finest at GulAhmed. {extra}",
        ],
        "extra_eid": "✨ Eid Mubarak! Celebrate in GulAhmed's exclusive Eid collection. #GulAhmedEid #EidFashion",
        "extra_lawn": "🌿 The lawn season you've been waiting for — GulAhmed is here! #GulAhmedLawn #LawnCollection",
    },
    "Maria B": {
        "instagram": {"followers": 320_000, "likes": (1000, 9000), "comments": (30, 300)},
        "hashtags_pool": ["#MariaB", "#MariaBPakistan", "#MariaBLawn", "#PakistaniFashion",
                          "#LuxuryPrêt", "#EidCollection", "#MariaBEid", "#BridalWear",
                          "#MariaBBridal", "#FashionPakistan", "#Couture"],
        "caption_templates": [
            "Maria B — where couture meets culture. {extra}",
            "New collection just in! Elegance in every stitch. ✨ {extra}",
            "Dressed to impress with Maria B's latest. {extra}",
            "Luxury pret for the modern Pakistani woman. {extra}",
            "Because fashion is an art form. Maria B. {extra}",
            "Explore our newest arrivals — made for you. 🌸 {extra}",
        ],
        "extra_eid": "🌙 Eid al-Fitr with Maria B — feel extraordinary in handcrafted luxury. #MariaBEid #EidCollection",
        "extra_lawn": "🌸 Lawn season picks by Maria B — fresh, feminine, flawless. #MariaBLawn #LawnCollection",
    },
    "Sana Safinaz": {
        "instagram": {"followers": 410_000, "likes": (2000, 13000), "comments": (60, 400)},
        "hashtags_pool": ["#SanaSafinaz", "#SanaSafinazStyle", "#SanaSafinazLawn", "#PakistaniFashion",
                          "#LawnCollection", "#EidCollection", "#SanaSafinazEid", "#SSLawn",
                          "#SanaSafinazMuslin", "#FashionPakistan", "#SanaSafinazSale"],
        "caption_templates": [
            "Sana Safinaz — crafting stories through fashion. {extra}",
            "New season arrivals — because style never sleeps. ✨ {extra}",
            "Modern silhouettes, timeless prints. Sana Safinaz. {extra}",
            "Step into the new collection from Sana Safinaz. 🌸 {extra}",
            "Where art meets wardrobe. Sana Safinaz. {extra}",
            "Explore our latest — made for every occasion. {extra}",
        ],
        "extra_eid": "✨ Eid greetings from Sana Safinaz! Elevate your celebrations. #SanaSafinazEid #EidCollection",
        "extra_lawn": "🌿 SS Lawn is here and it's breathtaking! #SanaSafinazLawn #SSLawn #LawnCollection",
    },
}

PLATFORMS_SOCIAL = ["Instagram", "Facebook", "TikTok"]
# 40% reel, 30% image, 20% carousel, 10% video
MEDIA_TYPES = [
    "reel", "reel", "reel", "reel",
    "image", "image", "image",
    "carousel", "carousel",
    "video"
]

COLLECTED_AT = "2026-11-13T12:00:00Z"

# Date spread from April 2026 (for Eid spike) through November 1, 2026
START_DATE = datetime(2026, 4, 1, tzinfo=timezone.utc)
END_DATE = datetime(2026, 11, 1, tzinfo=timezone.utc)
RANGE_DAYS = (END_DATE - START_DATE).days

EID_START = datetime(2026, 4, 1, tzinfo=timezone.utc)
EID_END = datetime(2026, 4, 30, 23, 59, 59, tzinfo=timezone.utc)

LAWN_START = datetime(2026, 5, 1, tzinfo=timezone.utc)
LAWN_END = datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc)


def random_dt() -> datetime:
    offset = random.randint(0, RANGE_DAYS * 24 * 3600)
    return START_DATE + timedelta(seconds=offset)


def spike_multiplier(dt: datetime) -> float:
    if EID_START <= dt <= EID_END:
        return 3.0  # Eid spike: 3x normal
    if LAWN_START <= dt <= LAWN_END:
        return 1.8  # Lawn season spike: ~1.8x normal
    return 1.0


def platform_scale(platform: str, ig_followers: int, ig_likes: tuple, ig_comments: tuple):
    if platform == "Instagram":
        return ig_followers, ig_likes, ig_comments, False, (0, 0)
    if platform == "Facebook":
        f = int(ig_followers * 1.5)
        likes = (int(ig_likes[0] * 0.6), int(ig_likes[1] * 0.6))
        comments = (int(ig_comments[0] * 0.5), int(ig_comments[1] * 0.5))
        return f, likes, comments, False, (0, 0)
    if platform == "TikTok":
        f = int(ig_followers * 0.8)
        likes = (int(ig_likes[0] * 2.0), int(ig_likes[1] * 2.0))
        comments = (int(ig_comments[0] * 0.7), int(ig_comments[1] * 0.7))
        views = (ig_likes[0] * 10, ig_likes[1] * 50)
        return f, likes, comments, True, views
    raise ValueError(platform)


def make_hashtags(pool: list[str], n: int = 5) -> str:
    tags = random.sample(pool, min(n, len(pool)))
    return "|".join(t.lstrip("#").lower() for t in tags)


def make_caption(brand_cfg: dict, dt: datetime) -> str:
    template = random.choice(brand_cfg["caption_templates"])
    if EID_START <= dt <= EID_END:
        extra = brand_cfg["extra_eid"]
    elif LAWN_START <= dt <= LAWN_END:
        extra = brand_cfg["extra_lawn"]
    else:
        extra = ""
    return template.format(extra=extra).strip()


def time_fields(dt: datetime) -> dict:
    pkt = dt.astimezone(PKT)
    return {
        "published_at_utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date_pkt": pkt.date().isoformat(),
        "weekday_pkt": pkt.strftime("%A"),
        "hour_pkt": pkt.hour,
    }


def generate_post(platform: str, brand: str, brand_cfg: dict) -> dict:
    ig_cfg = brand_cfg["instagram"]
    followers, likes_range, comments_range, has_views, views_range = platform_scale(
        platform, ig_cfg["followers"], ig_cfg["likes"], ig_cfg["comments"]
    )

    dt = random_dt()
    mult = spike_multiplier(dt)
    tf = time_fields(dt)

    likes = int(random.randint(*likes_range) * mult)
    comments = int(random.randint(*comments_range) * mult)
    shares = int(likes * random.uniform(0.03, 0.15))
    views = int(random.randint(*views_range) * mult) if has_views else ""

    platform_slug = platform.lower().replace(" ", "")
    brand_slug = brand.lower().replace(" ", "_")
    post_id = f"{platform_slug}_{brand_slug}_{uuid4().hex[:12]}"
    url = f"https://www.{platform_slug}.com/p/{post_id}"

    caption = make_caption(brand_cfg, dt)
    hashtags = make_hashtags(brand_cfg["hashtags_pool"])
    media_type = random.choice(MEDIA_TYPES)

    return {
        "platform": platform,
        "brand": brand,
        "post_id": post_id,
        "url": url,
        "caption": caption,
        "hashtags": hashtags,
        "published_at_utc": tf["published_at_utc"],
        "date_pkt": tf["date_pkt"],
        "weekday_pkt": tf["weekday_pkt"],
        "hour_pkt": tf["hour_pkt"],
        "media_type": media_type,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "views": views,
        "followers": followers,
        "source": "demo",
        "collector": "Demo System",
        "collected_at_utc": COLLECTED_AT,
    }


def generate_youtube_post(brand: str, brand_cfg: dict) -> dict:
    dt = random_dt()
    mult = spike_multiplier(dt)
    tf = time_fields(dt)

    views = int(random.randint(500, 150_000) * mult)
    likes = int(random.randint(50, 5_000) * mult)
    comments = int(random.randint(5, 500) * mult)
    shares = int(likes * random.uniform(0.01, 0.08))

    brand_slug = brand.lower().replace(" ", "_")
    video_id = uuid4().hex[:11]
    post_id = f"yt_{brand_slug}_{video_id}"
    url = f"https://www.youtube.com/watch?v={video_id}"

    caption = make_caption(brand_cfg, dt)
    hashtags = make_hashtags(brand_cfg["hashtags_pool"])

    return {
        "platform": "YouTube",
        "brand": brand,
        "post_id": post_id,
        "url": url,
        "caption": caption,
        "hashtags": hashtags,
        "published_at_utc": tf["published_at_utc"],
        "date_pkt": tf["date_pkt"],
        "weekday_pkt": tf["weekday_pkt"],
        "hour_pkt": tf["hour_pkt"],
        "media_type": "video",
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "views": views,
        "followers": "",  # no followers for YouTube posts
        "source": "demo",
        "collector": "Demo System",
        "collected_at_utc": COLLECTED_AT,
    }


def main():
    random.seed(42)  # reproducible

    out_dir = ROOT / "data" / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "posts_demo_2026-11-13.csv"

    rows = []

    for brand, brand_cfg in BRANDS.items():
        # 30 posts per brand per social platform (30 x 3 = 90)
        for platform in PLATFORMS_SOCIAL:
            for _ in range(30):
                rows.append(generate_post(platform, brand, brand_cfg))

        # 15 YouTube posts per brand (15)
        for _ in range(15):
            rows.append(generate_youtube_post(brand, brand_cfg))

    # Shuffle for realism
    random.shuffle(rows)

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=POST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"✓ Written {len(rows)} rows to {out_path}")
    print(f"  Brands: {', '.join(BRANDS.keys())}")
    print(f"  Platforms: Instagram(30), Facebook(30), TikTok(30), YouTube(15) x {len(BRANDS)} brands")


if __name__ == "__main__":
    main()
