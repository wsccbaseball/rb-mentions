"""
Rates & Barrels — mention-frequency vs. career fWAR pipeline.

Flow:
  1. scrape_episode_urls()  -> list of podscripts episode URLs
  2. fetch_transcript(url)  -> clean transcript text
  3. build_roster()         -> {normalized name -> canonical name}, surname index
  4. count_mentions(...)    -> per-player mention counts across all episodes
  5. attach_fwar(...)       -> join career fWAR from the FanGraphs JSON API
  6. export / upsert        -> CSV + Supabase

Designed to run in an environment with open network (a GitHub Actions runner),
NOT from the phone editor — the phone commits the code, the Action runs it.

Env vars for the Supabase step:
  SUPABASE_URL, SUPABASE_SERVICE_KEY   (service key so the Action can upsert)

Install:  pip install -r requirements.txt
Run:      python pipeline.py            # full run
          python pipeline.py --limit 15 # smoke test on 15 episodes
"""

from __future__ import annotations
import argparse, csv, os, re, time, collections
from urllib.parse import urljoin
import requests
import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process
from unidecode import unidecode

BASE = "https://podscripts.co"
INDEX = f"{BASE}/podcasts/rates-barrels"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

# podscripts throttles bursts (HTTP 429). This session backs off and honors the
# server's Retry-After header instead of hammering.
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    retry = Retry(total=5, connect=3, read=3, backoff_factor=2.0,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET"]),
                  respect_retry_after_header=True)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

SESSION = _session()
PAGE_DELAY = 1.0        # between index pages
EPISODE_DELAY = 1.2     # between transcript fetches — polite enough to avoid 429

# Known audio-transcription errors fuzzy matching won't fix on its own.
# Map the mangled form -> the real player. Grow this as you spot misses.
ALIASES = {
    "josh young": "Josh Jung",          # hosts say "Young" for "Jung"
    "eno saris": None, "van ryper": None, "inosaris": None,  # hosts, not players
    "niv shah": None, "brian smith": None,
}

# People who show up as full names but are NOT players (writers, hosts, execs).
# Anything here is dropped even if it matches a historical player.
NON_PLAYERS = {
    "andrew baggarly", "ken rosenthal", "jen mccaffrey", "levi weaver",
    "david obrien", "zach meisel", "derek vanriper", "eno sarris",
    "buster posey", "farhan zaidi", "bob melvin", "alex anthopoulos",
    "dave roberts", "aj preller", "peter bendix", "mark kotsay",
    "craig breslow",
}

# Surnames that are also common English words, months, or verbs and survive the
# capitalization filter below. Count these ONLY via full-name matches, never as
# a bare surname (kills "in May" -> Trevor May).
SURNAME_STOP = {"may"}


