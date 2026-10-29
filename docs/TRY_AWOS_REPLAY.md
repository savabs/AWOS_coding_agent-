# Try AWOS Replay on real chores (15 minutes)

AWOS Replay is a Chrome extension: **show a web chore once, then replay it whenever you want.**
- When the site has changed, a small model on your own Mac finds the moved button, or stops and says it's gone.
- It never pays, sends, deletes or submits without your click.
- Nothing leaves your machine: the model runs on 127.0.0.1, and recordings stay in Chrome's local storage.

> Status: MVP. It works on our test pages (automated tests on local pages). These are its first real sites, and the point of this
> round is to find where it breaks. Use a **separate Chrome profile**, and start with low-risk chores.

## 1. Install (once)
1. Install the local models: `bash scripts/install_replay.sh --agent 4b`. On a Mac with 16 GB or more you can use
   `--agent 9b` instead (stronger, 5.7 GB). It installs llama.cpp with Homebrew if it is missing, downloads the models
   (sha256-checked), and starts two low-priority background servers on this Mac only:
   - `127.0.0.1:8080`: the 2B repair / verify model for Replay (1.3 GB).
   - `127.0.0.1:8081`: the Tell-it agent model (Qwen3.5-4B, 2.7 GB, or the 9B).

   Each server unloads its model after 5 idle minutes. **Tell it needs `--agent`.** The 2B cannot drive a page by
   itself. `--agent none` removes only the agent server. `AWOS_DRY_RUN=1` shows every step without doing it.
   Details: `docs/launch/replay_install.md`.
2. Check it with `bash scripts/install_replay.sh --status` or `bash scripts/awos_model.sh --check`. The second also
   checks that 8081 serves an agent model and not the 2B.
   (Without the installer: `brew install llama.cpp` and `bash scripts/awos_model.sh` in a terminal, which serves the
   2B only.)
3. Chrome: open `chrome://extensions`, turn on Developer mode, click **Load unpacked**, and pick the `extension/` folder of
   this repo.
4. Pin "AWOS Replay". In its **Options**, the endpoint should be `http://127.0.0.1:8080/v1`, and the Tell-it agent
   endpoint `http://127.0.0.1:8081/v1` with model `qwen3.5-4b` (`qwen3.5-9b` if you installed the 9B). Those are the
   defaults.

## 2. Record a chore (once per chore)
1. Open the start page of the chore and log in yourself (AWOS never stores passwords).
2. Click the AWOS icon, then **Record this tab**. Do the chore once, normally. Then click **Stop recording**.
3. Under the recording, fill in two fields, then click **Save goal + checks**:
   - **Goal**: what must be true at the end, with the details that matter (for example "marketing e-mails are off and
     the language is Deutsch"). The local verifier reads it.
   - **Expect text on final page**: a phrase that only appears when it worked (for example "Settings saved", "Lead
     added", or the order number). Without a check, the run can only end UNVERIFIED.

## 3. Replay
- Click **Run** on the recording.
- You will be asked to type passwords and codes yourself, and to approve any pay / send / delete / submit click.
- The result is one of four verdicts:
  - **VERIFIED**: all checks passed and the local verifier agrees.
  - **CHECK**: the checks passed, but the local verifier read the final page against your goal and disagrees. Look at that
    run; the reason is shown.
  - **UNVERIFIED**: no checks were set.
  - **FAILED**: a check failed or the run stopped; the reason is shown.
- The verifier can only downgrade. It never turns a failed check into a pass.
- Replay the same chore again a few days or weeks later. That is the real test: sites change.

## 4. Good first chores (recurring, low risk)
| Chore | Why it is a good test |
|---|---|
| Change a setting in a profile / settings page (notifications, language) | checkboxes, a dropdown, a save with a status text |
| Add a contact or lead to a CRM-style form | form filling; typed values become "Ask each run" params |
| Look up a record in an internal admin table or portal | a search box (Enter), a private page that must stay local |
| Check a train, flight or order status page | a read-only check with an expected text |
| Fill a weekly timesheet or a job application, stopping at Submit | the approval gate must pause at Submit |
| Export a CSV report from a dashboard | filters, then a download check |

## 4b. Tell it (v0.5): describe the chore instead of recording it
Type what you want in the popup's **Tell it** box and press **Start**; the agent does it in the tab while you watch.
- **The brain is local by default**: an agent model on your Mac, Qwen3.5-4B (or Qwen3.5-9B on a 16 GB+ Mac) on
  `127.0.0.1:8081`, set up by the installer: `bash scripts/install_replay.sh --agent 4b` (or `--agent 9b`). The 2B model
  on 8080 only repairs and verifies; it cannot drive a page. Pages you open with Tell it (intranet, CRM, bank, e-mail) never leave the Mac.
- **Claude is opt-in**: Options -> Tell it -> "Claude with your own API key" plus the consent box. When it is on, each
  step's page text and button names go to Anthropic. Add sites to "Never use the cloud on these sites" and Tell it
  stops before sending anything from them; a recipe can be locked the same way.
- Each step shows its reason and action. **Pause**, **Stop**, **Take over** (do it by hand; Resume hands back), or type a
  correction ("no, use the Settings tab"). Submit / pay / send / delete / unsubscribe-like clicks wait for **Approve**.
  Passwords and codes: you type them in the page, then **Continue**.
- When it says done, check the page, edit the proposed checks, and click **Yes, that's it**: it is saved as a normal
  recording and replays like one.
- Good first goals: "turn off marketing e-mails in my settings", "add Grace Hopper as a new lead", "what is the
  status of order 4417 in the admin table", "fill this application up to, not including, Submit", "compare the price
  of this product in two shops and tell me the cheaper one".
- **Export training data** saves your Tell-it runs to a file (it contains the page text the agent saw; nothing is
  uploaded). Details: `docs/specs/tell_it.md`.

## 5. Send the results back
Click **Export run log** in the popup and send the JSON file.
- It contains, per run: the verdict, how each step was found, and every model call (the candidate button names it saw and
  its answer).
- It does **not** contain anything you typed (only the length), URL queries, or passwords.
- Button and link names are included (they can contain things like an account name), so skim it before sending.

## Known limits (MVP)
- Main frame only: no iframes, shadow DOM, drag and drop, file uploads or multi-tab chores.
- "Verified" checks the final URL and the texts you set. It does not yet check that a downloaded file arrived.
- The model is a 2B trained on synthetic apps. Real sites are new to it, and finding where it struggles is the point of
  this round.
