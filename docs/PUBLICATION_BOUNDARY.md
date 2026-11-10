# Publication boundary

This collaboration extension is a local release candidate. Repository visibility does not grant permission to publish additional research material or select a software license. The maintainer must approve the exact file manifest before the first upload of this extension.

| Category | Publication rule |
| --- | --- |
| Previously published engineering code | May remain public; preserve its existing provenance and third-party notices |
| Contributor interface, validators, synthetic software fixtures, task descriptions and CI | Candidate for release after the exact file manifest is approved |
| Adapted scientific worker and frozen scientific configuration | Confirm institutional, supervisor, partner and possible patent obligations before release |
| Dataset acquisition scripts and source/permission records | Release only when they use authorized public endpoints and retain required attribution |
| Raw datasets and trained models | Do not place in the Git repository by default; users obtain data from original sources and verify fixed digests |
| Contributor result summaries | Publish after contributor consent, privacy checks, protocol review and an explicit acceptance record |
| Private grant applications and confidential templates | Do not publish |
| Private CVs, internal personnel, budgets, contracts and private contact data | Do not publish |
| API credentials, private keys, authentication caches and local service configurations | Do not publish |
| Internal review archives and audit logs containing personal information | Do not publish |
| Unreleased patent material or unauthorized enterprise data | Do not publish without the relevant rights-holder's authorization |

## Software license decision

No repository-wide software license was present at the inspected baseline. This extension does not add a LICENSE, CLA or DCO and does not make a decision about ownership. Possible choices for the rights holder include MIT (permissive), Apache-2.0 (permissive with an explicit patent grant and terms), or deferring an open-source license while ownership is clarified. These choices have different consequences and require authorization; public visibility alone is not a substitute.

Dataset licenses and third-party library licenses remain separate from the software license decision. A data source's license does not license this project's algorithm, and a code license does not confer rights over external data.

## Public and controlled evaluation

The initial tabular tasks use public source data and a published reproducibility protocol. Public evaluation labels and reconstructible controlled changes cannot be described as hidden final-test truth. The worker freezes selections before evaluation, which protects the implemented processing order; it does not prove that a contributor has never inspected public labels.

Future blind evaluation must be a separate task: maintainers control the hidden targets and reference answers, contributors submit an approved model/result format, and evaluation occurs independently. Such a service is not supplied or claimed by this extension.

## Contributor information

GitHub attribution is optional. Do not include machine usernames, hostnames, home paths, private email addresses or credentials in a public result. Machine specifications and dependency versions are collected for reproducibility. Review error logs before sharing. Contribution acknowledgement, paper authorship, intellectual property and financial compensation depend on actual agreements; compute contribution does not automatically confer authorship.

The internal full research archive and this engineering repository remain separate. Do not upload a review ZIP to the repository or an Issue.
