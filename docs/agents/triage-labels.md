# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's GitLab tracker. We use GitLab scoped-label style so labels group together in the UI.

| Label in mattpocock/skills | Label in our tracker          | Meaning                                  |
| -------------------------- | ----------------------------- | ---------------------------------------- |
| `needs-triage`             | `status::needs-triage`        | Maintainer needs to evaluate this issue  |
| `needs-info`               | `status::needs-info`          | Waiting on reporter for more information |
| `ready-for-agent`          | `status::ready-for-agent`     | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `status::ready-for-human`     | Requires human implementation            |
| `wontfix`                  | `status::wontfix`             | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

GitLab scoped labels (with `::`) are mutually exclusive within a scope — applying `status::ready-for-agent` automatically removes any other `status::*` label. Create the labels in the project's label settings if they don't already exist; the first triage skill run will create them on demand.
