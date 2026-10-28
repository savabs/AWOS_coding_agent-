# Validation from real users online: verdict (2026-10-23)

Five parallel studies of what real people say (Reddit via the Arctic Shift archive where reachable, Hacker News,
CAclubindia/TaxTMI, Trustpilot, community forums, official rules). Reddit direct was blocked; Indian subreddits
were not reached. Files in this folder.

| Study | Verdict |
|---|---|
| `invoice_pain_global.md` | **Mixed, leaning not validated.** Real monthly chore for founders with 20–30 SaaS tools, but 1–3 h/month, willingness to pay ~$10–50/month; "just make a mailbox rule"; Reddit saturated with founders pitching this; 2FA is the sharpest pain and also the trust objection. |
| `invoice_pain_india.md` | **Mixed, not validated as pitched.** The GST reverse-charge argument is weak (credit rests on the self-invoice; big vendors now bill from India and appear on the GST portal). Real money is lost on domestic invoices missing the company GSTIN. CA firms' pain is chasing clients, not portals. Prices are tiny (~₹10k/year tools). |
| `competitor_complaints.md` | **Pain real, unsolved, crowded.** "Portal connections fail / silently miss invoices" is the top complaint (16 sources). Hubdoc quit portal fetch in 2022. At least 7 tools are already local-first, so "logins stay on your machine" is not a differentiator. Gaps: silent failures, long-tail and changing portals (self-healing), MFA hand-back, statements, fair pricing. |
| `trust_agents_logins.md` | **Qualified yes, only for software that cannot move money.** Top fears: prompt injection, AI near bank accounts. Big US banks block agents for payments; read-only connectors are tolerated. Pitch: cannot pay or send, approval on anything risky, separate audit log. |
| `investor_docs_pain.md` | **Medium; the strongest pain and budget.** Family offices: "a tremendous amount of time clicking through bank portals… thousands of documents"; tools cost $40k–100k/year (Addepar, Masttro), Asora from $900/month; complaints about Canoe under-delivering, a 15–20% exception tail and 3–6 months of onboarding. Consumer and India versions weak. |

## What changed

1. **Company invoice collection is not a strong first business as pitched.** The pain is real but small per
   customer, cheap substitutes exist (mailbox rules, Ramp), the field is crowded and local-first is already common.
   It stays useful as a cheap feature or hook, not the company.
2. **The India GST angle mostly falls apart.** India-first for invoices is not supported.
3. **Investor-portal documents have the strongest pain and the highest budgets**, and they match the founder's
   finance contacts. The opening: small family offices, RIAs, multi-family offices and CPA firms serving wealthy
   clients that are priced out of the $40k+ tools, plus the long tail of portals and the exception tail that the
   incumbents handle with people.
4. **Our real edge is reliability, not privacy:** every expected document accounted for, failures reported
   loudly, portals that repair themselves, OTP handed back to a person, and no ability to move money. That is
   exactly what the verification layer and the model training are for.

## Revised recommendation (provisional)

- **First wedge:** investor-portal document collection and tracking ("expected vs received" per fund, per
  quarter) for small family offices, RIAs and CPA firms, sold through the founder's finance contacts.
  Read-only; cannot pay or send.
- **Depends on geography:** the evidence is US/global; the India version (registrars already email a
  consolidated statement) is not validated.
- **Kill test:** 5 interviews with family-office or wealth-management operations people. Go if at least 3 log into
  10+ portals per quarter, are not fully served by Canoe/Arch/Addepar (price, missing portals, exceptions), and
  would pay for a 60-day pilot.
