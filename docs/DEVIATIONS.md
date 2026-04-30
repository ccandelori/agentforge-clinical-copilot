# Deviations from the Original Plan

A chronological log of decisions where we did something different from
[`ARCHITECTURE.md`](../ARCHITECTURE.md), the original Taskmaster task spec,
or our initial assumption. Each entry captures **what changed**, **why**,
and **what we learned**.

The log exists so a future reader can recover the *reasoning* behind
divergences that look unmotivated against the planning artifacts. Big
architectural decisions also get an ADR in [`docs/adr/`](./adr/) when
created; this file is the lightweight running record.

---

## 2026-04-30 — Dropped `langchain` from sidecar dependencies

**Plan:** Taskmaster Task 5.1 (`pyproject.toml`) listed both `langgraph` and
`langchain` as production dependencies.

**Deviation:** Dropped top-level `langchain`; kept `langgraph` only.

**Why:** `langgraph` already pulls `langchain-core` transitively, and the
orchestrator uses LangGraph directly per ARCHITECTURE.md §3 ("Why LangGraph
and not vanilla LangChain"). No code path needs top-level `langchain`
chain primitives.

**What we learned:** Task specs written before the dep graph is verified can
carry redundant entries. Verify transitive availability before pinning every
named package — the lock surface should reflect what we actually import,
not what we think we'll need.

**Artifacts:** [commit d6dcea5e2](../sidecar/pyproject.toml).

---

## 2026-04-30 — Switched FastAPI app to factory pattern (test-driven discovery)

**Plan:** Task 5.3 (`main.py`) had `app = create_app()` at module level so
uvicorn could discover the app via `agentforge.main:app`.

**Deviation:** Removed the module-level `app`; production now runs
`uvicorn agentforge.main:create_app --factory`. Dockerfile ENTRYPOINT and
README updated to match.

**Why:** Module-level `app = create_app()` triggers `Settings()` instantiation
at *import* time. When pytest imports `agentforge.main` for collection, that
fires *before* a fixture can monkeypatch the required `JWT_SECRET` and
`HMAC_KEY` env vars — the test fails with Pydantic validation errors. The
factory pattern defers config loading to invocation, preserving fail-fast
on missing config in production while letting tests construct app instances
independently.

**What we learned:** "Required without default" config fields fight with
Python's import-time evaluation. The factory pattern is the standard FastAPI
way to defer this and should have been the default. Worth checking: any
future `app = X()` at module level for code that depends on Settings is a
landmine for testing.

**Artifacts:** [commit d40253ec9](../sidecar/src/agentforge/main.py).

---

## 2026-04-30 — Used Doctrine Migrations despite `db/README.md` "not yet integrated" warning

**Plan:** Task 40 spec said "Doctrine or direct" SQL migration. `CLAUDE.md`
says "New schema changes use Doctrine Migrations." `db/README.md` warns:
"The Doctrine Migrations system is NOT fully integrated into OpenEMR yet.
Don't make database changes using this until #10708 is completed."

**Deviation:** Followed CLAUDE.md. Schema change ships as a Doctrine
migration even though the upstream integration is incomplete.

**Why:** User instruction: "honor claude.md." When CLAUDE.md and an in-repo
README disagree, CLAUDE.md is authoritative — it is the project-specific
instruction set, while `db/README.md` reflects upstream OpenEMR's state.

**What we learned:** When CLAUDE.md and another in-repo doc disagree, surface
the conflict and ask before picking. The user has context the docs may not.
Practical follow-on: because the migration system isn't auto-integrated,
existing installs need a manual `./cli migrations:migrate` step to apply
indexes; documented in
[`oe-module-agentforge/README.md`](../interface/modules/custom_modules/oe-module-agentforge/README.md)
as a pre-deploy gate. Fresh installs bypass the migration runner via
`sql/database.sql`, so we updated both paths.

**Artifacts:** [commit f35cc1f47](../db/Migrations/Version20260430000001.php),
[`oe-module-agentforge/README.md`](../interface/modules/custom_modules/oe-module-agentforge/README.md).

---

## 2026-04-30 — Kept redundant `idx_procedure_report_date` (deferred to Task 49)

**Plan:** Task 40.1 spec listed seven indexes, including
`idx_procedure_report_date(procedure_report_id, date_report)` on
`procedure_report`.

**Deviation:** None at the schema level — the index ships as specified.
Created Taskmaster Task 49 (low priority, depends on Task 40) to revisit
post-MVP.

**Why:** `procedure_report_id` is the table's PRIMARY KEY. InnoDB tables are
clustered on the PK, so a secondary index leading with the PK is unlikely to
be selected by the optimizer over the clustered index. The spec is buggy on
this entry. User chose to follow the spec for parity rather than deviate
based on Claude's analysis ("less to touch = less I can break"). Cost to
live with is small (~1-3% slower writes on `procedure_report`, modest
storage). Cost to repay is one trivial `DROP INDEX` migration.

**What we learned:** "Trust the spec when you can't independently verify the
analysis" is a defensible conservative posture, especially when learning a
codebase. The deferral mechanism (Task 49 + comment in the migration's
`getDescription()` + this entry) makes the debt visible without forcing a
decision now. Tracked debt is much cheaper than invisible debt.

**Artifacts:**
[`db/Migrations/Version20260430000001.php`](../db/Migrations/Version20260430000001.php),
Taskmaster Task 49.

---

## 2026-04-30 — Registered AgentForge templates dir in TwigTemplateCompilationTest

**Plan:** Task 1.4 created
[`oe-module-agentforge/templates/agent_panel.html.twig`](../interface/modules/custom_modules/oe-module-agentforge/templates/agent_panel.html.twig)
extending `patient/card/card_base.html.twig`. The Task 1 spec did not call
out updating the project's existing Twig compilation test infrastructure.

**Deviation:** Added
`'interface/modules/custom_modules/oe-module-agentforge/templates'` to the
`EXTRA_TEMPLATE_DIRS` constant in
[`tests/Tests/Isolated/Common/Twig/TwigTemplateCompilationTest.php`](../tests/Tests/Isolated/Common/Twig/TwigTemplateCompilationTest.php).

**Why:** The regression check during Task 40 wrap-up surfaced a failing
isolated test:
`TwigTemplateCompilationTest::templateCompiles with data set "...agent_panel.html.twig"`.
The compilation test discovers `.twig` files via `SEARCH_DIRS` and compiles
them through a Twig environment whose `FilesystemLoader` is built from
`EXTRA_TEMPLATE_DIRS`. Without our module's templates dir in that list,
the loader couldn't resolve `{% extends "patient/card/card_base.html.twig" %}`
during compilation, even though the actual runtime Twig environment (built
by `Bootstrap` via `TwigContainer`) would have resolved it fine.

**What we learned:** Adding a Twig template in a new module isn't fully
self-contained — the project has a separate test-time Twig harness with its
own template-path registry. Any new module that ships templates needs an
entry in `EXTRA_TEMPLATE_DIRS`. This is now part of the implicit
"new module checklist" alongside `openemr.bootstrap.php`, `info.txt`, etc.
Worth folding into the bootstrapping flow when we add modules going forward.

**Artifacts:** [commit pending in this branch],
[`oe-module-agentforge/templates/agent_panel.html.twig`](../interface/modules/custom_modules/oe-module-agentforge/templates/agent_panel.html.twig),
[`tests/Tests/Isolated/Common/Twig/TwigTemplateCompilationTest.php`](../tests/Tests/Isolated/Common/Twig/TwigTemplateCompilationTest.php).

---

## 2026-04-30 — Stripped half-finished dependency storage from Bootstrap.php

**Plan:** Task 1.2 spec defined `Bootstrap.php` with constructor-stored
`$twig`, `$logger`, and `$eventDispatcher` properties (mirroring the
existing `oe-module-comlink-telehealth` and `oe-module-claimrev-connect`
patterns).

**Deviation:** Removed the `$twig`, `$logger`, and `$eventDispatcher`
property storage. The constructor still accepts these parameters (per
OpenEMR's module-loader contract) but does not retain them. Storage will
be reintroduced in Task 2 when `subscribeToEvents()` begins registering
listeners that actually need them. Also added `assert()` calls in
`openemr.bootstrap.php` to narrow the `$classLoader` and `$eventDispatcher`
globals injected by OpenEMR's `ModulesApplication`.

**Why:** PHPStan level 10 (per CLAUDE.md) flagged the stored-but-unused
properties as `property.onlyWritten`. Other modules suppress this with
baseline entries — but CLAUDE.md says "Avoid baselines. Never add new
baseline entries — fix the underlying type error" *and* "no half-finished
implementations either." Both directives point the same way: don't store
dependencies before you use them. Stripping is the honest fix.

The bootstrap.php globals were similarly flagged (`method.nonObject`,
`variable.undefined`) because PHPStan can't see through OpenEMR's
inject-by-name pattern. CLAUDE.md says "Avoid inline `@var` casts" — so
instead of `/** @var */`, we use `assert($x instanceof Y)`, which is
runtime-defensive in dev (where `assert.active=1`) and a no-op in
production. PHPStan understands the assertion for type narrowing.

**What we learned:** Two things worth recording:
1. The spec mirrored a pattern from established modules whose Bootstrap
   classes are *complete*. Copying their structure for a stub class
   imports the half-finished anti-pattern. Better to strip down and grow.
2. OpenEMR's project practice (baseline entries for module bootstrap globals)
   conflicts with CLAUDE.md's "avoid baselines" rule. The `assert(... instanceof ...)`
   idiom satisfies both — it's the right pattern for any future module
   bootstrap files we add.

**Artifacts:**
[`oe-module-agentforge/openemr.bootstrap.php`](../interface/modules/custom_modules/oe-module-agentforge/openemr.bootstrap.php),
[`oe-module-agentforge/src/Bootstrap.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Bootstrap.php).

---

## 2026-04-30 — Used `PatientDemographics\RenderEvent` instead of `Main\Tabs\RenderEvent` for panel injection

**Plan:** Task 2 spec said register the agent panel on
`OpenEMR\Events\Main\Tabs\RenderEvent::EVENT_BODY_RENDER_POST` and check
`$_SESSION['pid']` to gate rendering.

**Deviation:** Use `OpenEMR\Events\PatientDemographics\RenderEvent::EVENT_SECTION_LIST_RENDER_AFTER`,
which provides the patient ID directly via `$event->getPid()`.

**Why:** `Main\Tabs\RenderEvent` fires from `interface/main/tabs/main.php:562`
— the OpenEMR app shell, **not** any patient view. Other modules use it
for global UI plumbing (`oe-module-comlink-telehealth` injects telehealth
JS/CSS scripts there; `oe-module-faxsms` injects a floating phone widget).
Following the spec literally would render the agent panel at the bottom
of the global app shell, once per login, with broken styling because
`agent_panel.html.twig` extends `patient/card/card_base.html.twig` —
which assumes section-list context that doesn't exist in the shell.

`PatientDemographics\RenderEvent::EVENT_SECTION_LIST_RENDER_AFTER`, on
the other hand, fires from `interface/patient_file/summary/demographics.php:1529`
— inside the patient summary section list, exactly where ARCHITECTURE.md
§1 places the agent panel. It also gives us the canonical patient ID
via `getPid()` so we don't need session-state inspection. Same event used
by `oe-module-claimrev-connect`'s eligibility card and `SmartLaunchController`'s
SMART app section — so we're matching the established OpenEMR pattern
for "add a card to the demographics page."

**What we learned:** OpenEMR has at least four render events (`Main\Tabs`,
`PatientDemographics`, `Patient\Summary\Card`, `PatientPortal`), each for
a different surface and lifecycle. Picking one by name without confirming
where it actually fires (and what other modules do with it) is risky.
For future "add a card to X" work, **identify the dispatch site first**:
the event's name often suggests broader applicability than its actual
fire context. `Patient\Summary\Card\RenderEvent` was also considered but
turned out to be for *modifying* existing cards (note, reminder, lab,
etc.) via `RenderInterface` injection — not adding new ones.

**Artifacts:**
[`oe-module-agentforge/src/Bootstrap.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Bootstrap.php),
dispatch site at `interface/patient_file/summary/demographics.php:1529`.

---

## 2026-04-30 — Used `Symfony\Component\EventDispatcherInterface` not `Symfony\Contracts\...`

**Plan:** Task 2 spec imported
`Symfony\Contracts\EventDispatcher\EventDispatcherInterface`.

**Deviation:** Use `Symfony\Component\EventDispatcher\EventDispatcherInterface`.

**Why:** The Contracts version exposes only `dispatch()`. We need
`addListener()`, which is on the Component interface (a superset of
Contracts). The existing `oe-module-claimrev-connect` makes the same
choice for the same reason. Task 1.2 already used Component; Task 2
spec was inconsistent.

**What we learned:** When a spec dictates an interface, verify it has the
methods you need. Symfony Console / EventDispatcher / etc. all have
"Contracts" minimal interfaces and "Component" expanded ones — the
Component is usually what application code wants.

**Artifacts:**
[`oe-module-agentforge/src/Bootstrap.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Bootstrap.php),
[`oe-module-agentforge/openemr.bootstrap.php`](../interface/modules/custom_modules/oe-module-agentforge/openemr.bootstrap.php).

---

## 2026-04-30 — Added autoload-dev entry for module test discovery

**Plan:** OpenEMR modules self-register their PSR-4 namespace at runtime
via `openemr.bootstrap.php` calling `$classLoader->registerNamespaceIfNotExists`.
Tests don't go through that path — the standard composer autoloader
handles them, and module namespaces aren't in `composer.json`.

**Deviation:** Added
`OpenEMR\\Modules\\AgentForge\\` → `interface/modules/custom_modules/oe-module-agentforge/src`
to `autoload-dev` in `composer.json`.

**Why:** Subtask 2.2's TDD tests failed with `Class not found` because the
isolated test runner has no module-aware autoloading. The choices were
(a) add the entry, (b) `require_once` the class file in each test, or
(c) put tests inside the module like `oe-module-comlink-telehealth` does
(separate `phpunit.xml`, not picked up by main regression). (a) makes
module tests discoverable by `composer phpunit-isolated`, which is
where we want the regression gate to live.

**What we learned:** New modules with tests under `tests/Tests/Isolated/Modules/<name>/`
need a one-line `autoload-dev` entry mapping their namespace to their
`src/` directory, plus a `composer dump-autoload` after editing.
Documented in this entry so future modules don't re-derive the
discovery flow.

**Artifacts:** [`composer.json`](../composer.json),
[`tests/Tests/Isolated/Modules/AgentForge/BootstrapTest.php`](../tests/Tests/Isolated/Modules/AgentForge/BootstrapTest.php).

---

## 2026-04-30 — Lazy Twig environment in Bootstrap

**Plan:** Task 1.2 spec eagerly constructed Twig in the constructor:
`$this->twig = (new TwigContainer($path, $kernel))->getTwig();`. Task 40's
deviation #6 stripped the storage entirely.

**Deviation:** Constructor stores `?Environment $twig = null` (optional,
test-injectable) and a fallback kernel. Twig is constructed lazily on
first render via `getTwigForRendering()`.

**Why:** Subtask 2.4's TDD wanted to inject a fake Twig (ArrayLoader with
a stub template) so tests verify behavior without the full OpenEMR
template chain. Eager Twig in the constructor would force every test —
including the ones that only exercise event subscription — to provide a
working `Kernel`, which the isolated test environment can't initialize
("OpenEMR Kernel not initialized" runtime error).

Lazy gives us:
- Constructor succeeds in any environment (no Kernel needed for non-render paths).
- `renderAgentPanel` consumes a Twig that's either injected (tests) or
  built from `OEGlobalsBag::getInstance()->getKernel()` (production).
- Subscribe-only tests don't need fake Twig at all.

**What we learned:** "No half-finished implementations" (CLAUDE.md) and
"don't make tests provide irrelevant fixtures" both push toward lazy
construction of dependencies that are only used by some methods. The
property loses `readonly` (it's set on first use), but `?Environment`
+ `??=` keeps the mutation contained and idempotent. Worth replicating
for the future LLM client / Redis client / Langfuse setup in the
sidecar — same problem shape.

**Artifacts:**
[`oe-module-agentforge/src/Bootstrap.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Bootstrap.php).

---

## 2026-04-30 — Used `lcobucci/jwt` not `firebase/php-jwt` for JWT minting

**Plan:** Task 6 spec's implementation snippet used `Firebase\JWT\JWT` /
`Firebase\JWT\Key`; subtask 6.5's title contradicted with "lcobucci/jwt
Library and Full Claims."

**Deviation:** Use `lcobucci/jwt` 4.x.

**Why:** `composer.json` already requires `lcobucci/jwt: ^4.3.0`;
`firebase/php-jwt` is not a dependency. OpenEMR's OAuth2, OpenID
Connect, and JWKS code all use lcobucci. Adding a second JWT library
just for one module would split the project's auth surface for no
gain.

**What we learned:** When a spec snippet and a subtask title disagree,
verify against `composer.json` and existing project usage before
picking. The spec was written ahead of implementation; what landed in
the codebase wins.

**Artifacts:**
[`oe-module-agentforge/src/Services/AgentJwtService.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/AgentJwtService.php).

---

## 2026-04-30 — Wrote GACL query directly; `AclMain::getUserRole()` doesn't exist

**Plan:** Task 6 spec's implementation snippet had:
```php
return AclMain::getUserRole($userId);
```

**Deviation:** Created `UserRoleLookup` with a Doctrine DBAL query
mirroring `OpenEMR\Common\Logging\BreakglassChecker`'s shape:
```sql
SELECT grp.value
FROM gacl_aro JOIN gacl_groups_aro_map JOIN gacl_aro_groups
WHERE BINARY aro.value = ?
ORDER BY grp.id ASC LIMIT 1
```
(The lookup keys on username, not user id, because `gacl_aro.value`
stores the username — same convention BreakglassChecker uses.)

**Why:** `AclMain` only has ACL *check* methods (`aclCheckCore`,
`aclCheckForm`, `zhAclCheck`, etc.) — no role-getter. The spec called
a method that doesn't exist. Writing the GACL query directly is the
right path; it matches the established BreakglassChecker pattern in
the same area of the codebase.

The `BINARY` collation match and the lowest-id deterministic
tiebreaker are inherited from BreakglassChecker — case-sensitive
match avoids a username-spoofing class of bug, and a deterministic
"primary group" keeps role claims stable across requests for the same
user.

**What we learned:** Spec method references should be verified against
the actual class file. Looking at OpenEMR's auth/ACL code reveals that
"role" is not a single concept in OpenEMR — there's the OAuth coarse
`user_role` (`users` / `patient` / `system`), and there are GACL
group memberships. The spec conflated them.

**Artifacts:**
[`oe-module-agentforge/src/Services/UserRoleLookup.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/UserRoleLookup.php),
[`tests/Tests/Services/AgentForge/UserRoleLookupIntegrationTest.php`](../tests/Tests/Services/AgentForge/UserRoleLookupIntegrationTest.php).

---

## 2026-04-30 — `BreakglassContext` value object + PSR-20 clock injection

**Plan:** Task 6 spec passed `bool $breakglassFlag` and
`?string $breakglassReason` as separate parameters to `mintToken`,
plus `time()` directly inside the method for `iat` / `exp`.

**Deviation:** Two changes:
1. Replaced the two breakglass parameters with a `BreakglassContext`
   value object whose constructor enforces "flag=true requires non-empty
   reason."
2. Inject `Psr\\Clock\\ClockInterface` instead of calling `time()` /
   `new DateTimeImmutable()` directly.

**Why:**

CLAUDE.md is explicit on both points. "Parse, don't validate" pushes
constraints into the type system: the consistency rule (true flag →
non-empty reason) is something every caller had to remember; making
it a constructor invariant means `mintToken` only sees valid contexts.
Whitespace-only reasons are caught too — the trim guard closes a
foot-gun where a single-space reason would satisfy a naive empty
check while leaving the audit trail with no actionable text.

Clock injection is the PSR-20 idiom CLAUDE.md cites directly:

> Inject ClockInterface instead of calling new DateTimeImmutable()
> or time() directly. This makes time-dependent code deterministically
> testable.

The new tests use `Lcobucci\\Clock\\FrozenClock` so iat/exp values are
predictable across runs. `lcobucci/clock` is already in
`composer.json`.

**What we learned:** Two ADR-flavored decisions worth preserving as
patterns: (a) wrap related primitive parameters in a value object when
they have a consistency invariant, (b) never embed clock reads in
business logic. Both apply to many of the sidecar's coming
implementations (verifier, orchestrator) where time and consistency
matter.

**Artifacts:**
[`oe-module-agentforge/src/Services/BreakglassContext.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/BreakglassContext.php),
[`oe-module-agentforge/src/Services/AgentJwtService.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/AgentJwtService.php).

---
