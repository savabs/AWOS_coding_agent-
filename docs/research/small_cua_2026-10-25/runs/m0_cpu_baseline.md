# M0 attempt 1: Online-Mind2Web dev (100 tasks) on GitHub CPU runners (2026-10-25, run 40209008622)

**Not a valid score. It measured the setup, not the models.** Not judged (no WebJudge run).

| | base 2B | base 4B | run A final (4B) |
|---|---|---|---|
| blocked by the site (bot wall / Cloudflare / captcha / load failed / 403 / 404) | 33 | 34 | 31 |
| hit the 900 s wall-clock cap | 9 | 25 | 30 |
| stopped at grounding (model picked a control the reader could not map) | 6 | 16 | 18 |
| invalid replies | 33 | 3 | 3 |
| product guard stops (sign-in, payment...) | 9 | 6 | 10 |
| agent said done (upper bound on success) | 1 | 8 | 6 |
| steps per episode | 4.0 | 2.7 | 3.7 |
| seconds per model step | 46 | 148 | 107 |

## Causes and fixes
1. **Speed.** About 2.5 min per 4B step on 4 vCPU (prompts of about 2.7k tokens, 2 parallel slots). Real tasks need
   10-20 steps, so the time cap ended most episodes after 2-4 steps. This is the same trap as baseline amendment A1.
   **Fix:** run on Kaggle GPU (`claude/web-kaggle`).
2. **Blocking.** About 1/3 of sites block datacenter IPs: mayoclinic, cars.com, apartments.com, accuweather, uniqlo, and
   google (captcha). These are reported and never bypassed. Kaggle is a datacenter too.
   **Fix:** report the score on all tasks and on reachable tasks. Optionally, compare on a residential IP.
3. **The reader misses things on real pages:**
   - links inside headings;
   - role=option / menuitem / gridcell elements;
   - deep pages (CBOR);
   - unnamed and ambiguous controls;
   - no re-observe after fill.

   **Fix:** `claude/web-reader`.
4. **Base 2B produces invalid replies one time in three on real pages.** It is out of the main race (still fine for
   repair / verify).
