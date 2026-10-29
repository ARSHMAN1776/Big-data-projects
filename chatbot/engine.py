"""Answer plain-English questions about the collected posts.

Every number comes from the CSV files in data/raw. Nothing is made up and no language model is
involved: a question is matched to one of a fixed set of analyses, which is run on the data.
When the data cannot support an answer (no likes, tiny samples, no demographics) it says so.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

MIN_GROUP = 5            # a group smaller than this is flagged as a small sample
MIN_FOR_FINDING = 8      # smallest group the recommendation will build on
FINDING_P = 0.01         # stricter than 0.05 because the recommendation tests several things at once
N_PERM = 600

FILE_RE = re.compile(r"^(posts_instagram_api|posts_manual|videos_api|videos_rss)_(\d{4}-\d{2}-\d{2})\.csv$")
PRIORITY = ["posts_instagram_api", "posts_manual", "videos_api", "videos_rss"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAYPARTS = [("Late night, 12 to 5 am", range(0, 6)), ("Morning, 6 to 11 am", range(6, 12)),
            ("Afternoon, 12 to 4 pm", range(12, 17)), ("Evening, 5 to 9 pm", range(17, 22)),
            ("Night, 10 to 11 pm", range(22, 24))]
PLATFORM_NAMES = {"instagram": "Instagram", "facebook": "Facebook", "tiktok": "TikTok", "youtube": "YouTube"}
ALL_PLATFORMS = ["youtube", "instagram", "facebook", "tiktok"]
SOURCE_NAMES = {"api": "official API", "manual": "typed in by hand", "rss": "public feed"}
MEDIA_NAMES = {"short": "Shorts", "video": "Videos", "reel": "Reels", "image": "Images",
               "carousel": "Carousels", "text": "Text posts", "link": "Link posts", "other": "Other"}
THEMES = {
    "Sale and discounts": [r"\bsale\b", r"\bdiscounts?\b", r"\d+\s?%", r"\bflat\b", r"clearance", r"end of season", r"\boff\b"],
    "Winter": [r"winter", r"shawls?\b", r"jackets?\b", r"sweaters?\b", r"\bfall\b", r"velvet", r"\bcoats?\b", r"sweats?\b"],
    "Summer and lawn": [r"summer", r"\blawn\b", r"\bspring\b", r"\bcotton\b"],
    "Eid and festive": [r"\beid\b", r"ramadan", r"ramzan", r"festive", r"chand ?raat", r"mubarak"],
    "Bridal and wedding": [r"bridal", r"wedding", r"shaadi", r"nikkah", r"barat", r"walima", r"mehndi"],
    "New launch": [r"new arrivals?", r"just landed", r"launch", r"now live", r"live now", r"introducing", r"out now", r"collection", r"\bvol\b"],
    "Campaign and film": [r"campaign", r"\bfilm\b", r"\bft\b", r"featuring", r"ambassador", r"\btvc\b", r"behind the scenes"],
    "Stores and events": [r"\bstores?\b", r"outlets?\b", r"\bmall\b", r"opening", r"we are back", r"flagship", r"exhibition"],
}
_THEME_RE = {k: re.compile("|".join(v)) for k, v in THEMES.items()}

SUGGESTIONS = [
    "Which brand performs best?",
    "Which platform performs best?",
    "Shorts vs long videos",
    "What is the best day to post?",
    "Most popular hashtags",
    "Show the top 5 posts",
    "Which themes work best?",
    "What should a brand post next?",
    "What are the limits of this data?",
]

# Named analyses the agent can run: kind -> (handler, the question that selects it)
ANALYSES = {
    "overview": ("overview", "what data do you have"),
    "brands": ("brands_rank", "which brand performs best"),
    "formats": ("media", "which format works best"),
    "days": ("when", "what is the best day to post"),
    "times": ("when", "what is the best time of day"),
    "hashtags": ("hashtags", "most popular hashtags"),
    "themes": ("themes", "which themes work best"),
    "top_posts": ("top_posts", "show the top posts"),
    "brand_profile": ("brand_profile", "tell me about this brand"),
    "compare_brands": ("compare_brands", "compare two brands"),
    "platforms": ("platform", "which platform performs best"),
    "recommend": ("recommend", "what should a brand post next"),
    "limits": ("limits", "what are the limits of this data"),
}

# The cards on the overview screen: (id, title, plain-words help, analysis, the question it answers)
DASH_SECTIONS = [
    ("brands", "Which brands do best?",
     "Brands ranked by what one typical post achieves. “Typical” means the median: the middle post, so half did better and half did worse.",
     "brands_rank", "which brand performs best"),
    ("platforms", "Which platform does best?",
     "The same measure for every platform, so you can see where posts perform best.",
     "platform", "which platform performs best"),
    ("formats", "Which format works best?",
     "Photos, carousels, reels, videos and Shorts compared. Each bar is the typical result of one post in that format.",
     "media", "which format works best"),
    ("days", "Which day is best to post?",
     "The typical result of posts published on each weekday, in Pakistan time.",
     "when", "what is the best day to post"),
    ("times", "Which time of day is best?",
     "The typical result of posts published in each part of the day, in Pakistan time. Only posts with a known time are counted.",
     "when", "what is the best time of day"),
    ("hashtags", "Which hashtags are used, and which work?",
     "How often each hashtag appears, and which ones come with the best results.",
     "hashtags", "most popular hashtags"),
    ("themes", "Which topics work best?",
     "Posts grouped by what they talk about (sale, winter, Eid and so on), found from keywords in the caption.",
     "themes", "which themes work best"),
    ("top", "The five best posts",
     "The single best-performing posts, with a link where we have one.",
     "top_posts", "show the top 5 posts"),
    ("next", "What should we test next?",
     "Only patterns that pass a strict chance test appear here. If there are none, that is the honest answer.",
     "recommend", "what should a brand post next"),
]


# ---------------------------------------------------------------- loading

def find_files(raw_dir: Path) -> list[Path]:
    """Newest file of each kind, in priority order."""
    latest: dict[str, tuple[str, Path]] = {}
    for p in raw_dir.glob("*.csv"):
        m = FILE_RE.match(p.name)
        if m and (m.group(1) not in latest or m.group(2) > latest[m.group(1)][0]):
            latest[m.group(1)] = (m.group(2), p)
    return [latest[k][1] for k in PRIORITY if k in latest]


def load_file(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, encoding="utf-8-sig")
    out = pd.DataFrame(index=raw.index)
    if path.name.startswith("videos_"):
        out["platform"] = "youtube"
        out["post_id"] = raw["video_id"]
        out["text"] = raw["title"]
        out["media_type"] = raw["format"]
        out["comments"] = raw.get("comment_count")
        out["shares"] = np.nan
        out["followers"] = np.nan
        for c in ("brand", "url", "hashtags", "date_pkt", "weekday_pkt", "hour_pkt", "likes", "views", "source", "collected_at_utc"):
            out[c] = raw.get(c)
    else:
        out["text"] = raw["caption"]
        for c in ("platform", "brand", "post_id", "url", "hashtags", "date_pkt", "weekday_pkt", "hour_pkt",
                  "media_type", "likes", "comments", "shares", "views", "followers", "source", "collected_at_utc"):
            out[c] = raw.get(c)
    out["file"] = path.name
    return out


def prepare(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(["platform", "post_id"], keep="first").reset_index(drop=True)
    for c in ("likes", "comments", "shares", "views", "followers", "hour_pkt"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["text"] = df["text"].fillna("").astype(str)
    df["hashtags"] = df["hashtags"].fillna("").astype(str)
    df["date"] = pd.to_datetime(df["date_pkt"], errors="coerce")
    collected = pd.to_datetime(df["collected_at_utc"].astype(str).str[:10], errors="coerce")
    df["age_days"] = (collected - df["date"]).dt.days.clip(lower=0)
    df["engagement"] = np.where(df["likes"].notna() & df["comments"].notna(),
                                df["likes"] + df["comments"] + df["shares"].fillna(0), np.nan)
    df["eng_rate"] = np.where(df["followers"] > 0, df["engagement"] / df["followers"] * 100, np.nan)
    return df


class DataStore:
    """Loads the newest files and reloads by itself when a collector writes a new one."""

    def __init__(self, raw_dir: Path):
        self.raw_dir = Path(raw_dir)
        self._sig = None
        self._df = pd.DataFrame()
        self.files: list[Path] = []

    def frame(self) -> pd.DataFrame:
        files = find_files(self.raw_dir) if self.raw_dir.exists() else []
        sig = tuple((p.name, p.stat().st_mtime_ns) for p in files)
        if sig != self._sig:
            self._df = prepare([load_file(p) for p in files])
            self.files, self._sig = files, sig
        return self._df


# ---------------------------------------------------------------- numbers

@dataclass
class Metric:
    col: str
    label: str            # "engagement per 100 followers"
    phrase: str           # "median engagement per 100 followers per post"
    note: str | None = None

    def fmt(self, v) -> str:
        if v is None or pd.isna(v):
            return "n/a"
        if self.col == "eng_rate":
            return f"{v:.2f}" if v < 10 else f"{v:.1f}"
        return f"{v:,.0f}" if abs(v) >= 10 else f"{v:,.1f}".rstrip("0").rstrip(".")


_M = {
    "eng_rate": ("engagement per 100 followers", "median engagement per 100 followers"),
    "engagement": ("likes + comments (+ shares)", "median likes + comments (+ shares)"),
    "views": ("views", "median views"),
    "likes": ("likes", "median likes"),
    "comments": ("comments", "median comments"),
    "shares": ("shares", "median shares"),
}


def choose_metric(df: pd.DataFrame, wanted: str | None) -> Metric:
    cov = lambda c: float(df[c].notna().mean()) if len(df) else 0.0  # noqa: E731
    mk = lambda c, note=None: Metric(c, _M[c][0], _M[c][1], note)  # noqa: E731
    if wanted in ("likes", "comments", "shares", "views"):
        if cov(wanted):
            return mk(wanted)
        prefix = f"This data has no {wanted.rstrip('s')} counts, so I used "
    elif wanted == "eng_rate":
        prefix = "Follower counts are missing, so I used "
    else:
        prefix = None
    if cov("eng_rate") >= 0.5:
        return mk("eng_rate", None if wanted in (None, "eng_rate", "engagement") else prefix + "engagement per follower instead.")
    if cov("engagement") >= 0.5:
        return mk("engagement", (prefix or "") + ("raw likes + comments. They are not adjusted for account size, so large accounts look better than they perform."
                                                  if prefix else "Follower counts are missing, so these are raw likes + comments. They are not adjusted for account size, so large accounts look better than they perform."))
    if cov("views"):
        why = ("This data has no likes or comments yet, only views. Views show reach, not engagement."
               if wanted != "views" else None)
        return mk("views", why)
    return mk("views", "No numeric metrics were found in this data.")


def fmt_date(d) -> str:
    d = pd.Timestamp(d)
    return f"{d.day} {d.strftime('%b %Y')}"


def clip(text: str, n: int = 80) -> str:
    t = re.sub(r"\s+", " ", str(text)).strip() or "(no caption)"
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def summarize(d: pd.DataFrame, key: str, mcol: str) -> pd.DataFrame:
    d = d.dropna(subset=[mcol, key])
    if d.empty:
        return pd.DataFrame(columns=[key, "n", "median", "age"])
    return (d.groupby(key).agg(n=(mcol, "size"), median=(mcol, "median"), age=("age_days", "median"))
            .reset_index())


CHANCE_NOTE = ("Chance test: values were shuffled among posts of the same brand on the same platform hundreds of times, so a big brand or a high-engagement platform cannot create a pattern by itself. "
               "It does not account for budget, topic or post age, and repeated posts of one campaign count as separate posts.")


def perm_p(values: np.ndarray, members: dict, strata=None, min_n: int = MIN_GROUP, n_perm: int = N_PERM, seed: int = 7):
    """Chance that the best group would beat the rest by this much if the groups meant nothing.

    The statistic is the largest 'group median minus the median of everything else'. Values are
    shuffled only among posts of the same stratum (the brand), so brand size cannot drive the
    result, and taking the best of several groups is allowed for. Returns None when no group
    is big enough.
    """
    names = [k for k, m in members.items() if m.sum() >= min_n and (~m).sum() >= min_n]
    if not names:
        return None
    stat = lambda v: max(np.median(v[members[k]]) - np.median(v[~members[k]]) for k in names)  # noqa: E731
    obs = stat(values)
    if strata is None:
        blocks = [np.arange(len(values))]
    else:
        s = np.asarray(strata)
        blocks = [np.flatnonzero(s == u) for u in np.unique(s)]
    blocks = [b for b in blocks if len(b) > 1]
    rng = np.random.default_rng(seed)
    v = values.copy()
    hits = 0
    for _ in range(n_perm):
        for b in blocks:
            v[b] = rng.permutation(v[b])
        hits += stat(v) >= obs
    return (hits + 1) / (n_perm + 1)


def fmt_p(p) -> str:
    return "p < 0.01" if p < 0.01 else f"p = {p:.2f}"


def chance_phrase(p, n_best: int | None = None) -> str:
    if n_best is not None and n_best < MIN_FOR_FINDING:
        return f"The leading group has only {n_best} posts, too few to call this a pattern."
    if p is None:
        return "I could not test whether this is chance (groups too small)."
    if p < 0.01:
        return "This gap is very unlikely to be chance (p < 0.01)."
    if p < 0.05:
        return f"This gap is unlikely to be chance (p = {p:.2f})."
    return f"This gap could easily be chance (p = {p:.2f}), so treat it as a hint, not a result."


def age_note(g: pd.DataFrame) -> str | None:
    ages = g["age"].dropna()
    if len(ages) >= 2 and ages.max() - ages.min() > 30 and ages.max() > 2 * max(ages.min(), 1):
        return (f"Posts differ a lot in age (typical age from {ages.min():.0f} to {ages.max():.0f} days). "
                "Older posts have had longer to collect views and likes, so the comparison favours them.")
    return None


# ---------------------------------------------------------------- understanding the question

@dataclass
class Ents:
    q: str
    brands: list = field(default_factory=list)
    platforms: list = field(default_factory=list)
    media: list = field(default_factory=list)
    n: int | None = None
    metric: str | None = None
    quote: bool = True      # False when the answer goes to a model: post text then appears only in fenced table cells


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_MEDIA_RE = {"short": r"\bshorts?\b", "reel": r"\breels?\b", "video": r"\b(?:long )?videos?\b|\blong ?form\b",
             "image": r"\b(?:images?|photos?|pictures?)\b", "carousel": r"\b(?:carousels?|albums?)\b"}
_PLATFORM_RE = {"instagram": r"\b(?:instagram|insta|ig)\b", "facebook": r"\b(?:facebook|fb)\b", "youtube": r"\b(?:youtube|yt|you tube)\b",
                "tiktok": r"\b(?:tiktok|tik tok|tt)\b"}


def norm(question: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w#%\s]", " ", question.lower())).strip()


def brand_aliases(brands: list[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    firsts: dict[str, list] = {}
    for b in brands:
        low = norm(b)
        aliases[low] = b
        aliases[low.replace(" ", "")] = b
        parts = low.split()
        if len(parts) > 1:
            firsts.setdefault(parts[0], []).append(b)
            firsts.setdefault(parts[-1], []).append(b)
    for word, owners in firsts.items():
        if len(owners) == 1 and len(word) >= 4:
            aliases.setdefault(word, owners[0])
    aliases.setdefault("khadi", aliases.get("khaadi", ""))
    aliases = {k: v for k, v in aliases.items() if v}
    if "junaid jamshed" in aliases:
        aliases["jj"] = aliases["junaid jamshed"]
    return aliases


def parse(q: str, brands: list[str]) -> Ents:
    e = Ents(q=q)
    aliases = brand_aliases(brands)
    rest = f" {q} "
    for alias in sorted(aliases, key=len, reverse=True):
        pat = rf"(?<!\w){re.escape(alias)}(?!\w)"
        if re.search(pat, rest):
            if aliases[alias] not in e.brands:
                e.brands.append(aliases[alias])
            rest = re.sub(pat, " ", rest)
    for tok in rest.split():
        if len(tok) >= 5 and tok not in {"videos", "shorts", "posts", "brand", "brands", "views", "likes"}:
            hit = difflib.get_close_matches(tok, list(aliases), n=1, cutoff=0.84)
            if hit and aliases[hit[0]] not in e.brands:
                e.brands.append(aliases[hit[0]])
    e.platforms = [k for k, p in _PLATFORM_RE.items() if re.search(p, q)]
    e.media = [k for k, p in _MEDIA_RE.items() if re.search(p, q)]
    m = re.search(r"\b(?:top|best)\s+(\d+|" + "|".join(_NUM_WORDS) + r")\b", q) or re.search(r"\b(\d+)\s+(?:best|top)\b", q)
    if m:
        s = m.group(1)
        e.n = int(s) if s.isdigit() else _NUM_WORDS[s]
    if re.search(r"engagement rate|per follower|engagement per|relative to followers", q):
        e.metric = "eng_rate"
    elif re.search(r"\b(?:views?|viewed|watched)\b", q):
        e.metric = "views"
    elif re.search(r"\b(?:likes?|liked)\b", q):
        e.metric = "likes"
    elif re.search(r"\b(?:comments?|commented)\b", q):
        e.metric = "comments"
    elif re.search(r"\b(?:shares?|shared)\b", q):
        e.metric = "shares"
    elif re.search(r"\b(?:engagement|engaging|engage)\b", q):
        e.metric = "engagement"
    return e


def route(e: Ents) -> str:
    q = e.q
    has = lambda p: re.search(p, q) is not None  # noqa: E731
    if has(r"\b(?:audience|demographics?|gender|age groups?|cities|which city|sentiment|mood|emotions?|opinions?|feelings?|comment text)\b") \
            or has(r"\bwho (?:responds|reacts|likes|engages|watches|follows|comments)\b"):
        return "cannot"
    if has(r"\b(?:limits?|limitations?|caveats?|reliable|reliability|trust|bias\w*|accura\w*|how good|weakness\w*|can i rely)\b"):
        return "limits"
    if has(r"\b(?:recommend\w*|suggest\w*|advice|strategy|post next|what to post|next post|improve)\b") or has(r"\bshould\b.*\b(?:post|try|do|make)\b"):
        return "recommend"
    if re.match(r"^(?:hi|hello|hey|salam|assalam\w*|aoa)\b", q) or has(r"\b(?:help|what can you|what do you do|how do i use|examples?)\b"):
        return "help"
    if has(r"\bhashtags?\b|#\w"):
        return "hashtags"
    if len(e.brands) >= 2:
        return "compare_brands"
    if len(e.platforms) >= 2 or has(r"\bplatforms?\b"):
        return "platform"
    if has(r"\b(?:day|days|weekday|weekdays|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday|hour|hours|time|times|morning|afternoon|evening|night|daypart|when)\b"):
        return "when"
    if has(r"\b(?:formats?|content types?|type of content|kind of content|media types?|long ?form)\b") or len(e.media) >= 2:
        return "media"
    if has(r"\b(?:themes?|topics?|subjects?|keywords?|sale|discount|winter|summer|eid|bridal|wedding|campaign)\b"):
        return "themes"
    if has(r"\bbrands?\b") and not e.brands:
        return "brands_rank"
    if has(r"\b(?:top|best|most|highest|biggest|viral|popular|worst|lowest)\b") or has(r"\b(?:viewed|liked|commented|engaging)\b"):
        return "top_posts"
    if e.media:
        return "media"
    if e.brands:
        return "brand_profile"
    if has(r"\b(?:how many|number of|count|data|dataset|summary|overview|sources?|brands|platforms|posts|have you)\b"):
        return "overview"
    return "unknown"


# ---------------------------------------------------------------- the engine

def reply(text, blocks=None, notes=None, evidence="", follow_ups=None, intent="") -> dict:
    return {"intent": intent, "text": text, "blocks": blocks or [], "notes": notes or [],
            "evidence": evidence, "follow_ups": follow_ups or []}


def warn(t):
    return {"level": "warn", "text": t}


def info_note(t):
    return {"level": "info", "text": t}


def method_note(t):
    return {"level": "method", "text": t}


def bars(title, items, unit="", order_note=None):
    return {"type": "bars", "title": title, "unit": unit, "items": items, "note": order_note}


def table(columns, rows):
    return {"type": "table", "columns": [{"key": k, "label": l, "align": a} for k, l, a in columns], "rows": rows}


class Engine:
    def __init__(self, store: DataStore):
        self.store = store
        self._cache: dict = {}

    # -- public

    def _platform_status(self, full: pd.DataFrame) -> list[dict]:
        out = []
        for key in ALL_PLATFORMS:
            d = full[full["platform"] == key] if len(full) else full
            has = len(d) > 0
            out.append({
                "key": key, "name": PLATFORM_NAMES[key], "rows": int(len(d)),
                "brands": int(d["brand"].nunique()) if has else 0,
                "date_from": fmt_date(d["date"].min()) if has else None,
                "date_to": fmt_date(d["date"].max()) if has else None,
                "sources": [SOURCE_NAMES.get(s, s) for s in sorted(d["source"].dropna().unique())] if has else [],
            })
        return out

    def info(self) -> dict:
        df = self.store.frame()
        base = {"suggestions": SUGGESTIONS, "platform_status": self._platform_status(df)}
        if df.empty:
            return {**base, "empty": True, "rows": 0, "brands": 0, "brand_list": [], "platforms": [], "date_from": None,
                    "date_to": None, "collected": None, "files": []}
        return {
            **base,
            "empty": False, "rows": int(len(df)), "brands": int(df["brand"].nunique()),
            "brand_list": sorted(df["brand"].dropna().unique()),
            "platforms": [{"key": p, "name": PLATFORM_NAMES.get(p, p), "rows": int((df.platform == p).sum())}
                          for p in ALL_PLATFORMS if (df.platform == p).any()],
            "date_from": fmt_date(df["date"].min()), "date_to": fmt_date(df["date"].max()),
            "collected": fmt_date(pd.to_datetime(df["collected_at_utc"].astype(str).str[:10], errors="coerce").max()),
            "files": [{"name": p.name} for p in self.store.files],
        }

    def dashboard(self, platform: str | None = None, brand: str | None = None) -> dict:
        """Every analysis at once, for the overview screen. Filters are applied before the analyses run."""
        full = self.store.frame()
        key = ("dash", self.store._sig, platform, brand)
        if key in self._cache:
            return self._cache[key]
        status = self._platform_status(full)
        df = full
        if brand and len(full):
            df = df[df["brand"] == brand]
        full_b = df
        if platform and len(df):
            df = df[df["platform"] == platform]
        if df.empty:
            return {"empty": True, "platform_status": status, "kpis": [], "sections": []}
        m = choose_metric(df, None)
        in_use = [s["name"] for s in status if s["rows"]]
        short_why = {"eng_rate": "likes + comments per 100 followers", "engagement": "likes + comments, no follower counts yet",
                     "views": "views only, no likes or comments yet"}[m.col]
        kpis = [
            {"label": "Posts", "value": f"{len(df):,}", "hint": f"from {df['brand'].nunique()} brand" + ("s" if df["brand"].nunique() != 1 else "")},
            {"label": "Platforms with data", "value": f"{len(in_use)} of {len(ALL_PLATFORMS)}", "hint": ", ".join(in_use)},
            {"label": "Posts published", "value": f"{fmt_date(df['date'].min())} to {fmt_date(df['date'].max())}", "hint": "Pakistan time"},
            {"label": "Main measure", "value": m.label, "hint": short_why},
            {"label": "Typical post", "value": m.fmt(df[m.col].median()), "hint": f"{m.label}, median"},
        ]
        brands = sorted(full["brand"].dropna().unique())
        sections = []
        for sid, title, help_, intent, q in DASH_SECTIONS:
            if (sid == "brands" and brand) or (sid == "platforms" and platform):
                continue
            e = parse(norm(q), brands)
            e.brands, e.platforms = [], []  # the filters were already applied above
            try:
                ans = getattr(self, "h_" + intent)(df, e, full_b, platform)
            except Exception as exc:  # one broken card must not blank the whole page
                ans = reply("This card could not be built from the data.", notes=[warn(f"{type(exc).__name__}: {exc}")], intent=intent)
            sections.append({"id": sid, "title": title, "help": help_, "question": q[0].upper() + q[1:] + "?", "answer": ans})
        out = {"empty": False, "platform_status": status, "kpis": kpis, "sections": sections}
        if len(self._cache) > 200:
            self._cache.clear()
        self._cache[key] = out
        return out

    def analyze(self, kind: str, *, platform: str | None = None, brand: str | None = None, brand_b: str | None = None,
                metric: str | None = None, top_n: int | None = None) -> dict:
        """Run one named analysis with explicit parameters (used by the agent's tools).

        Returns the same evidence-carrying answer dict as ask(). Raises ValueError for a bad request, with a
        message that says what is allowed, so a caller can pass it straight back to the model.
        """
        if kind not in ANALYSES:
            raise ValueError(f"unknown analysis '{kind}'. Choose one of: {', '.join(ANALYSES)}")
        full = self.store.frame()
        if full.empty:
            return reply("There is no data yet.", notes=[info_note("Collect or add posts first.")], intent="empty")
        brands = sorted(full["brand"].dropna().unique())
        for b in (brand, brand_b):
            if b and b not in brands:
                raise ValueError(f"unknown brand '{b}'. Brands in the data: {', '.join(brands)}")
        if platform and platform not in PLATFORM_NAMES:
            raise ValueError(f"unknown platform '{platform}'. Choose one of: {', '.join(PLATFORM_NAMES)}")
        if metric and metric not in _M:
            raise ValueError(f"unknown metric '{metric}'. Choose one of: {', '.join(_M)}")
        intent, question = ANALYSES[kind]
        e = parse(norm(question), brands)
        e.brands, e.platforms, e.media = [], [], []
        e.metric = metric or None
        e.quote = False
        if top_n:
            e.n = max(1, min(int(top_n), 15))
        df = full
        if kind == "brand_profile":
            if not brand:
                raise ValueError("brand_profile needs a brand")
            e.brands = [brand]
        elif kind == "compare_brands":
            if not (brand and brand_b):
                raise ValueError("compare_brands needs brand and brand_b")
            e.brands = [brand, brand_b]
        elif brand:
            df = df[df["brand"] == brand]
        full_b = df
        if platform:
            df = df[df["platform"] == platform]
        if df.empty:
            what = " ".join(x for x in (brand, PLATFORM_NAMES.get(platform)) if x) or "matching"
            return reply(f"There are no {what} posts in the data yet.", intent=intent)
        key = ("analyze", self.store._sig, kind, platform, brand, brand_b, metric, top_n)
        if key in self._cache:
            return self._cache[key]
        out = getattr(self, "h_" + intent)(df, e, full_b, platform)
        self._cache[key] = out
        return out

    def ask(self, question: str, platform: str | None = None, brand: str | None = None) -> dict:
        full = self.store.frame()
        if full.empty:
            return reply("There is no data to ask about yet.", notes=[info_note(
                "Run a collector first: python collect_instagram.py, or hand-collect posts and run python merge_manual.py. "
                "Files appear in data/raw and this page picks them up automatically.")], intent="empty")
        q = norm(question)
        if not q:
            return reply("Type a question about the posts, for example “Which brand performs best?”.", intent="unknown")
        key = (self.store._sig, q, platform, brand)
        if key in self._cache:
            return self._cache[key]
        e = parse(q, sorted(full["brand"].dropna().unique()))
        intent = route(e)
        plat = e.platforms[0] if len(e.platforms) == 1 else (platform or None)
        if brand:
            full = full[full["brand"] == brand]
        df = full[full["platform"] == plat] if plat else full
        if df.empty:
            what = " ".join(x for x in (brand, PLATFORM_NAMES.get(plat, plat)) if x)
            return reply(f"There are no {what} posts in the data yet.", intent=intent, follow_ups=["What data do you have?"])
        ans = getattr(self, "h_" + intent)(df, e, full, plat)
        if len(self._cache) > 200:
            self._cache.clear()
        self._cache[key] = ans
        return ans

    # -- helpers

    def _scope(self, brand=None, plat=None) -> str:
        bits = []
        if brand:
            bits.append(f"for {brand}")
        if plat:
            bits.append(f"on {PLATFORM_NAMES.get(plat, plat)}")
        return (" " + " ".join(bits)) if bits else ""

    def _evidence(self, d: pd.DataFrame, m: Metric, extra: str = "") -> str:
        s = f"n = {len(d):,} posts · {m.phrase} · posted {fmt_date(d['date'].min())} to {fmt_date(d['date'].max())}"
        return s + (f" · {extra}" if extra else "")

    def _bar_items(self, g: pd.DataFrame, key: str, m: Metric, name=lambda x: x, top=None):
        items = []
        for _, r in g.iterrows():
            items.append({"label": name(r[key]), "value": float(r["median"]), "display": m.fmt(r["median"]),
                          "note": f"n = {int(r['n'])}", "highlight": top is not None and r[key] == top,
                          "flag": bool(r["n"] < MIN_GROUP)})
        return items

    def _mix(self, d: pd.DataFrame, notes: list) -> None:
        n = d["platform"].nunique()
        if n > 1:
            notes.append(warn(f"This mixes {n} platforms, and platforms have very different typical results (and some formats exist on only one). "
                              "Choose a single platform in the filter at the top for a fairer comparison."))

    def _small_note(self, g: pd.DataFrame):
        small = g[g["n"] < MIN_GROUP]
        if len(small):
            return warn(f"Small samples (fewer than {MIN_GROUP} posts, marked in red): "
                        + ", ".join(str(x) for x in small.iloc[:, 0]) + ". Their numbers can swing a lot.")
        return None

    # -- handlers

    def h_help(self, df, e, full, plat):
        return reply(
            "I answer questions about the posts you have collected. Every number is calculated from your files, and I tell you "
            "how many posts it rests on.",
            blocks=[table([("topic", "You can ask about", "left"), ("example", "For example", "left")], [
                {"topic": "Brands", "example": "Which brand performs best?"},
                {"topic": "One brand", "example": "Tell me about Khaadi"},
                {"topic": "Two brands", "example": "Compare Khaadi and Gul Ahmed"},
                {"topic": "Formats", "example": "Shorts vs long videos"},
                {"topic": "Timing", "example": "What is the best day to post?"},
                {"topic": "Hashtags and themes", "example": "Most popular hashtags"},
                {"topic": "Best posts", "example": "Show the top 5 posts"},
                {"topic": "Next steps", "example": "What should a brand post next?"},
            ])], follow_ups=["What data do you have?", "What are the limits of this data?"], intent="help")

    def h_overview(self, df, e, full, plat):
        m = choose_metric(df, None)
        by = df.groupby("brand").agg(posts=("post_id", "size"), plats=("platform", lambda s: ", ".join(PLATFORM_NAMES.get(x, x) for x in sorted(set(s))))).reset_index().sort_values("posts", ascending=False)
        text = (f"I have {len(df):,} posts from {df['brand'].nunique()} brands on "
                f"{', '.join(PLATFORM_NAMES.get(p, p) for p in sorted(df['platform'].unique()))}, "
                f"posted between {fmt_date(df['date'].min())} and {fmt_date(df['date'].max())}.")
        notes = []
        cov = lambda c: df[c].notna().mean()  # noqa: E731
        notes.append(info_note(f"Coverage: views {cov('views'):.0%}, likes {cov('likes'):.0%}, comments {cov('comments'):.0%}, "
                               f"followers {cov('followers'):.0%} of posts. Blank means the source did not provide it, not zero."))
        if m.note:
            notes.append(warn(m.note))
        if df["platform"].nunique() == 1:
            notes.append(warn(f"Only {PLATFORM_NAMES.get(df['platform'].iloc[0])} data is loaded, so platforms cannot be compared yet."))
        return reply(text, blocks=[table([("brand", "Brand", "left"), ("posts", "Posts", "right"), ("plats", "Platforms", "left")],
                                         [{"brand": r.brand, "posts": f"{r.posts:,}", "plats": r.plats} for r in by.itertuples()])],
                     notes=notes, evidence=self._evidence(df, m), intent="overview",
                     follow_ups=["Which brand performs best?", "What are the limits of this data?"])

    def h_brands_rank(self, df, e, full, plat):
        m = choose_metric(df, e.metric)
        g = summarize(df, "brand", m.col).sort_values("median", ascending=False)
        if g.empty:
            return reply("No post has the numbers I need for that.", intent="brands_rank")
        top = g.iloc[0]
        notes = [warn(m.note)] if m.note else []
        self._mix(df, notes)
        text = f"{top['brand']} leads on {m.label}{self._scope(plat=plat)}: median {m.fmt(top['median'])} per post across {int(top['n'])} posts."
        if len(g) > 1:
            text += f" Next is {g.iloc[1]['brand']} with {m.fmt(g.iloc[1]['median'])}."
        for n_ in (age_note(g), self._small_note(g.rename(columns={"brand": "brand"}))):
            if n_:
                notes.append(n_ if isinstance(n_, dict) else warn(n_))
        tbl = table([("brand", "Brand", "left"), ("n", "Posts", "right"), ("age", "Typical post age, days", "right"), ("median", "Median", "right")],
                    [{"brand": r.brand, "n": f"{int(r.n):,}", "age": "n/a" if pd.isna(r.age) else f"{r.age:.0f}", "median": m.fmt(r.median)} for r in g.itertuples()])
        return reply(text, blocks=[bars(f"Median {m.label} per post, by brand", self._bar_items(g, "brand", m, top=top["brand"])), tbl],
                     notes=notes, evidence=self._evidence(df, m), intent="brands_rank",
                     follow_ups=[f"Tell me about {top['brand']}", "Show the top 5 posts", "What should a brand post next?"])

    def h_top_posts(self, df, e, full, plat):
        m = choose_metric(df, e.metric)
        d = df
        if e.brands:
            d = d[d["brand"].isin(e.brands)]
        if e.media:
            d = d[d["media_type"].isin(e.media)]
        d = d.dropna(subset=[m.col])
        if d.empty:
            return reply("No posts match that.", intent="top_posts")
        asc = bool(re.search(r"\b(?:worst|lowest)\b", e.q))
        n = min(e.n or 5, 15, len(d))
        t = d.sort_values(m.col, ascending=asc).head(n)
        first = t.iloc[0]
        label = ", ".join(e.brands) if e.brands else None
        who = f"{first['brand']}: “{clip(first['text'], 60)}”" if e.quote else first["brand"]
        text = (f"{'Lowest' if asc else 'Top'} {n} posts by {m.label}{self._scope(label, plat)}. "
                f"#1 is {who} with {m.fmt(first[m.col])}.")
        notes = [warn(m.note)] if m.note else []
        med = d[m.col].median()
        if not asc and med > 0 and first[m.col] > 10 * med:
            notes.append(info_note(f"The top post has {first[m.col] / med:,.0f} times the median ({m.fmt(med)}). A few hits can dominate a total, so compare medians when judging a brand."))
        rows = [{"rank": str(i + 1), "brand": r.brand, "post": {"text": clip(r.text), "href": r.url if isinstance(r.url, str) and r.url.startswith("http") else ""},
                 "type": MEDIA_NAMES.get(r.media_type, str(r.media_type)), "date": fmt_date(r.date), "value": m.fmt(getattr(r, m.col))}
                for i, r in enumerate(t.itertuples())]
        return reply(text, blocks=[table([("rank", "#", "right"), ("brand", "Brand", "left"), ("post", "Post", "left"), ("type", "Type", "left"),
                                          ("date", "Posted", "left"), ("value", m.label.capitalize(), "right")], rows)],
                     notes=notes, evidence=self._evidence(d, m), intent="top_posts",
                     follow_ups=["Which brand performs best?", "Shorts vs long videos"])

    def _tested_groups(self, d: pd.DataFrame, key: str, m: Metric, order=None):
        d = d.dropna(subset=[m.col, key])
        g = summarize(d, key, m.col)
        if order:
            g = g.set_index(key).reindex([o for o in order if o in set(g[key])]).reset_index()
        vals = d[m.col].to_numpy(dtype=float)
        members = {k: (d[key] == k).to_numpy() for k in g[key]}
        return g, perm_p(vals, members, (d["brand"] + "|" + d["platform"]).to_numpy())

    def h_media(self, df, e, full, plat):
        m = choose_metric(df, e.metric)
        d = df[df["brand"].isin(e.brands)] if e.brands else df
        g, p = self._tested_groups(d, "media_type", m)
        g = g.sort_values("median", ascending=False)
        scope = self._scope(", ".join(e.brands) or None, plat)
        if len(g) == 0:
            return reply(f"No posts{scope} have the numbers needed to compare formats.", intent="media", follow_ups=["What data do you have?"])
        if len(g) < 2:
            only = MEDIA_NAMES.get(g.iloc[0]["media_type"], g.iloc[0]["media_type"])
            return reply(f"I can only see {only.lower()} in this data{scope}, so there is nothing to compare yet.", intent="media",
                         notes=[warn(m.note)] if m.note else [], follow_ups=["What data do you have?"])
        a, b = g.iloc[0], g.iloc[1]
        ratio = a["median"] / b["median"] if b["median"] else float("inf")
        text = (f"{MEDIA_NAMES.get(a['media_type'], a['media_type'])} lead{scope}: median {m.fmt(a['median'])} {m.label} per post "
                f"against {m.fmt(b['median'])} for {MEDIA_NAMES.get(b['media_type'], b['media_type'])}"
                + (f" ({ratio:.1f} times as much). " if np.isfinite(ratio) else ". ") + chance_phrase(p, int(a["n"])))
        notes = [warn(m.note)] if m.note else []
        self._mix(df, notes)
        notes.append(info_note("A gap between formats is not proof that the format causes it. Budget, topic, timing and post age differ between formats too."))
        notes.append(method_note(CHANCE_NOTE))
        for x in (age_note(g), self._small_note(g)):
            if x:
                notes.append(x if isinstance(x, dict) else warn(x))
        names = lambda k: MEDIA_NAMES.get(k, k)  # noqa: E731
        return reply(text, blocks=[bars(f"Median {m.label} per post, by format", self._bar_items(g, "media_type", m, names, a["media_type"])),
                                   table([("t", "Format", "left"), ("n", "Posts", "right"), ("age", "Typical post age, days", "right"), ("m", "Median", "right")],
                                         [{"t": names(r.media_type), "n": f"{int(r.n):,}", "age": "n/a" if pd.isna(r.age) else f"{r.age:.0f}", "m": m.fmt(r.median)} for r in g.itertuples()])],
                     notes=notes, evidence=self._evidence(d, m, f"chance test {fmt_p(p)}" if p is not None else ""), intent="media",
                     follow_ups=["What is the best day to post?", "What should a brand post next?"])

    def h_when(self, df, e, full, plat):
        m = choose_metric(df, e.metric)
        d = df[df["brand"].isin(e.brands)] if e.brands else df
        q = e.q.replace("time of day", " timeofday ")
        want_day = bool(re.search(r"\b(?:day|days|weekday|weekdays|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", q))
        want_hour = bool(re.search(r"\b(?:hour|hours|morning|afternoon|evening|night|daypart|timeofday)\b", q))
        if not want_day and not want_hour:
            want_day = want_hour = True
        blocks, notes, bits, pvals = [], [warn(m.note)] if m.note else [], [], []
        self._mix(df, notes)
        scope = self._scope(", ".join(e.brands) or None, plat)
        if want_day:
            g, p = self._tested_groups(d, "weekday_pkt", m, WEEKDAYS)
            if len(g) >= 2:
                best = g.sort_values("median", ascending=False).iloc[0]
                bits.append(f"Best day{scope}: {best['weekday_pkt']} (median {m.fmt(best['median'])}, {int(best['n'])} posts). {chance_phrase(p, int(best['n']))}")
                blocks.append(bars(f"Median {m.label} per post, by weekday (Pakistan time)", self._bar_items(g, "weekday_pkt", m, top=best["weekday_pkt"])))
                pvals.append(f"day {fmt_p(p)}" if p is not None else "")
                sm = self._small_note(g)
                if sm:
                    notes.append(sm)
        if want_hour:
            h = d.dropna(subset=["hour_pkt"]).copy()
            if len(h) < MIN_GROUP * 2:
                notes.append(warn(f"The posting hour is known for only {len(h)} of {len(d)} posts{scope}. Instagram's website does not show it, so hour-of-day needs the API or Facebook."))
            else:
                h["daypart"] = h["hour_pkt"].astype(int).map({hr: lab for lab, rg in DAYPARTS for hr in rg})
                g, p = self._tested_groups(h, "daypart", m, [lab for lab, _ in DAYPARTS])
                if len(g) >= 2:
                    best = g.sort_values("median", ascending=False).iloc[0]
                    bits.append(f"Best time of day: {best['daypart']} (median {m.fmt(best['median'])}, {int(best['n'])} posts). {chance_phrase(p, int(best['n']))}")
                    blocks.append(bars(f"Median {m.label} per post, by time of day (Pakistan time)", self._bar_items(g, "daypart", m, top=best["daypart"])))
                    pvals.append(f"time {fmt_p(p)}" if p is not None else "")
                    if len(h) < len(d):
                        notes.append(info_note(f"Time of day uses the {len(h)} of {len(d)} posts that have a posting hour."))
        if not blocks:
            return reply("I do not have enough dated posts to compare days or times yet.", notes=notes, intent="when")
        notes.append(info_note("Posting time is tangled up with campaign launches. Treat any pattern as something to test, not a rule."))
        notes.append(method_note(CHANCE_NOTE))
        return reply(" ".join(bits), blocks=blocks, notes=notes, evidence=self._evidence(d, m, " · ".join(x for x in pvals if x)), intent="when",
                     follow_ups=["Shorts vs long videos", "What should a brand post next?"])

    def h_hashtags(self, df, e, full, plat):
        m = choose_metric(df, e.metric)
        d = df[df["brand"].isin(e.brands)] if e.brands else df
        x = d.assign(tag=d["hashtags"].str.split("|")).explode("tag")
        x = x[x["tag"].fillna("") != ""]
        if x.empty:
            return reply("No hashtags appear in these posts, so there is nothing to rank.", intent="hashtags",
                         notes=[info_note("Hashtags come from captions and descriptions. Some brands do not use them.")])
        counts = x.groupby("tag").agg(n=("post_id", "size"), median=(m.col, "median"), age=("age_days", "median")).reset_index()
        used = counts.sort_values(["n", "tag"], ascending=[False, True]).head(8)
        notes = [warn(m.note)] if m.note else []
        self._mix(df, notes)
        blocks = [{"type": "bars", "title": "Most used hashtags (number of posts)", "unit": "",
                   "items": [{"label": r.tag, "value": float(r.n), "display": f"{int(r.n)}", "note": "", "highlight": i == 0, "flag": False} for i, r in enumerate(used.itertuples())], "note": None}]
        eligible = counts[(counts["n"] >= 3) & counts["median"].notna()].sort_values("median", ascending=False).head(8)
        text = f"{x['tag'].nunique()} different hashtags appear in {x['post_id'].nunique()} posts. Most used: " + ", ".join(f"{r.tag} ({int(r.n)})" for r in used.head(3).itertuples()) + "."
        if len(eligible):
            top = eligible.iloc[0]
            text += f" Among tags used at least 3 times, {top['tag']} has the highest median {m.label}: {m.fmt(top['median'])}."
            blocks.append(bars(f"Median {m.label} per post, tags used at least 3 times", self._bar_items(eligible, "tag", m, top=top["tag"])))
        notes.append(info_note("Many hashtags are just the brand's own name or campaign name, so they mostly identify the brand. Compare tags within one brand before drawing conclusions."))
        return reply(text, blocks=blocks, notes=notes, evidence=self._evidence(d, m), intent="hashtags",
                     follow_ups=["Which themes work best?", "Which brand performs best?"])

    def h_themes(self, df, e, full, plat):
        m = choose_metric(df, e.metric)
        d = df[df["brand"].isin(e.brands)] if e.brands else df
        d = d.dropna(subset=[m.col])
        text_l = d["text"].str.lower()
        members = {k: text_l.str.contains(rx).to_numpy() for k, rx in _THEME_RE.items()}
        rows = [(k, int(v.sum()), float(np.median(d[m.col].to_numpy()[v]))) for k, v in members.items() if v.sum() > 0]
        if not rows:
            return reply("None of the captions or titles match the themes I look for (sale, winter, summer and lawn, Eid, bridal, launch, campaign, stores).", intent="themes")
        g = pd.DataFrame(rows, columns=["theme", "n", "median"]).sort_values("median", ascending=False)
        g["age"] = [float(d.loc[members[t], "age_days"].median()) for t in g["theme"]]
        p = perm_p(d[m.col].to_numpy(dtype=float), {k: v for k, v in members.items() if v.sum() > 0}, (d["brand"] + "|" + d["platform"]).to_numpy())
        best = g.iloc[0]
        covered = np.any(np.vstack(list(members.values())), axis=0).mean()
        text = f"“{best['theme']}” posts have the highest median {m.label}: {m.fmt(best['median'])} across {int(best['n'])} posts. {chance_phrase(p, int(best['n']))}"
        notes = [warn(m.note)] if m.note else []
        self._mix(df, notes)
        notes.append(info_note(f"Themes come from simple keyword matching on the caption or title, not from reading each post. {covered:.0%} of posts matched at least one theme; a post can match several."))
        notes.append(method_note(CHANCE_NOTE))
        sm = self._small_note(g.rename(columns={"theme": "theme"}))
        if sm:
            notes.append(sm)
        return reply(text, blocks=[bars(f"Median {m.label} per post, by theme", self._bar_items(g, "theme", m, top=best["theme"]))],
                     notes=notes, evidence=self._evidence(d, m, f"chance test {fmt_p(p)}" if p is not None else ""), intent="themes",
                     follow_ups=["Most popular hashtags", "What should a brand post next?"])

    def h_brand_profile(self, df, e, full, plat):
        brand = e.brands[0]
        d = df[df["brand"] == brand]
        if d.empty:
            return reply(f"I have no {brand} posts{self._scope(plat=plat)}.", intent="brand_profile")
        m = choose_metric(d, e.metric)
        overall = df[m.col].median()
        mix = d["media_type"].value_counts()
        mix_txt = ", ".join(f"{MEDIA_NAMES.get(k, k)} {v}" for k, v in mix.items())
        mine = d[m.col].median()
        text = (f"{brand}: {len(d):,} posts{self._scope(plat=plat)}, posted {fmt_date(d['date'].min())} to {fmt_date(d['date'].max())}. "
                f"Median {m.label} per post is {m.fmt(mine)}, against {m.fmt(overall)} for all brands in this data.")
        t = d.dropna(subset=[m.col]).sort_values(m.col, ascending=False).head(3)
        notes = [warn(m.note)] if m.note else []
        if len(d) < MIN_GROUP:
            notes.append(warn(f"Only {len(d)} posts, so this profile is thin."))
        facts = [{"k": "Format mix", "v": mix_txt}, {"k": "Median " + m.label, "v": m.fmt(mine)},
                 {"k": "Typical post age", "v": "n/a" if d["age_days"].isna().all() else f"{d['age_days'].median():.0f} days"}]
        rows = [{"rank": str(i + 1), "post": {"text": clip(r.text), "href": r.url if isinstance(r.url, str) and r.url.startswith("http") else ""},
                 "date": fmt_date(r.date), "value": m.fmt(getattr(r, m.col))} for i, r in enumerate(t.itertuples())]
        return reply(text, blocks=[table([("k", "Fact", "left"), ("v", "Value", "left")], facts),
                                   table([("rank", "#", "right"), ("post", "Best posts", "left"), ("date", "Posted", "left"), ("value", m.label.capitalize(), "right")], rows)],
                     notes=notes, evidence=self._evidence(d, m), intent="brand_profile",
                     follow_ups=[f"Top 5 posts from {brand}", "Which brand performs best?"])

    def h_compare_brands(self, df, e, full, plat):
        a, b = e.brands[:2]
        m = choose_metric(df, e.metric)
        da, db = df[df["brand"] == a], df[df["brand"] == b]
        if da.empty or db.empty:
            return reply("One of those brands has no posts here.", intent="compare_brands")
        ma, mb = da[m.col].median(), db[m.col].median()
        hi, lo = (a, b) if ma >= mb else (b, a)
        ratio = max(ma, mb) / min(ma, mb) if min(ma, mb) else float("inf")
        text = f"{hi} has the higher median {m.label}{self._scope(plat=plat)}: {m.fmt(max(ma, mb))} against {m.fmt(min(ma, mb))} for {lo}" + (f" ({ratio:.1f} times as much)." if np.isfinite(ratio) else ".")
        notes = [warn(m.note)] if m.note else []
        ga = summarize(pd.concat([da, db]), "brand", m.col)
        x = age_note(ga)
        if x:
            notes.append(warn(x))
        for br, dd in ((a, da), (b, db)):
            if len(dd) < MIN_GROUP:
                notes.append(warn(f"{br} has only {len(dd)} posts."))
        cols = [("k", "", "left"), ("a", a, "right"), ("b", b, "right")]
        mix = lambda dd: ", ".join(f"{MEDIA_NAMES.get(k, k)} {v}" for k, v in dd["media_type"].value_counts().items())  # noqa: E731
        rows = [{"k": "Posts", "a": f"{len(da):,}", "b": f"{len(db):,}"},
                {"k": "Median " + m.label, "a": m.fmt(ma), "b": m.fmt(mb)},
                {"k": "Typical post age, days", "a": f"{da['age_days'].median():.0f}", "b": f"{db['age_days'].median():.0f}"},
                {"k": "Format mix", "a": mix(da), "b": mix(db)}]
        return reply(text, blocks=[table(cols, rows)], notes=notes, evidence=self._evidence(pd.concat([da, db]), m), intent="compare_brands",
                     follow_ups=[f"Tell me about {a}", f"Tell me about {b}"])

    def h_platform(self, df, e, full, plat):
        d = full
        plats = sorted(d["platform"].dropna().unique())
        if len(plats) < 2:
            return reply(f"Only {PLATFORM_NAMES.get(plats[0], plats[0])} data is loaded, so I cannot compare platforms yet.", intent="platform",
                         notes=[info_note("Collect the same brands on Instagram and Facebook (see COLLECTION_GUIDE.md and collect_instagram.py), then ask again.")],
                         follow_ups=["What data do you have?"])
        m = choose_metric(d, e.metric)
        g = summarize(d, "platform", m.col).sort_values("median", ascending=False)
        a = g.iloc[0]
        text = f"{PLATFORM_NAMES.get(a['platform'], a['platform'])} leads on {m.label}: median {m.fmt(a['median'])} per post across {int(a['n'])} posts."
        notes = [warn(m.note)] if m.note else []
        notes.append(info_note("Facebook 'likes' are total reactions and Instagram likes are hearts, so counts are not identical measures. Rates per follower are the fairer comparison."))
        shared = set.intersection(*[set(d[d.platform == p]["brand"]) for p in plats])
        if not shared:
            notes.append(warn("No brand appears on every platform here, so differences may reflect brands, not platforms."))
        return reply(text, blocks=[bars(f"Median {m.label} per post, by platform", self._bar_items(g, "platform", m, lambda k: PLATFORM_NAMES.get(k, k), a["platform"]))],
                     notes=notes, evidence=self._evidence(d, m), intent="platform", follow_ups=["Which brand performs best?"])

    def h_recommend(self, df, e, full, plat):
        m = choose_metric(df, e.metric)
        d = df.dropna(subset=[m.col])
        rows, used = [], []
        vals = d[m.col].to_numpy(dtype=float)

        def consider(key_label, col, order=None, names=lambda x: x, action=None, source=None):
            src = source if source is not None else d
            g, p = self._tested_groups(src, col, m, order)
            g = g[g["n"] >= MIN_FOR_FINDING].sort_values("median", ascending=False)
            if len(g) < 2:
                return
            a_, b_ = g.iloc[0], g.iloc[1]
            ratio = a_["median"] / b_["median"] if b_["median"] else float("inf")
            if p is None or p >= FINDING_P or ratio < 1.3:
                return
            rows.append({"f": f"{key_label}: {names(a_[col])} get a higher median {m.label} than {names(b_[col])} ({m.fmt(a_['median'])} vs {m.fmt(b_['median'])}).",
                         "e": f"{int(a_['n'])} vs {int(b_['n'])} posts, {fmt_p(p)}", "t": action(names(a_[col]), names(b_[col]))})
            used.append(key_label)

        consider("Format", "media_type", names=lambda k: MEDIA_NAMES.get(k, k),
                 action=lambda a_, b_: f"For four weeks, post a planned mix with more {a_.lower()} and fewer {b_.lower()}, and compare {m.label} per post.")
        consider("Weekday", "weekday_pkt", WEEKDAYS,
                 action=lambda a_, b_: f"Schedule similar posts on {a_} and on {b_} over several weeks and compare.")
        h = d.dropna(subset=["hour_pkt"]).copy()
        if len(h) >= MIN_FOR_FINDING * 2:
            h["daypart"] = h["hour_pkt"].astype(int).map({hr: lab for lab, rg in DAYPARTS for hr in rg})
            consider("Time of day", "daypart", [lab for lab, _ in DAYPARTS], source=h,
                     action=lambda a_, b_: f"Post the same kind of content in the {a_.lower()} slot and the {b_.lower()} slot, then compare.")
        text_l = d["text"].str.lower()
        members = {k: text_l.str.contains(rx).to_numpy() for k, rx in _THEME_RE.items()}
        members = {k: v for k, v in members.items() if v.sum() >= MIN_FOR_FINDING and (~v).sum() >= MIN_FOR_FINDING}
        if members:
            p = perm_p(vals, members, (d["brand"] + "|" + d["platform"]).to_numpy())
            best = max(members, key=lambda k: np.median(vals[members[k]]) - np.median(vals[~members[k]]))
            inn, out = np.median(vals[members[best]]), np.median(vals[~members[best]])
            if p is not None and p < FINDING_P and out > 0 and inn / out >= 1.3:
                rows.append({"f": f"Theme: “{best}” posts get a higher median {m.label} than the rest ({m.fmt(inn)} vs {m.fmt(out)}).",
                             "e": f"{int(members[best].sum())} vs {int((~members[best]).sum())} posts, {fmt_p(p)}",
                             "t": f"Make a few more “{best}” posts, keeping everything else similar, and compare."})
        notes = [warn(m.note)] if m.note else []
        self._mix(df, notes)
        notes.append(warn("Limitation: these are patterns in past posts, not proof of cause. They do not account for budget, topic or post age, and the keyword themes are rough."))
        notes.append(method_note(CHANCE_NOTE + f" Because several things are tested at once, a finding needs p < {FINDING_P}."))
        if not rows:
            text = (f"No pattern in {len(d):,} posts is strong enough to recommend yet. A pattern needs at least {MIN_FOR_FINDING} posts per group, "
                    "a clear gap, and a small chance of being luck.")
            notes.append(info_note("What would help: more posts per brand, likes and comments (not only views), follower counts, and both Instagram and Facebook."))
            return reply(text, notes=notes, evidence=self._evidence(d, m), intent="recommend", follow_ups=["What data do you have?", "Which brand performs best?"])
        text = f"The data supports {len(rows)} thing{'s' if len(rows) > 1 else ''} worth testing next. Each passed a strict chance test (p < {FINDING_P}); none is proven."
        return reply(text, blocks=[table([("f", "Finding", "left"), ("e", "Evidence", "left"), ("t", "What to test", "left")], rows)],
                     notes=notes, evidence=self._evidence(d, m), intent="recommend", follow_ups=["What are the limits of this data?", "Which brand performs best?"])

    def h_limits(self, df, e, full, plat):
        m = choose_metric(df, None)
        notes = []
        per = df.groupby("brand").size()
        pts = [f"Sample: {len(df):,} posts, between {per.min()} and {per.max()} per brand."]
        if per.min() < 30:
            pts.append("Some brands have few posts, so their medians are unstable.")
        if m.note:
            pts.append(m.note)
        if df["platform"].nunique() == 1:
            pts.append(f"Only {PLATFORM_NAMES.get(df['platform'].iloc[0])} is loaded. No cross-platform comparison is possible.")
        ages = df["age_days"].dropna()
        if len(ages):
            pts.append(f"Post age ranges from {ages.min():.0f} to {ages.max():.0f} days. Older posts have had longer to collect views and likes.")
        pts += ["Public data cannot show paid promotion, so a post that looks strong may have been boosted.",
                "No age, city or gender is available, so I cannot say which audience segment responds.",
                "Patterns here are associations in past posts, not proof of what causes engagement."]
        return reply("Here is what to keep in mind before trusting any answer from this data:",
                     blocks=[table([("p", "Limitation", "left")], [{"p": x} for x in pts])], notes=notes, evidence=self._evidence(df, m),
                     intent="limits", follow_ups=["What data do you have?", "What should a brand post next?"])

    def h_cannot(self, df, e, full, plat):
        sentiment = bool(re.search(r"sentiment|mood|emotion|opinion|feeling|comment text", e.q))
        if sentiment:
            text = "I have not scored sentiment. Comment text is not collected, only comment counts, so I cannot tell how audiences feel."
            notes = [info_note("The closest thing I can do is group posts by theme (sale, winter, Eid, bridal and so on) from the caption, and compare engagement.")]
        else:
            text = "Public data cannot tell us who responds. There is no age, city or gender, so an audience segment cannot be measured."
            notes = [info_note("The nearest honest proxy is the language of the caption, which shows who the brand is talking to, not who answers. I do not compute it yet.")]
        return reply(text, notes=notes, intent="cannot", follow_ups=["Which themes work best?", "What are the limits of this data?"])

    def h_unknown(self, df, e, full, plat):
        return reply("I did not understand that. I can answer questions about brands, formats, days and times, hashtags, themes and the best posts.",
                     blocks=[table([("s", "Try one of these", "left")], [{"s": s} for s in SUGGESTIONS[:6]])], intent="unknown",
                     follow_ups=["What can you do?"])
