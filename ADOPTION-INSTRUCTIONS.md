# Adoption instructions — Telegram-Bot

Target repository: https://github.com/shajeduzzamanenan992008inc/Telegram-Bot
Default branch verified: `main`

## Files to adopt

1. Copy this package's `LICENSE` to the repository root as `LICENSE`.
2. Apply the two license metadata lines from this package's `pyproject.toml` to
the repository's existing `[project]` table:
   - `license = "LicenseRef-Enan-Ultra-PANC-1.1"`
   - `license-files = ["LICENSE"]`
   The supplied `pyproject.toml` is a full copy of the version inspected on
   2026-11-27, with only those two lines added. Review it against the current
   repository version before replacing anything.
3. Append the section in `README-LICENSE-SECTION.md` to the root `README.md`.
4. Keep `.github/licensing/COMMERCIAL-LICENSE-TEMPLATE.md` clearly identified
   as a template. It is not a substitute for the root `LICENSE` and should not
   be presented as an executed commercial agreement.

## Activation condition

The text is adoption-ready but is not currently active on GitHub. The
repository was verified as **Private**, and no root `LICENSE` exists on
`main`. Under the license's own effective-date clause, the public grant starts
only when the root `LICENSE` is first published on the default branch while
the repository is publicly accessible. Do not backdate the effective date.

## Before publication

- Confirm that the Licensor owns or has permission to license all first-party
  code, documentation, assets, and templates being covered.
- Review any external contributor commits and obtain an explicit compatible
  license grant for their contributions, or exclude those contributions.
- Review bundled fonts, icons, images, model assets, copied snippets, and other
  third-party materials; retain their separate notices and licenses.
- Do not include real `.env` values, bot tokens, API keys, database credentials,
  chat exports, user uploads, backups, or other User Data in the license scope.
- Have qualified counsel review the custom non-commercial terms for the
  jurisdictions and distribution model in which the project will be used.

## No remote changes made

This package was created locally for review. No GitHub file, branch, commit,
Pull Request, repository setting, or visibility setting was changed. No push
was performed.
