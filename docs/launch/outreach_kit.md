# AWOS early access: outreach kit

The goal for the next 4 weeks is **10 people who run AWOS on their own repo, and 3 who use it every week.** Revenue comes after that. Activity counts for nothing here; a real user running a real job is the only measure.

## Who to contact (in order)

1. **Open-source maintainers with a backlog of small, well-defined issues.** They have public repos, real tests, and more issues than time. They are the easiest to show value to.
2. **Indie SaaS founders and 2–10 person dev teams.** Their backlog of small bugs never gets done, and they are price-sensitive and used to trying tools.
3. **Agencies and freelancers.** Billable time spent on small fixes is margin they lose.

## The strongest move: show, don't pitch (the concierge demo)

For an open-source maintainer:

1. Pick **one** open issue in their repo that is well-defined and has tests.
2. Run AWOS on it locally.
3. **If the branch passes their tests and you have read the diff yourself,** message them with the result. Don't open an unsolicited pull request unless their CONTRIBUTING guide welcomes them.
4. **Send at most one demo per maintainer,** and only work you would be proud of. Never send unreviewed AI output to a maintainer.

> Hi {name}, I'm building AWOS, a background coding agent that works on small tickets and hands back a branch with the tests run. I tried it on #{issue} ({title}) in {repo}. It produced a fix that passes your test suite ({n} tests), and I've reviewed the diff: {link to gist/branch}. If it's useful, I'm happy to open a PR the normal way. If you'd like to try AWOS on your own backlog, it's free during early access: {site}/access/

## Message templates

**Founder or team lead (short):**

> Hi {name}, quick one: I'm building AWOS, an agent that works through small coding tickets in the background and gives you review branches with test results, usually for cents per ticket, and it never pushes anything. I'm onboarding a few teams personally. Would you be up for handing it 3 tickets from your backlog this week? I'll set it up with you. {site}

**Follow-up (once, after 4–5 days):**

> Following up once: if small tickets pile up on your side, I can run 3 of them through AWOS for you and send the branches, no setup on your end. If not relevant, no worries.

**Community post (Show HN, r/programming-style, X):** lead with one concrete result, not the vision.

> Show HN: AWOS – queue small coding tickets, get review branches with test results. On 73 real issues from 26 open-source Python projects it solved 72 for about $0.007 each (one run each, Claude Haiku 5.5). It runs on your machine, works in a separate git worktree per job, never pushes, and can use a local model for $0. Looking for early users with a backlog of small tickets.

## Where to find people

- **GitHub:** repos with 50+ open issues labelled `bug` or `good first issue`, active in the last month, with a test suite. Search with `gh search issues "label:bug state:open" --language python`.
- **Hacker News:** "Who wants to be hired / Who is hiring" threads, Show HN comments on coding-agent launches (people who said they want unattended agents).
- **X:** indie hackers posting about backlog or "too many small bugs"; replies on Claude Code and Codex threads asking for background or overnight runs.
- **Indie Hackers, dev Discords:** product channels where founders discuss tooling.

## Weekly targets

| Week | Messages sent | Conversations | Installed | Ran ≥3 jobs |
|---|---|---|---|---|
| 1 | 30 | 5 | 2 | 1 |
| 2 | 30 | 6 | 4 | 2 |
| 3 | 30 | 6 | 7 | 4 |
| 4 | 30 | 6 | 10 | 6 |

**Stop and rethink** if fewer than 3 people have installed it by the end of week 2. Change the offer or the audience, not the volume.

## Tracking (keep it in one sheet)

`date · name · segment · channel · message sent · replied? · call? · installed? · jobs run · what they handed off · what broke · quote`

What people say ("I'd pay for…", "this broke when…") is the most valuable thing you'll collect. Paste their words verbatim.

## What to watch in their usage

- **Activation:** their first job reaches `done` with tests passed.
- **Retention:** jobs per week, and the share they merge.
- **Failure modes:** which ticket types fail, and where verification wrongly says done. Fix those first.
- **Willingness to pay:** after 2 weeks of use, ask "if this cost $X/month, would you keep it?" with real numbers ($10 / $25 / $50 per seat).
