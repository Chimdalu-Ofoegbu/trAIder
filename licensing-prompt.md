# Licensing Implementation Prompt for trAIder

Feed this prompt to Claude Code. It will set up the full multi-license structure across the trAIder repo (BSL 1.1 on contracts, Apache 2.0 on everything else, CC BY 4.0 on docs, trademark assertion on the brand). Reviewable stop point at the end before commit.

---

## The prompt

```
Task: Implement BSL 1.1 licensing structure across the trAIder repo.

This is a multi-component repo. Different directories get different licenses by design. The contracts get BSL 1.1 (Business Source License) to protect the novel mechanism for two years; everything else gets Apache 2.0. Brand mark assertion is separate from code license.

Reference the existing project.md §7 for the repo layout (with the verifier merged into orchestrator per the earlier deviation). All paths below assume that layout.

EXECUTE THESE STEPS IN ORDER:

1. Create the root LICENSE file at /LICENSE with the BSL 1.1 template. Use the canonical MariaDB BSL 1.1 text from https://mariadb.com/bsl11/ verbatim. Fill in these parameter values at the top of the license:

   Licensor:             Bensage
   Licensed Work:        trAIder v1.0.0
                         The Licensed Work is (c) 2026 Bensage
   Additional Use Grant: Any use of the Licensed Work is permitted for:
                         (a) academic, research, and educational purposes;
                         (b) participation in the Arbitrum Open House London
                             Buildathon (June 2026) judging and evaluation;
                         (c) personal, non-commercial experimentation;
                         (d) deployment on test networks for non-production purposes;
                         (e) review and audit of the Licensed Work for security
                             vulnerabilities, with disclosure under standard
                             responsible disclosure practices.

                         Any use of the Licensed Work to operate a commercial
                         speculation market, prediction market, or autonomous
                         agent trading platform that competes with trAIder is
                         not granted under this Additional Use Grant.
   Change Date:          June 14, 2028
   Change License:       Apache License, Version 2.0

2. Create /contracts/LICENSE as an identical copy of /LICENSE. The root LICENSE file is the canonical statement; the contracts/LICENSE file is the explicit per-directory marker.

3. Create /orchestrator/LICENSE with the Apache License 2.0 text. Use the canonical Apache 2.0 text from https://www.apache.org/licenses/LICENSE-2.0.txt. Include the standard "Copyright 2026 Bensage" line in the appendix.

4. Create /backend/LICENSE as an identical copy of /orchestrator/LICENSE (Apache 2.0).

5. Create /frontend/LICENSE as an identical copy of /orchestrator/LICENSE (Apache 2.0).

6. Create /docs/LICENSE with the Creative Commons Attribution 4.0 International (CC BY 4.0) text. Use the canonical text from https://creativecommons.org/licenses/by/4.0/legalcode.txt. Attribution to "Bensage and the trAIder project".

7. Create /TRADEMARK.md at the repo root with this content:

   # Trademark Notice

   "trAIder" and the trAIder brand mark are trademarks of Bensage.

   The licenses applied to the source code in this repository (BSL 1.1,
   Apache 2.0, and CC BY 4.0) grant rights to use, modify, and redistribute
   the code. They do not grant rights to use the trAIder name or brand mark
   in derivative works, forks, integrations, or any commercial context.

   Use of the trAIder name or brand mark for any of the following requires
   prior written permission from Bensage:

   - Naming a forked project, derivative work, or competing service "trAIder"
     or any variation that could cause confusion.
   - Using the trAIder brand mark in marketing materials, websites, or
     applications operated by third parties.
   - Implying endorsement, affiliation, or partnership with trAIder.

   Permitted nominative uses (no permission required):

   - Referring to trAIder in academic papers, news articles, blog posts, and
     factual descriptions.
   - Citing the project in attribution required by the BSL 1.1 or Apache 2.0
     licenses.
   - Discussion in good-faith commentary, criticism, or comparison.

   For trademark licensing inquiries, contact: [Bensage to fill in]

8. Add SPDX license identifiers to the top of every source file in the repo:

   - For every file in /contracts/src/ and /contracts/test/ (Solidity files),
     add as the first line:
     // SPDX-License-Identifier: BUSL-1.1

   - For every file in /orchestrator/src/ (Python files), add as the first
     line:
     # SPDX-License-Identifier: Apache-2.0

   - For every file in /backend/src/ (Python files), add as the first line:
     # SPDX-License-Identifier: Apache-2.0

   - For every file in /frontend/ (TypeScript/JavaScript/TSX files), add as
     the first line:
     // SPDX-License-Identifier: Apache-2.0

   Skip SPDX headers in: package.json, pyproject.toml, foundry.toml, .env
   files, .gitignore, README.md, generated lock files (package-lock.json,
   yarn.lock, uv.lock, foundry.lock). These are configuration or
   documentation files, not source.

9. Update /README.md (or create it if not yet present) to include a Licensing
   section at the bottom with this content:

   ## License

   trAIder uses a multi-license approach by component:

   | Component | License | Notes |
   |---|---|---|
   | Smart contracts (`/contracts`) | [BSL 1.1](./contracts/LICENSE) | Converts to Apache 2.0 on June 14, 2028 |
   | Orchestrator and verifier (`/orchestrator`) | [Apache 2.0](./orchestrator/LICENSE) | |
   | Backend service (`/backend`) | [Apache 2.0](./backend/LICENSE) | |
   | Frontend (`/frontend`) | [Apache 2.0](./frontend/LICENSE) | Brand mark excluded, see [TRADEMARK.md](./TRADEMARK.md) |
   | Documentation (`/docs`) | [CC BY 4.0](./docs/LICENSE) | |

   The contracts are protected under BSL 1.1 to give the trAIder mechanism
   a two-year commercial window before automatic conversion to Apache 2.0.
   See [LICENSE](./LICENSE) for the full BSL terms including permitted uses.

   The trAIder name and brand mark are trademarks of Bensage. See
   [TRADEMARK.md](./TRADEMARK.md) for usage terms.

10. Create a CI workflow at /.github/workflows/license-check.yml that runs
    on every push and pull request, verifying:

    - The /LICENSE file exists and contains the BUSL-1.1 marker text.
    - The /contracts/LICENSE file exists and matches /LICENSE.
    - Every .sol file in /contracts/src/ has the BUSL-1.1 SPDX header on
      line 1.
    - Every .py file in /orchestrator/src/ and /backend/src/ has the
      Apache-2.0 SPDX header on line 1.
    - Every .ts/.tsx/.js/.jsx file in /frontend/src/ has the Apache-2.0
      SPDX header on line 1.

    Use a simple bash script with grep/find. Fail the workflow if any
    required header or file is missing. This catches regressions when
    new files are added without proper headers.

11. After all files are in place, verify the BUSL-1.1 detection by
    running this command locally:

    docker run --rm -v "$PWD":/src licensee/licensee detect /src

    Expected output: License: Business Source License 1.1 (or "Other"
    with a confidence note if Licensee version is older than the BUSL
    catalog entry). If detection fails, the LICENSE text deviates from
    the canonical MariaDB template and must be corrected.

12. Do NOT commit any of these files yet. Stop after step 11 and report
    completion. Bensage will review the files, then commit them in a
    single commit titled:

    chore: add BSL 1.1, Apache 2.0, and CC BY 4.0 licensing structure

CONSTRAINTS:

- Use canonical license text only. Do not improvise license language.
  Do not edit the license body. Only fill in the parameter values
  specified above for BSL.
- The SPDX identifier "BUSL-1.1" is the correct one. Not "BSL-1.1"
  (which is Boost Software License, a different license). Verify every
  reference to the SPDX ID uses BUSL-1.1.
- The Change Date is June 14, 2028. Do not change this to anything else
  without explicit instruction. Two years from the buildathon submission
  date is the chosen window.
- Do not add any files outside the scope above. No package metadata
  updates, no contributor agreements, no code-of-conduct files in this
  pass. Those are separate decisions.
- Preserve any existing project.md, foundry.toml, pyproject.toml, or
  other configuration files. Do not touch them.

REPORT WHEN COMPLETE:

Print a summary of:
- Files created (path + license type)
- Number of source files updated with SPDX headers, broken down by
  language
- Output of the licensee/licensee detection check
- Any deviations from this prompt and the reason
```

