# Commercial Distribution Readiness Checklist

Use this before advertising, licensing, or selling the kit. An unchecked item is not a completed control.

## Ownership and licensing

- [ ] Confirm ownership or redistribution rights for every file, snippet, screenshot, logo, font, action example, and third-party asset.
- [ ] Remove customer-specific, company-specific, secret-name, private URL, internal process, and unreleased product information.
- [ ] Choose a real license model (proprietary, permissive, dual, subscription, or services) and have counsel review it.
- [ ] Complete the licensee scope, permitted users/repositories, modification, redistribution, renewal, termination, warranty, liability, and governing-law terms.
- [ ] Add any required third-party notices and attribution.

## Product quality

- [ ] Test install/upgrade in clean sample repositories (not only the source repository).
- [ ] Validate at least one representative repository for each technology stack that will be marketed as tested.
- [ ] Test monorepo and multi-language configuration, missing manifests, empty checks, failed commands, and invalid project profiles.
- [ ] Validate active GitHub Actions with GitHub-hosted runs and a workflow linter.
- [ ] Verify each action SHA and release against the official upstream source before publishing.
- [ ] Verify docs links, templates, issue forms, frontmatter, examples, and all installation paths.
- [ ] Maintain a changelog, versioning policy, compatibility statement, and known-limitations list.

## Commercial operations

- [ ] Define target customer, edition differences, supported stacks, pricing, refund terms, support channel, and update cadence.
- [ ] Define compatibility/support windows and a process for security advisories and critical updates.
- [ ] Prepare a clean demo repository and screenshots/video captured from a tested version.
- [ ] State clearly which features are templates, which are automatic, and which require customer configuration or paid GitHub features.
- [ ] Avoid claims of guaranteed security, regulatory compliance, uptime, scalability, or enterprise certification.
- [ ] Provide a clear upgrade and migration path between product versions.

## Release packaging

- [ ] Build the ZIP from a tagged, reviewed source commit.
- [ ] Exclude temporary files, caches, test fixtures, local settings, and customer-specific profile archives.
- [ ] Generate checksums and retain the release source/artifact identity.
- [ ] Review the final archive contents before upload to a store or customer portal.
