# Demo video scripts

Three videos, 60–90 s each. Use fictional sites only: host them locally or on a test domain under names like `billing.example.com` and `shop.example.com`. No real accounts, real emails, real invoices or real customer data. Use a clean Chrome profile with only the AWOS Replay extension pinned, and hide bookmarks.

Format: 1920x1080, 30 fps, screen recording plus a voice-over. Burn the captions in, since X and the landing page autoplay muted. Speed up waiting time with a visible "2x" label; never cut out a step without saying so.

Fictional cast: the user is "Sam" at "Northwind Studio" (invoice account NW-0042). The billing site is "Ledgerly" at `billing.example.com`.

Only film what the shipped build does. If a scene needs a feature that isn't in the build yet (recipe import, `{{month}}` params), skip that shot and say so in a caption; never fake it.

---

## Script 1: Hero (landing page), about 80 s

"Record the invoice download once, replay it, get VERIFIED, share it."

| Time | On screen | Voice-over / caption |
|---|---|---|
| 0:00–0:05 | Title card: "Show it once. It does it forever." | (caption only) |
| 0:05–0:12 | `billing.example.com` dashboard, logged in as Sam. Cursor hovers over Billing. | "Every month I download the same invoice. Three minutes, every month." |
| 0:12–0:18 | Click the AWOS icon, then **Record this tab**. A small "recording" banner appears. | "So I show AWOS Replay once." |
| 0:18–0:32 | Billing → Invoices → choose "October 2026" in the month dropdown → **Download invoice**. The page shows "Invoice INV-2026-10 downloaded". | "Click Billing, Invoices, pick the month, download. Just how I'd do it anyway." |
| 0:32–0:38 | **Stop recording**. Popup shows "4 actions, from billing.example.com". | "Four actions, saved locally in Chrome." |
| 0:38–0:46 | Type the goal "October 2026 invoice for NW-0042 downloaded" and the expected text "Invoice INV-2026-10 downloaded". **Save goal + checks**. | "I tell it what success looks like. That's how it knows if a run worked." |
| 0:46–0:58 | New tab on the dashboard. Click **Run**. Steps replay on their own (show at 2x, labelled). | "Next month I press Run." |
| 0:58–1:06 | Popup: green **VERIFIED**, checks ✓ url, ✓ text; log line "verifier says VERIFIED". | "VERIFIED: the checks passed, and a small model on my Mac read the page and agreed. If it disagrees, I get CHECK instead of a fake success." |
| 1:06–1:14 | The `/r/` recipe page for "Download this month's invoice" with steps in plain English. | "And I can share the chore as a link. No passwords inside, and the recipe never touches a server." |
| 1:14–1:20 | End card: "AWOS Replay. Free, open source, runs on your Mac." + `awos.tirramind.com` + "Install: INSTALL_COMMAND" | "AWOS Replay." |

Notes: if the shipped build doesn't export a recipe from a recording yet, show the gallery's prebuilt "Download this month's invoice" recipe at 1:06 and caption it "example recipe". Do not imply the verdict checked the PDF file; it checks the page text and URL.

---

## Script 2: Self-healing after a page change, about 75 s

"The site changed. Replay finds the moved button, and stops when the button is really gone."

| Time | On screen | Voice-over / caption |
|---|---|---|
| 0:00–0:06 | Title: "Sites change. Recordings usually break." | (caption only) |
| 0:06–0:14 | `reports.example.com` (fictional "Metricly"). The saved recording "Export weekly signups CSV" is shown in the popup. | "I recorded this export last week." |
| 0:14–0:22 | Split screen: old page with a button **Export CSV** at top right; new page where it's now **Download as CSV**, inside a toolbar at the bottom. | "This week the site was redesigned. The button has a new name and a new place." |
| 0:22–0:36 | Click **Run**. On the step, the log shows: "scoring not confident ... asking local model over 3 candidates", then "click 'Download as CSV' via model pick #1". | "The usual matching isn't sure, so it asks a small model running on this Mac. It picks from three candidates." |
| 0:36–0:44 | Popup: **VERIFIED**, text check "Export ready" ✓. | "Found it, finished, and the checks passed." |
| 0:44–0:52 | Third version of the page: the export feature is removed and only "Share report" and "Delete report" buttons remain near it. | "Now the hard case: the export button is gone. There's a similar-looking button right where it was." |
| 0:52–1:04 | Run again. Log: "local model says the control 'Export CSV' is gone". Popup: **FAILED NOT_FOUND**. Page banner: "stopped. ..." | "It doesn't click the neighbour. It stops and tells me why. A wrong click is worse than no click." |
| 1:04–1:12 | Options page showing the endpoint `http://127.0.0.1:8080/v1`. | "The model runs on localhost. The extension refuses anything else." |
| 1:12–1:15 | End card with URL. | "AWOS Replay." |

Notes: run it live several times before filming and use real log lines from the popup. If the model picks wrong or abstains on the rename take, film a different rename; don't edit the log.

---

## Script 3: Sharing and importing a recipe, about 70 s

"A colleague sends a timesheet recipe. You read it, add it, run it, and it stops before Submit."

| Time | On screen | Voice-over / caption |
|---|---|---|
| 0:00–0:06 | Title: "Share a chore as a link." | (caption only) |
| 0:06–0:14 | A chat window (fictional, plain styling) with a message from "Priya": "here's the timesheet one" + a link `awos.tirramind.com/r/#...` | "A teammate sends me a recipe link." |
| 0:14–0:26 | `/r/` page: title "Fill weekly timesheet (stops before Submit)", steps in plain language: "Open timesheets.example.com/week", "Type {{hours}} into Monday hours"... "Ends before Submit". | "The page shows exactly what it will do, in plain English, before I add anything. The recipe sits after the #, so the server never sees it." |
| 0:26–0:32 | Highlight a line showing a placeholder "Password: you type this yourself". | "No passwords inside. Secrets become placeholders, and I type them." |
| 0:32–0:40 | Click **Add to AWOS Replay**. Popup shows the new recipe in the list. | "One click to add it." |
| 0:40–0:56 | On `timesheets.example.com`, click **Run**. Fields fill (2x). The run pauses for the password field: "Type the password ... yourself, then press Continue". Type it, then Continue. | "It fills the week. When it reaches the password, it waits for me." |
| 0:56–1:04 | Run finishes with the form filled and the **Submit** button untouched. Popup verdict. | "It stops before Submit. I check the hours and press Submit myself." |
| 1:04–1:10 | End card: `awos.tirramind.com/recipes/` | "Six example recipes are on the site." |

Notes: if "Add to AWOS Replay" isn't in the shipped build, end the video at 0:32 with the caption "Import lands in the next build", and don't film 0:32–1:04. If `{{hours}}`-style params aren't in the build, show a recipe with fixed values instead. The verdict at 1:04 will be UNVERIFIED unless the recipe carries a check; give it the expected text "Draft saved" so the run can end VERIFIED honestly.
