# Try AWOS Replay on real chores (15 minutes)

AWOS Replay is a Chrome extension: **show a web chore once, then replay it whenever you want.**
- When the site has changed, a small model on your own Mac finds the moved button, or stops and says it's gone.
- It never pays, sends, deletes or submits without your click.
- Nothing leaves your machine: the model runs on 127.0.0.1, and recordings stay in Chrome's local storage.

> Status: MVP. It works on our test pages (28 automated tests). These are its first real sites, and the point of this
> round is to find where it breaks. Use a **separate Chrome profile**, and start with low-risk chores.

## 1. Install (once)
1. `brew install llama.cpp`. You also need `gh` (logged in) to download the model.
2. Start the model: `bash scripts/awos_model.sh` (first run downloads 1.3 GB). Leave it running. Check it with
   `bash scripts/awos_model.sh --check`.
3. Chrome: open `chrome://extensions`, turn on Developer mode, click **Load unpacked**, and pick the `extension/` folder of
   this repo.
4. Pin "AWOS Replay". In its **Options**, the endpoint should be `http://127.0.0.1:8080/v1`.

## 2. Record a chore (once per chore)
1. Open the start page of the chore and log in yourself (AWOS never stores passwords).
2. Click the AWOS icon, then **Record this tab**. Do the chore once, normally. Then click **Stop recording**.
3. Under the recording, fill **Expect text on final page**: a phrase that only appears when it worked (for example
   "Statement downloaded", "Saved", or the month's name). Click **Save checks**. Without a check, the run can only end
   UNVERIFIED.

## 3. Replay
- Click **Run** on the recording.
- You will be asked to type passwords and codes yourself, and to approve any pay / send / delete / submit click.
- The result is **VERIFIED** (all checks passed), **UNVERIFIED** (no checks set), or **FAILED** with the reason.
- Replay the same chore again a few days or weeks later. That is the real test: sites change.

## 4. Good first chores (recurring, low risk)
| Chore | Why it is a good test |
|---|---|
| Download last month's statement from a bank or card portal | login wall (you type), menus, a date picker, a download |
| Download this month's invoice from a SaaS billing page (Google Workspace, AWS, Notion, Zoom...) | the classic month-end chore |
| Export a CSV report from a dashboard (Stripe, analytics, a CRM) | filters, then export |
| Fill a weekly timesheet or expense form, stopping at Submit | form filling; the approval gate must pause at Submit |
| Check an order or shipment status page | a read-only check with an expected text |

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
