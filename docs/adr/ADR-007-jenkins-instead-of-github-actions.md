# ADR-007: Jenkins instead of GitHub Actions

**Status:** Accepted

## Context

The project is developed and tested on a single 16 GB laptop, and the optional evaluations of
real systems may target local servers (InferenceLab, AgentRuntime on `127.0.0.1`) that a hosted
runner cannot reach. Provider credentials for those optional runs should stay on infrastructure
the maintainer controls. The default pipeline must remain offline.

## Decision

* CI is a declarative `Jenkinsfile`; the repository contains no GitHub Actions workflows.
* The default pipeline is offline and CPU-only: lock check, formatting, ruff, strict mypy,
  unit/property/integration/security tests, mock end-to-end runs with gates, secret scan,
  package build and an optional Docker build.
* Provider keys are blanked in the default environment. An optional stage, enabled by the
  `RUN_EXTERNAL` parameter, binds a Jenkins "Secret text" credential and exports it under the
  variable name the system config expects.
* `disableConcurrentBuilds()` keeps one build at a time on the laptop.
* Only reports and summaries are archived.

## Consequences

Positive:

* Builds can reach local model servers and run on the same hardware as development.
* Credentials stay in the Jenkins credential store; nothing secret is needed for the default
  build.
* The pipeline is plain shell + uv, so every stage can be reproduced locally.

Negative / trade-offs:

* The maintainer operates Jenkins (installation, plugins, updates, backups) and must expose a
  webhook endpoint or poll the repository.
* No hosted status checks or matrix builds out of the box; builds are serialized.
* The pipeline assumes a Linux (or WSL2) agent with `sh`.
* Contributors cannot see CI logs unless the Jenkins instance is made accessible to them.
