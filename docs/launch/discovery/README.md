# One-week customer discovery

`docs/STRATEGY.md` leaves the paying customer unknown. This week is meant to name one segment cheaply: no ads, no
spend, roughly an hour a day alongside the local proof work (H2).

| file | what |
|---|---|
| `posts.md` | three post drafts: r/LocalLLaMA, Show HN, r/developersIndia |
| `interview_script.md` | a 10-minute interview script, the "serious conversation" test, and a one-page note template |
| `tracking.csv` | one row per sign-up batch, conversation and interview (template; fill locally) |
| `notes/` | interview notes. Git ignores everything in it except `.gitignore`, because the notes hold personal details |

## Candidate segments (fixed before any data)

The waitlist on the home page (`/#waitlist`) tags every sign-up with one of these. The tag is derived only from the
two required answers (`site/functions/api/request-access.js`, `segmentFor`):

| segment | rule | hypothesis from STRATEGY.md |
|---|---|---|
| `privacy_team` | why = privacy, role = team_dev or founder | privacy-bound dev teams (code can't leave the machine) |
| `privacy_individual` | why = privacy, other roles | privacy-minded individuals; counted with `privacy_team` for the decision but reported separately |
| `cost_sensitive` | why = cost | cost-sensitive individual developers (e.g. India) |
| `local_builder` | why = offline, speed or curiosity | builders on open or local models |
| `unclassified` | why = other | read the free text and re-tag by hand in `tracking.csv` only if it is unambiguous |

## Plan

| day | do | done when |
|---|---|---|
| 0 (before) | Owner approves the deploy. Check that `/#waitlist` stores a test sign-up, then delete it from KV. Read `posts.md` again against the evidence rules. | a test row shows up in `?view=waitlist` |
| 1 | Post on r/LocalLLaMA (`?src=localllama`). Reply to every comment within 12 h. Ask 3 commenters for a 10-minute call. | post live, 3 asks sent |
| 2 | Post Show HN (`?src=hn`) on a weekday morning US time. Same reply rule. Ask 3 for calls. | post live, 3 asks sent |
| 3 | Post on r/developersIndia (`?src=devindia`), following the subreddit's self-promotion rules. Ask 3 for calls. | post live, 3 asks sent |
| 2–6 | Interviews: aim for **8–10**, at least 2 per segment where possible. Write notes within 15 minutes. One row per interview in `tracking.csv`. | ≥ 8 notes |
| 4 | Mid-week check: pull `?view=waitlist`, log counts per segment and source in `tracking.csv`. Don't change the posts or the form mid-week (it would mix the samples). | counts logged |
| 7 | Pull final counts, score each segment with the decision rule, and write the result into `docs/STRATEGY.md` under "Customer". | segment named, or "no decision" with the reason |

Admin reads (the token comes from your environment; never paste it anywhere):

```
curl -s "https://awos.tirramind.com/api/request-access?token=$ADMIN_TOKEN&view=waitlist" | python3 -m json.tool
curl -s -o waitlist.csv "https://awos.tirramind.com/api/request-access?token=$ADMIN_TOKEN&view=waitlist&format=csv"
```

## Decision rule (fixed now, applied on day 7)

For each segment (`privacy_team` + `privacy_individual` together as **privacy**, then `cost_sensitive` and
`local_builder`):

```
score = waitlist sign-ups + 3 × serious conversations
```

A "serious conversation" is an interview, or a written exchange, that meets at least 2 of the 5 signals in
`interview_script.md`. One serious conversation is weighted as three sign-ups because it costs the person far more
than a two-question form.

- **The segment with the highest score becomes "the customer"** for the next build cycle: the landing copy, the
  first paid offer and the next proof runs target it.
- **It needs at least 3 serious conversations.** If no segment has 3, the result is "no decision": run a second
  week with the same rule, aimed at the two highest scorers.
- **Tie (within 10%):** pick the segment whose interviewees named a price, or already pay for a tool. If that is
  still tied, pick the one with more referrals.
- **Do not rescue a segment with anecdotes.** One enthusiastic person doesn't outweigh the rule. Write the
  exception down instead.

Also record (not part of the score): the median "first number they said" for price, the share of sign-ups that left
an email, and the top 3 blockers people named. These shape the offer, not the choice.