---

## Pre-flight checklist before pasting

Verify or update these values before running:

1. **Licensor name.** The prompt uses "Bensage" throughout. Swap to your full legal name or registered entity if you want the strongest enforceability. BSL works either way but a real legal name is the cleanest position if anyone ever forks commercially.

2. **Competition carve-out clause.** The Additional Use Grant contains an explicit anti-competition clause ("Any use of the Licensed Work to operate a commercial speculation market... is not granted"). This is the teeth of BSL for your case. Delete that paragraph if you want softer terms; expand it if you want harder terms. Current language is closer to Uniswap V3's posture than minimal BSL.

3. **Trademark contact placeholder.** TRADEMARK.md leaves a placeholder for trademark licensing inquiries. Fill in with a real email or contact handle before committing.

4. **Change Date alignment with submission.** The prompt assumes June 14, 2026 as the buildathon submission date and sets the BSL Change Date to June 14, 2028. If submission slips, slip the Change Date accordingly so the two-year window stays intact.

5. **Docker availability for step 11.** The licensee detection check requires Docker. If Claude Code is in a sandbox without Docker access, it will skip step 11 and report that the verification check is pending manual run. You can run it locally before flipping the repo public.

## What this prompt deliberately does not do

Out of scope for this pass (handle separately):

- Contributor License Agreement (CLA). Add if you plan to accept external contributions and want to retain the option to relicense.
- Code of Conduct. Standard CoC file (e.g., Contributor Covenant) is a separate decision.
- Security disclosure policy (SECURITY.md). Separate file with a different scope.
- Package metadata license fields (pyproject.toml `license`, package.json `license`, foundry.toml `license`). These can be added per-package after the LICENSE files exist; doing it in the same pass risks scope creep.

## After Claude Code completes

Once the prompt finishes and reports completion:

1. **Review every LICENSE file by hand.** License text in git history is annoying to rewrite. Confirm the parameter values are correct before committing.
2. **Verify SPDX header coverage.** Run `grep -L "SPDX-License-Identifier" $(find contracts/src -name "*.sol")` and the equivalent for Python and frontend directories. Any files printed are missing headers.
3. **Run the local licensee check.** Confirm detection returns "Business Source License 1.1" for the contracts directory.
4. **Commit in a single atomic commit** with the suggested message: `chore: add BSL 1.1, Apache 2.0, and CC BY 4.0 licensing structure`.
5. **Push to the private repo.** Confirm GitHub's About sidebar correctly identifies the license. If it shows "Other" or fails to detect, the LICENSE text deviates from canonical and needs correction.

Detection passing in private is the same as passing in public, so this verifies the public flip will display correctly on submission day.
