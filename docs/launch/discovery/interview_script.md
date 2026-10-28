# 10-minute customer interview

The goal is to learn what the person does today and what it costs them. It is not a pitch: don't demo or describe
AWOS until the last two minutes, and only if they ask. Ask about past behaviour ("last time…"), not opinions about
the future ("would you…"). Let silences run. Take notes in the template below while it's fresh, within 15 minutes.

**Opening (30 s):** "Thanks for the time. I'm trying to understand how developers use, or avoid, AI coding tools.
There are no right answers, and I'm not selling anything today. Is it OK if I take notes?"

## Questions (pick 5–7, in this order)

1. **Current tools.** "Walk me through the last coding task where you used an AI tool. Which tool was it, and what
   happened?" *(If none: "What did you use instead?")*
2. **Cloud: why or why not.** "Does your code go to a cloud AI service today? Who decided that, and why?"
3. **Blockers.** "What stops you, or your team, from using AI agents more than you do now? Think of the last time
   it got in the way."
4. **The offline moment.** "When did you last want an agent to work while offline, or without sending code
   anywhere? What were you doing, and what did you do instead?"
5. **Money.** "What do you or your company pay for AI coding tools today, per month? Who approves that spend?"
6. **Willingness to pay.** "If a private agent ran on your own laptop and fixed routine, well-tested issues
   reliably, what would that be worth per month, and who would pay?" *(Then stay quiet. Write down the first
   number they say.)*
7. **Hardware.** "What machine do you code on (RAM, GPU)? Have you run a local model on it?"

**Close (1 min):** "Who else should I talk to who feels this more strongly than you?" Then: "Can I send you the
local results when they're out?" (Only now, if asked, show the site.)

## Signals that count as a "serious conversation"

Count an interview as serious only if at least **two** of these are true:
- They described a specific recent time the problem hurt (a date, a project, a rule).
- They pay for a tool today, or can name who approves tool spend.
- They named a price, or agreed to a follow-up, without being pushed.
- They introduced you to someone else.
- They offered to try it on a real repo.

---

## One-page note template

Copy this for each interview into `docs/launch/discovery/notes/<YYYY-MM-DD>_<initials>.md`. Git ignores that
folder, so the notes stay on your machine. Only the anonymised row in `tracking.csv` is shared.

```
# Interview <YYYY-MM-DD> · <initials>
Source: hn / localllama / devindia / x / intro / other
Role: solo_dev / team_dev / founder / researcher / other      Company size:
Country:                                                        Machine (RAM/GPU):
Segment guess: privacy_team / privacy_individual / cost_sensitive / local_builder / unclassified

## What they do today (tools, who pays, how much)
-

## Cloud AI: allowed? why or why not (their words)
-

## The last time it hurt (specific story)
-

## Blockers to using agents more
-

## Money: pays today / would pay / who approves
- First number they said:

## Offline / private moment
-

## Serious? (tick ≥2)
[ ] specific recent pain  [ ] pays today / knows approver  [ ] named a price or follow-up
[ ] referred someone       [ ] offered a real repo
Serious: yes / no

## Surprises / quotes worth keeping (verbatim)
-

## Follow-up promised
-
```