# ------------------------------------------------------------------ scraping
def scrape_episode_urls(limit: int | None = None) -> list[str]:
    """Walk the paginated index and collect every episode URL (absolute)."""
    seen, out, page = set(), [], 1
    while True:
        r = SESSION.get(f"{INDEX}?page={page}", timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        hrefs = [urljoin(BASE, a["href"]) for a in soup.select("h3 a[href]")
                 if "/podcasts/rates-barrels/" in a["href"]]
        hrefs = [h for h in hrefs if h.rstrip("/") != INDEX]
        new = 0
        for h in hrefs:
            if h not in seen:
                seen.add(h); out.append(h); new += 1
        if new == 0:                 # no fresh links -> past the last page
            break
        if limit and len(out) >= limit:
            return out[:limit]
        if page > 60:
            break
        page += 1
        time.sleep(PAGE_DELAY)      # be polite
    return out[:limit] if limit else out


AD_MARKERS = ("instacart", "watercolor westport", "rakuten", "logan & cove",
              "logan and cove", "toyota", "audible", "nyt cooking", "megaphone.fm")

def fetch_transcript(url: str) -> str:
    """Return the transcript body, ad reads and timestamp markers stripped.

    The transcript is reliably bracketed by 'Starting point is HH:MM:SS' markers,
    so we slice on those rather than on a 'Transcript' heading — and we drop
    <script>/<style> first, because podscripts ships a 'There aren't comments yet'
    string inside an early script that would otherwise truncate the body.
    """
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text("\n")

    starts = [m.start() for m in
              re.finditer(r"Starting point is \d{2}:\d{2}:\d{2}", text)]
    if starts:
        body = text[starts[0]:]
        body = re.split(r"There aren.t comments yet|©\s*PodScripts", body)[0]
    else:  # fallback if the timestamp format ever changes
        m = re.search(r"\bTranscript\b(.*?)(?:There aren.t comments yet|©\s*PodScripts)",
                      text, re.S)
        body = m.group(1) if m else text

    body = re.sub(r"Starting point is \d{2}:\d{2}:\d{2}", " ", body)
    lines = [ln for ln in body.splitlines()
             if not any(a in ln.lower() for a in AD_MARKERS)]
    return " ".join(lines)


# -------------------------------------------------------------------- roster
REGISTER = ("https://raw.githubusercontent.com/chadwickbureau/"
            "register/master/data/people-{shard}.csv")
SHARDS = list("0123456789abcdef")   # register is split across 16 files

def _norm(s: str) -> str:
    return re.sub(r"[^a-z ]", "", unidecode(str(s)).lower()).strip()

def build_roster(active_since: int = 2015):
    """Candidate set of recent MLB players from the Chadwick register (the same
    source pybaseball uses). Filtering on mlb_played_last keeps anyone who has
    reached the majors since `active_since`, including 2025/2026 debuts. Returns:
       full_exact:  {normalized 'first last' -> canonical name}
       last_index:  {normalized last -> [(norm_first, canonical), ...]}
    """
    frames = []
    for s in SHARDS:
        frames.append(pd.read_csv(
            REGISTER.format(shard=s),
            usecols=["name_first", "name_last", "mlb_played_last"],
            dtype=str, low_memory=False))
    reg = pd.concat(frames, ignore_index=True).dropna(
        subset=["name_first", "name_last", "mlb_played_last"])
    reg["yr"] = pd.to_numeric(reg["mlb_played_last"], errors="coerce")
    reg = reg[reg["yr"] >= active_since]

    full_exact, last_index = {}, collections.defaultdict(list)
    for first, last in zip(reg["name_first"], reg["name_last"]):
        first, last = str(first).strip(), str(last).strip()
        nf, nl = _norm(first), _norm(last)
        if not nf or not nl:
            continue
        canonical = f"{first} {last}"
        if f"{nf} {nl}" in NON_PLAYERS:
            continue
        full_exact[f"{nf} {nl}"] = canonical
        last_index[nl].append((nf, canonical))
    return full_exact, last_index


# ------------------------------------------------------------------ matching
WORD = re.compile(r"[A-Za-z][A-Za-z.'-]+")

def match_episode(text: str, full_exact, last_index, fuzz_cut=87):
    """Count player mentions in one transcript.

    Pass 1: full-name bigrams (exact, then fuzzy on the first name for a known
            surname) -> establishes which players are 'present' this episode and
            marks the tokens they consumed.
    Pass 2: bare surnames, counted only when (a) that surname maps to exactly one
            present player, (b) the token is Capitalized in the source (so 'young'
            the adjective doesn't become Brandon Young), (c) the surname isn't a
            month/verb collision, and (d) the token was not already consumed by a
            full-name hit (so 'Taylor' in 'Taylor Ward' doesn't feed Grant Taylor).
    """
    raw = WORD.findall(text)                       # case preserved
    tokens = [unidecode(w).lower().strip(".'-") for w in raw]
    counts = collections.Counter()
    present, consumed = {}, set()

    # aliases first (mangled full names)
    joined = " ".join(tokens)
    for bad, good in ALIASES.items():
        if good and bad in joined:
            counts[good] += joined.count(bad)
            present[_norm(good.split()[-1])] = good

    # pass 1: full-name bigrams
    for i in range(len(tokens) - 1):
        first, last = tokens[i], tokens[i + 1]
        key = f"{first} {last}"
        if key in NON_PLAYERS:
            continue
        if key in full_exact:                       # exact
            canon = full_exact[key]
        elif last in last_index:                    # fuzzy on first name
            cand = process.extractOne(
                first, [f for f, _ in last_index[last]],
                scorer=fuzz.ratio, score_cutoff=fuzz_cut)
            canon = dict((f, c) for f, c in last_index[last])[cand[0]] if cand else None
        else:
            canon = None
        if canon and _norm(canon) not in NON_PLAYERS:
            counts[canon] += 1
            present[_norm(canon.split()[-1])] = canon
            consumed.add(i); consumed.add(i + 1)

    # pass 2: bare surnames for present players
    unique = collections.Counter(_norm(c.split()[-1]) for c in present.values())
    for i, tok in enumerate(tokens):
        if i in consumed or tok in SURNAME_STOP:
            continue
        if tok in present and unique[tok] == 1 and raw[i][:1].isupper():
            counts[present[tok]] += 1
    return counts


def count_mentions(urls, full_exact, last_index):
    total = collections.Counter()
    per_ep = {}
    for j, url in enumerate(urls, 1):
        try:
            txt = fetch_transcript(url)
        except Exception as e:
            print(f"  ! skip {url}: {e}"); continue
        if len(txt) < 500:
            print(f"  ! short transcript ({len(txt)} chars), check parsing: {url}")
        c = match_episode(txt, full_exact, last_index)
        per_ep[url] = c
        total.update(c)
        if j % 10 == 0:
            print(f"  ...{j}/{len(urls)} episodes")
        time.sleep(EPISODE_DELAY)
    print(f"  {len(total)} distinct players; top 10: "
          + ", ".join(f"{n}({m})" for n, m in total.most_common(10)))
    return total, per_ep


# ---------------------------------------------------------------------- fWAR
FG_API = "https://www.fangraphs.com/api/leaders/major-league/data"

def _fg_war(stats: str, start: int, end: int) -> dict:
    """Pull career-over-span WAR for one stat group ('bat' or 'pit') from the
    current FanGraphs JSON API (the old pybaseball endpoint 403s now)."""
    params = {
        "pos": "all", "stats": stats, "lg": "all", "qual": "0",
        "season": end, "season1": start, "ind": "0",
        "pageitems": "200000", "pagenum": "1", "type": "8",
        "month": "0", "team": "0", "rost": "0",
        "sortstat": "WAR", "sortdir": "default",
    }
    r = SESSION.get(FG_API, params=params,
                    headers={"Accept": "application/json"}, timeout=90)
    r.raise_for_status()
    payload = r.json()
    rows = payload.get("data", payload) if isinstance(payload, dict) else payload
    out, saw_war = {}, False
    for row in rows:
        name = row.get("PlayerName") or row.get("Name") or ""
        name = re.sub(r"<[^>]+>", "", str(name)).strip()   # strip any HTML anchor
        war = row.get("WAR")
        if war is None:
            continue
        saw_war = True
        if name:
            out[_norm(name)] = out.get(_norm(name), 0.0) + float(war)
    if not saw_war:
        raise ValueError(f"FanGraphs {stats} response had no WAR field")
    return out

def attach_fwar(counts, start=2015, end=2025):
    """Career-over-span fWAR from the FanGraphs API. Two-way players get their
    batting and pitching WAR summed."""
    war = collections.defaultdict(float)
    for stats in ("bat", "pit"):
        for k, v in _fg_war(stats, start, end).items():
            war[k] += v
    out = []
    for name, m in counts.most_common():
        val = war.get(_norm(name))
        out.append({"player": name, "mentions": m,
                    "career_fwar": round(val, 1) if val is not None else None})
    return out


# -------------------------------------------------------------- export/store
def write_csv(rows, path="mentions_vs_fwar.csv"):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["player", "mentions", "career_fwar"])
        w.writeheader(); w.writerows(rows)
    print(f"wrote {path} ({len(rows)} players)")

