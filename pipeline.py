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
import argparse, csv, io, os, re, time, collections
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
    "keston hura": "Keston Hiura",       # transcription drops the 'i' in Hiura
    "keston hira": "Keston Hiura",
    "corbin burns": "Corbin Burnes",   # "Burns" alone is ambiguous with Chase Burns
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
    "trevor may",   # retired; 'Trevor' (guest) + 'may' (verb) forms false bigrams
}

# Surnames that are also common English words, months, or verbs and survive the
# capitalization filter below. Count these ONLY via full-name matches, never as
# a bare surname (kills "in May" -> Trevor May).
SURNAME_STOP = {"may"}

# Unambiguous star surnames that the show almost always uses bare, so the
# per-episode "present" rule undercounts them (or misses them when the roster
# lags / the name is mistranscribed). Each key here is checked to have exactly
# one prominent bearer and to NOT collide with a common word or first name, so
# it's safe to always credit. Includes known mistranscriptions (scoobal=Skubal).
SURNAME_SOLO = {
    "wheeler": "Zack Wheeler",
    "ohtani": "Shohei Ohtani", "otani": "Shohei Ohtani",
    "semien": "Marcus Semien",
    "adames": "Willy Adames",
    "skubal": "Tarik Skubal", "scoobal": "Tarik Skubal",
    "skenes": "Paul Skenes", "skeens": "Paul Skenes", "skeans": "Paul Skenes",
    "burnes": "Corbin Burnes",
    "gallen": "Zac Gallen",
    "misiorowski": "Jacob Misiorowski", "mizorowski": "Jacob Misiorowski",
    "mizaraski": "Jacob Misiorowski",
}


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

def scan_episode(text: str, full_exact, last_index, fuzz_cut=87):
    """Scan one transcript. Returns:
      fc      : Counter of full-name (and alias) mention counts
      present : {surname_norm -> set(canonicals)} full-named in THIS episode
      bares   : Counter of {surname_norm -> capitalized bare-surname occurrences}
                not consumed by a full-name hit (resolved in count_mentions)
    """
    raw = WORD.findall(text)                       # case preserved
    tokens = [unidecode(w).lower().strip(".'-") for w in raw]
    fc = collections.Counter()
    present = collections.defaultdict(set)
    consumed = set()

    joined = " ".join(tokens)
    for bad, good in ALIASES.items():
        if good and bad in joined:
            fc[good] += joined.count(bad)
            present[_norm(good.split()[-1])].add(good)

    for i in range(len(tokens) - 1):
        first, last = tokens[i], tokens[i + 1]
        key = f"{first} {last}"
        if key in NON_PLAYERS:
            continue
        if key in full_exact:
            canon = full_exact[key]
        elif last in last_index:
            cand = process.extractOne(
                first, [f for f, _ in last_index[last]],
                scorer=fuzz.ratio, score_cutoff=fuzz_cut)
            canon = dict((f, c) for f, c in last_index[last])[cand[0]] if cand else None
        else:
            canon = None
        if canon and _norm(canon) not in NON_PLAYERS:
            fc[canon] += 1
            present[_norm(canon.split()[-1])].add(canon)
            consumed.add(i); consumed.add(i + 1)

    bares = collections.Counter()
    for i, tok in enumerate(tokens):
        if i in consumed or tok in SURNAME_STOP:
            continue
        if raw[i][:1].isupper() and (tok in last_index or tok in SURNAME_SOLO):
            bares[tok] += 1
    return fc, present, bares


def count_mentions(urls, full_exact, last_index):
    total = collections.Counter()
    for j, url in enumerate(urls, 1):
        try:
            txt = fetch_transcript(url)
        except Exception as e:
            print(f"  ! skip {url}: {e}"); continue
        if len(txt) < 500:
            print(f"  ! short transcript ({len(txt)} chars), check parsing: {url}")
        fc, present, bares = scan_episode(txt, full_exact, last_index)
        total.update(fc)
        # resolve bare surnames: vetted solo stars always count; otherwise only
        # when exactly one full-named player owns that surname THIS episode.
        for s, n in bares.items():
            if s in SURNAME_SOLO:
                total[SURNAME_SOLO[s]] += n
            elif len(present.get(s, ())) == 1:
                total[next(iter(present[s]))] += n
        if j % 10 == 0:
            print(f"  ...{j}/{len(urls)} episodes")
        time.sleep(EPISODE_DELAY)
    print(f"  {len(total)} distinct players; top 10: "
          + ", ".join(f"{n}({m})" for n, m in total.most_common(10)))
    return total, None


