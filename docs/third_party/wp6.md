# WP6 third-party code: element grounding

`scaffold/agent/desktop/grounding.py` ports logic from two permissively
licensed projects. No source file was copied verbatim. The algorithms below
were rewritten in Python to run over macOS AX / DOM-like JSON trees, and the
file header names both origins.

| Origin | Files read | Licence | What was ported | Changes |
|---|---|---|---|---|
| microsoft/playwright, commit b335271 (1.65.0-next) | `packages/injected/src/selectorGenerator.ts` (`generateSelectorFor`, `buildNoTextCandidates`, `buildTextCandidates`, `suitableTextAlternatives`, `trimWordBoundary`, `isGuidLike`, `penalizeScoreForLength`, `combineScores`); `roleUtils.ts` / `ariaSnapshot.ts` read for role / accessible-name semantics only | Apache-2.0 | The cost table (test id 1, role+name 100/105, placeholder 120/125, label 140/145, text 180/185, title 200/205, role without name 510, nth 10000); the length penalty for scores in (50, 300); number-stripping text alternatives; GUID-like id rejection; icon-font (private-use) name rejection; best-first search with a uniqueness check; `nth` only up to 5; best-parent scoping (two levels) that skips a parent when it cannot improve the result; retargeting to the interactive ancestor | Python over AX nodes instead of the DOM. The CSS fallback is replaced by container + nth scoping. AXIdentifier stands in for the test id. Fuzzy matching is a case-insensitive substring, as with `getByRole(name)` without `exact`. Up to k verified locators are returned rather than one. The engine string syntax is AWOS's own. |
| OpenAdapt (openadapt-flow) | `packages/flow/openadapt_flow/backends/macos_backend.py` (`find_candidates`, `_locator_matches`, `_unique_candidate`, `_AX_ENUMERATION_LIMIT`, `MacCandidateSet.truncated`); `runtime/resolver.py` read for the ladder design | MIT | Window-scoped bounded iterative DFS (4000-node budget); a truncated walk refuses rather than resolving from a partial or first-match walk; an identifier match also needs the role to agree; zero candidates means not found, several mean an ambiguous refusal, and candidate order is never used; a fallback ladder that ends at OCR | Refusals are returned as typed values (`Refusal.reason`) instead of exceptions. Further gates added: modal blocking, risk escalation (via `ax_compress.button_role`), disabled, off-screen. OCR is an interface only (`OcrProvider`, `NullOcr`, `AppleVisionOcr` stub). |

Not used (licence or privacy):
- OmniParser: its icon-detect weights are AGPL.
- skyvern, automa, workflow-use: AGPL. workflow-use's target format (text anchor + container/position hint) informed the `Target` fields as a design idea only. No code was read into this module.
- browser-use: not vendored. It sends PostHog telemetry by default (`ANONYMIZED_TELEMETRY=true`).
- stagehand, selenium-ide: reports read for design. selenium-ide's lesson is that its fallback list is never tried and that it takes the first match, so AWOS's ladder requires exactly one match at every tier. No code was taken.

## Notices

### Playwright (Apache License 2.0)

```
Playwright
Copyright (c) Microsoft Corporation

This software contains code derived from the Puppeteer project (https://github.com/puppeteer/puppeteer),
available under the Apache 2.0 license (https://github.com/puppeteer/puppeteer/blob/master/LICENSE).
```

Licensed under the Apache License, Version 2.0 (the "License"); you may not
use this file except in compliance with the License. You may obtain a copy of
the License at http://www.apache.org/licenses/LICENSE-2.0. Unless required by
applicable law or agreed to in writing, software distributed under the License
is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied. See the License for the specific language
governing permissions and limitations under the License.

Modified by AWOS (2026-10-19): ported from TypeScript to Python and adapted to
macOS accessibility trees, as described above.

### OpenAdapt (MIT License)

```
MIT License

Copyright (c) 2026 OpenAdapt.AI (MLDSAI Inc.)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