def upsert_supabase(rows):
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
    if not (url and key):
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — skipping upsert")
        return
    from supabase import create_client
    sb = create_client(url, key)
    sb.table("rb_player_mentions").upsert(rows, on_conflict="player").execute()
    print(f"upserted {len(rows)} rows to Supabase")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="cap episodes (smoke test)")
    ap.add_argument("--no-fwar", action="store_true", help="skip FanGraphs join")
    args = ap.parse_args()

    print("scraping episode list...")
    urls = scrape_episode_urls(limit=args.limit)
    print(f"  {len(urls)} episodes")

    print("building roster...")
    full_exact, last_index = build_roster()
    print(f"  {len(full_exact)} candidate players")

    print("counting mentions...")
    counts, _ = count_mentions(urls, full_exact, last_index)

    if args.no_fwar:
        rows = [{"player": n, "mentions": m, "career_fwar": None}
                for n, m in counts.most_common()]
    else:
        try:
            rows = attach_fwar(counts)
        except Exception as e:
            print(f"  ! fWAR join failed ({e}); saving mentions without fWAR")
            rows = [{"player": n, "mentions": m, "career_fwar": None}
                    for n, m in counts.most_common()]

    write_csv(rows)
    upsert_supabase(rows)


if __name__ == "__main__":
    main()