# ---------------------------------------------------------------------- fWAR
# FanGraphs blocks the Actions runner IP (403), so career WAR comes from
# Baseball-Reference's public bulk files instead. This is bWAR, not fWAR, but the
# two track closely; the column stays named career_fwar for schema compatibility.
BREF = {
    "bat": "https://www.baseball-reference.com/data/war_daily_bat.txt",
    "pit": "https://www.baseball-reference.com/data/war_daily_pitch.txt",
}

def _bref_war(url: str) -> dict:
    r = SESSION.get(url, timeout=120)
    r.raise_for_status()
    # requests defaults to latin-1 for text/plain, which mojibakes accented
    # names (Tatís, Ramírez, Acuña) and drops them from the join. Decode the
    # raw bytes as UTF-8 instead.
    df = pd.read_csv(io.BytesIO(r.content), encoding="utf-8",
                     encoding_errors="replace", low_memory=False)
    if "WAR" not in df.columns or "name_common" not in df.columns:
        raise ValueError(f"unexpected columns from {url}: {list(df.columns)[:6]}")
    df["WAR"] = pd.to_numeric(df["WAR"], errors="coerce").fillna(0.0)
    out = collections.defaultdict(float)
    for name, w in zip(df["name_common"], df["WAR"]):   # career = sum all seasons
        out[_norm(name)] += float(w)
    return out

def attach_fwar(counts, *_ignored):
    """Career WAR per player from Baseball-Reference (bat + pitch summed, so
    two-way players are handled). Joined to mention counts by normalized name."""
    war = collections.defaultdict(float)
    for url in BREF.values():
        for k, v in _bref_war(url).items():
            war[k] += v

    # Baseball-Reference suffixes names ("Luis Robert Jr.", "Michael Harris II")
    # but the register / mention side usually doesn't. Add a suffix-stripped
    # alias ONLY when it's unambiguous, so we never merge a Jr with his Sr.
    suffix = re.compile(r"\s+(jr|sr|ii|iii|iv|v)$")
    groups = collections.defaultdict(list)
    for k in list(war):
        groups[suffix.sub("", k)].append(k)
    for stripped, keys in groups.items():
        if stripped not in war and len(keys) == 1:
            war[stripped] = war[keys[0]]
    # explicit father/son collisions where stripping is ambiguous
    for mention_norm, bref_norm in {"fernando tatis": "fernando tatis jr"}.items():
        if bref_norm in war:
            war[mention_norm] = war[bref_norm]

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

def _supabase():
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
    if not (url and key):
        return None
    from supabase import create_client
    return create_client(url, key)

def upsert_supabase(rows):
    sb = _supabase()
    if sb is None:
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — skipping upsert")
        return
    sb.table("rb_player_mentions").upsert(rows, on_conflict="player").execute()
    print(f"upserted {len(rows)} rows to Supabase")

def load_counts_from_supabase():
    """Read the existing player/mention rows back out, paging past the 1000-row
    PostgREST cap, so we can recompute WAR without re-scraping."""
    sb = _supabase()
    if sb is None:
        raise RuntimeError("SUPABASE creds not set; --fwar-only needs them")
    counts, page = collections.Counter(), 0
    while True:
        res = sb.table("rb_player_mentions").select("player,mentions") \
                .range(page * 1000, page * 1000 + 999).execute()
        for row in res.data:
            counts[row["player"]] = row["mentions"]
        if len(res.data) < 1000:
            break
        page += 1
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="cap episodes (smoke test)")
    ap.add_argument("--no-fwar", action="store_true", help="skip the WAR join")
    ap.add_argument("--fwar-only", action="store_true",
                    help="skip scraping; recompute WAR for players already in Supabase")
    args = ap.parse_args()

    if args.fwar_only:
        print("loading players from Supabase...")
        counts = load_counts_from_supabase()
        print(f"  {len(counts)} players")
        print("joining WAR...")
        rows = attach_fwar(counts)
        matched = sum(1 for r in rows if r["career_fwar"] is not None)
        print(f"  WAR matched {matched}/{len(rows)} players")
        write_csv(rows)
        upsert_supabase(rows)
        return

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
    if args.limit:
        print("test run (--limit): skipping Supabase upsert to protect the live table")
    else:
        upsert_supabase(rows)


if __name__ == "__main__":
    main()
