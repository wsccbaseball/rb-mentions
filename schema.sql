-- Supabase table for podcast mention counts joined to career fWAR.
-- Run once in the Supabase SQL editor.
create table if not exists rb_player_mentions (
  player       text primary key,
  mentions     int  not null,
  career_fwar  real,
  updated_at   timestamptz default now()
);

-- The "your question" view: talked about a lot, produced little.
-- Ordered by how over-discussed they are relative to production.
create or replace view rb_over_discussed as
select player, mentions, career_fwar,
       mentions - (2.5 + 0.12 * coalesce(career_fwar,0)) as mention_residual
from rb_player_mentions
where career_fwar <= 5 and mentions >= 4
order by mention_residual desc;
