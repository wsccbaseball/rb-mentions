# Rates & Barrels — mentions vs. career fWAR

Counts how often each player is named on the podcast, joins to career fWAR,
and surfaces the guys talked about a lot who've produced little.

## Run it
- **Full run (GitHub Action):** commit this repo, add `SUPABASE_URL` and
  `SUPABASE_SERVICE_KEY` as repo secrets, run `schema.sql` in Supabase once.
  The Action runs weekly (or hit "Run workflow" manually).
- **Smoke test locally:** `pip install -r requirements.txt` then
  `python pipeline.py --limit 15` (add `--no-fwar` to skip the FanGraphs join).

## Why an Action, not the phone
The scraper needs to reach podscripts.co and FanGraphs. Commit from the phone;
let the runner do the fetching. Output lands in Supabase + a CSV artifact.

## The chart
`rates_barrels_bubble.html` is standalone. Point it at Supabase by replacing the
`DATA` array with a fetch of `rb_player_mentions`.

## Accuracy notes
- Matching restricts to players active since 2015 (see `build_roster`) and uses
  fuzzy first-name matching to survive transcription errors ("Tarek Scoobal" ->
  Tarik Skubal). Genuine word swaps it can't infer ("Josh Young" -> Josh Jung)
  go in the `ALIASES` table — grow it as you spot misses.
- Bare-surname mentions are attributed only when one "present" player owns that
  surname in an episode. Ambiguous surnames (Marte, Rodriguez) are undercounted
  by design rather than misassigned.
- `career_fwar` is summed over the queried season span, and bat+pitch WAR are
  added for two-way players. Widen the range in `attach_fwar` for true career.
- It's a *fantasy* show: low fWAR + high mentions skews toward prospects,
  closers, and injury-return darts, not "busts." Read the residual accordingly.
