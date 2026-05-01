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

## 2026-04-30 — `/agentforge/turn` routed via `public/turn.php`, not a root URL

**Plan:** Task 7 spec said "Route registration: POST /agentforge/turn →
AgentProxyController::turn". OpenEMR has no clean way to register
top-level URLs from a custom module.

**Deviation:** The controller is reached via the standard module URL
`/interface/modules/custom_modules/oe-module-agentforge/public/turn.php`,
which boots OpenEMR's `interface/globals.php` and dispatches. Production
deployments are expected to add an Apache / Caddy reverse-proxy rewrite
to expose the canonical `/agentforge/turn` URL.

**Why:** Three options were considered:

| Approach | Verdict |
|---|---|
| `public/turn.php` entry point | Standard OpenEMR module pattern (matches comlink/claimrev). No infra config needed for dev. |
| `RestApiCreateEvent` listener | Routes through OpenEMR's REST API extension — yields `/apis/...` URLs, wrong location. |
| Custom Apache rewrite from the module | Cleanest URL in dev, but adds infra config the module shouldn't own. |

The `public/turn.php` approach won on "self-contained module / no infra
edits required for development." Reverse-proxy rewrite is a one-line
production deployment task.

**What we learned:** OpenEMR module URL design is constrained by the
historical `interface/modules/custom_modules/<name>/public/...` convention.
Modules with custom routes should pair a `public/<route>.php` entry
point with deployment-time URL rewriting; both halves go in the module
README so deployment-engineers know what to do.

**Artifacts:**
[`oe-module-agentforge/public/turn.php`](../interface/modules/custom_modules/oe-module-agentforge/public/turn.php).

---

## 2026-04-30 — Symfony HttpClient (not PSR-18) for sidecar proxy

**Plan:** Task 7 spec used a generic "proxy to sidecar" stub without
naming a client. ARCHITECTURE.md §1 implies streaming responses from
the sidecar (verifier emits sentence-level chunks).

**Deviation:** Use `Symfony\Contracts\HttpClient\HttpClientInterface`
(from `symfony/http-client`, already in composer.json) rather than the
generic PSR-18 `ClientInterface`.

**Why:** Symfony HttpClient supports response streaming via its
`stream()` method — chunks flow through to the browser without
buffering the full body. PSR-18's `ClientInterface::sendRequest()`
returns a complete `ResponseInterface`; the body's `StreamInterface` is
readable incrementally, but the API isn't designed for incremental
forwarding the way Symfony's is. Tests are simpler too: `MockHttpClient`
+ `MockResponse` model the sidecar's responses (including error /
transport-failure cases) without building PSR-7 fixtures by hand.

For testability the controller still receives `HttpClientInterface`
via constructor injection, so any compatible implementation works. The
actual production wiring (`HttpClient::create([...])` in `turn.php`)
happens at the boundary, not in the controller.

**What we learned:** PSR-18 is the right portability target for
*generic* HTTP clients but not for *streaming proxies* — Symfony's
purpose-built API is one less abstraction layer to reason about.
Worth applying the same pattern in the sidecar's later FHIR-client
work (sidecar talks to OpenEMR via HTTP too); the equivalent Python
choice is `httpx` over a streaming-unaware client.

**Artifacts:**
[`oe-module-agentforge/src/Controllers/AgentProxyController.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Controllers/AgentProxyController.php),
[`tests/Tests/Isolated/Modules/AgentForge/AgentProxyControllerTest.php`](../tests/Tests/Isolated/Modules/AgentForge/AgentProxyControllerTest.php).

---

## 2026-04-30 — Controller takes `BreakglassContext`, not raw flag + reason

**Plan:** Task 7 spec snippet:
```php
$jwtService->mintToken(
    $session->get('authUserID'),
    $patientId,
    $session->get('breakglass_flag', false),
    $request->get('breakglass_reason')
);
```

**Deviation:** The controller constructs a `BreakglassContext` value
object first and passes that to `mintToken`. `AgentJwtService::mintToken`'s
signature is
`(int userId, string username, int patientId, BreakglassContext breakglass)`,
not the spec's four-positional-arg shape.

**Why:** `BreakglassContext` (Task 6.4) enforces the consistency
invariant "flag=true requires non-empty reason" at construction time.
Passing raw flag and reason to `mintToken` would mean every caller has
to re-derive that rule — and a bug in any one caller bypasses the
audit-trail guarantee. The Task 6 → Task 7 contract should respect the
parse-don't-validate choice we made in 6.4.

**What we learned:** When a previous task introduces a value object,
the next task's controller / service contract should consume it. The
spec was written before 6.4's value object existed; updating to match
is a normal evolution, not a deviation worth agonizing over.

**Artifacts:**
[`oe-module-agentforge/src/Controllers/AgentProxyController.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Controllers/AgentProxyController.php).

---

## 2026-04-30 — `RequestContext` carries `username` and `breakglass_reason` too

**Plan:** Task 8 spec defined `RequestContext` with five fields:
`user_id`, `patient_id`, `role`, `breakglass_flag`,
`sensitivity_clearances`.

**Deviation:** Added two more fields to the frozen dataclass:
`username: str` and `breakglass_reason: str | None`.

**Why:** Both are present on the JWT (the PHP minter from Task 6.5
emits them) and have clear downstream consumers:

- `username` is the key for sensitivity-policy lookup. The gateway
  resolves it via JWT claim, but tools and the verifier need it too
  for record-attribution decisions. Recomputing or re-parsing the
  JWT downstream is wasteful and risks drift.
- `breakglass_reason` is required for audit-log routing per
  ARCHITECTURE.md §2: "the reason text appears in exactly one place
  — OpenEMR's `log.comments`". The sidecar emits the audit event
  upstream of OpenEMR's logger; it needs the reason at hand.

Dropping these fields meant later subsystems would either re-decode
the JWT (rebuilding the gateway's work) or pass them as side
parameters, breaking the "RequestContext is the only auth surface"
discipline.

**What we learned:** When the trust-boundary contract is the single
chokepoint, it should carry every claim downstream code might need.
Cheaper to over-include in the value object than to add fields later
once consumers have been written.

**Artifacts:**
[`sidecar/src/agentforge/gateway/auth_gateway.py`](../sidecar/src/agentforge/gateway/auth_gateway.py).

---

## 2026-04-30 — Auth gateway validates `iss` claim explicitly

**Plan:** Task 8 spec snippet decoded the JWT with
`jwt.decode(token, secret, algorithms=['HS256'])` and only checked
`patient_id` afterwards.

**Deviation:** Pass `issuer="openemr-agentforge"` to `jwt.decode` so
PyJWT raises `InvalidIssuerError` for tokens with the wrong (or
missing) `iss` claim. Map that to a 401 response.

**Why:** Task 6 mints tokens with `iss=openemr-agentforge`. Without
issuer enforcement at the gateway, any well-formed HS256 token signed
with the same secret would pass — including tokens minted for a
different purpose by some unrelated component that shares the secret.
HS256 + a shared secret means trust is per-secret, not per-issuer; the
explicit issuer check restores the intended one-to-one binding between
the OpenEMR module and this sidecar.

**What we learned:** PyJWT's verification options are opt-in. The
default `decode()` checks signature + exp; everything else (issuer,
audience, nbf) requires explicit kwargs. Worth treating
`jwt.decode(token, secret, algorithms=['HS256'])` as suspicious in
code review; production callers should also pass `issuer=` and
ideally `audience=`.

**Artifacts:**
[`sidecar/src/agentforge/gateway/auth_gateway.py`](../sidecar/src/agentforge/gateway/auth_gateway.py).

---

## 2026-04-30 — Redis client typed via `Protocol` for test ergonomics

**Plan:** Task 8 spec used `redis.asyncio.Redis` directly as the
client type.

**Deviation:** Defined a private `_RedisProto` Protocol covering only
`get` and `smembers` — the two methods AuthGateway actually uses —
and typed the constructor parameter as `_RedisProto | None`.

**Why:** mypy --strict treats `redis.asyncio.Redis` as a concrete
class. Tests that pass `unittest.mock.AsyncMock(spec=Redis)` (or a
plain `AsyncMock`) fail type-checking even though they work at
runtime. The Protocol gives us structural typing — anything with
the right `get` and `smembers` shape qualifies — and limits the
gateway's coupling to the Redis library to two methods.

**What we learned:** When a constructor needs a small slice of a
big third-party library's API, define a Protocol covering exactly
that slice. The benefits compound: (a) tests pass mypy without
reaching for `# type: ignore`, (b) the gateway can be reused
against fakes / fixtures / alternative backends, (c) the surface
area is documented in the type signature.

**Artifacts:**
[`sidecar/src/agentforge/gateway/auth_gateway.py`](../sidecar/src/agentforge/gateway/auth_gateway.py).

---

## 2026-04-30 — MVP wiring: collapsed Tasks 3, 4, 11, 13, 14, 26, 33 into one branch

**Plan:** Each of the seven tasks above had its own subtasks, dedicated test
suites, and a dependency-graph promotion ritual.

**Deviation:** Compressed all seven tasks into a single branch
(`task-mvp-functional-agent`) with one bundled commit and a far lighter
test footprint — one happy-path test per piece, no exhaustive coverage.
Several large adjacent items were also deferred entirely:
sensitivity-policy redaction (Task 9–10), per-tool fetchers beyond
get_demographics (Tasks 15–25), the verifier loop (Task 28), Redis-backed
session memory + Langfuse tracing (Tasks 30–32), Docker compose + reverse
proxy (Tasks 35–36), and the eval framework (Tasks 37–39).

**Why:** Submission deadline tonight; the goal was a *working* user-visible
agent ("type a question, get a grounded answer about a real patient"), not
a production-shaped surface. The architecture stays compatible — each
deferred item can be added later without rewriting the seven we shipped.

**What we learned:**
1. Three independent streams (frontend, LLM client, tool DTOs) parallelized
   cleanly via subagents because the file boundaries were strict and the
   shared state (`pyproject.toml`, `composer.json` autoload) was already
   set up. The integration step still had to be sequential — but that was
   the cheap part once the foundations existed.
2. "1-tool MVP" is a defensible scope cut. The orchestrator loop and the
   FastAPI /turn route are the real architectural surface; adding tools 2-N
   later is mechanical (one PHP internal endpoint + one async fetcher each).
3. The DEMOGRAPHICS_TOOL_SPEC has zero LLM-visible inputs — patient_id is
   bound from RequestContext server-side. Worth keeping as a pattern for
   the remaining tools: the LLM decides *whether* to call, not *who about*.

**Artifacts:** commit `feat(agentforge): wire MVP end-to-end agent loop`.

---

## 2026-04-30 — Module-local `.env` instead of container env vars

**Plan:** ARCHITECTURE.md assumes `AGENTFORGE_JWT_SECRET` lives in the
deployment's environment (docker-compose env block, kubernetes secret,
etc.) and is available to PHP via `getenv()` at request time.

**Deviation:** Built a small `EnvLoader` (vlucas/phpdotenv) that reads
`interface/modules/custom_modules/oe-module-agentforge/.env` on every
request and stuffs the values into `getenv()` via the Putenv adapter. The
PHP entry points call `EnvLoader::load()` right after `globals.php`.

**Why:** OpenEMR's `development-easy` Apache + mod_php container does not
propagate environment variables to web requests by default — `docker
compose exec` env doesn't reach the request lifecycle, and modifying the
compose file requires a container restart that risks losing other in-place
state (the `sqlconf.php $config` reset, npm assets, etc.). A module-local
`.env` lets the developer drop secrets in without touching the container.
Production deployments can still set the same vars at the
container/process level — `safeLoad()` doesn't override existing env.

**What we learned:**
1. phpdotenv 5.x's `createMutable()` is misleadingly named: by default it
   writes to `$_ENV` and `$_SERVER` only. The `PutenvAdapter` has to be
   added explicitly via `RepositoryBuilder` if you want `getenv()` to see
   the values. Cost us one debug iteration.
2. `.env` files are a perfectly fine boundary for a single-host dev
   container; the architecture document's assumption that env vars come
   from the deployment was correct in spirit but unworkable for the
   easy-mode container we're shipping against tonight.

**Artifacts:**
[`oe-module-agentforge/src/EnvLoader.php`](../interface/modules/custom_modules/oe-module-agentforge/src/EnvLoader.php),
[`oe-module-agentforge/.env.example`](../interface/modules/custom_modules/oe-module-agentforge/.env.example).

---

## 2026-04-30 — Read OpenEMR session through `$_SESSION['OpenEMR']`, not top-level

**Plan:** AgentProxyController + turn.php were built against the
PHPUnit-fixture model where session keys (`pid`, `authUserID`, `authUser`)
sit at the top level of the Symfony Session — i.e. the unit tests pass
`$session->set('pid', 123)` and the controller reads `$session->get('pid')`.

**Deviation:** OpenEMR namespaces *all* its session data under
`$_SESSION['OpenEMR']` (a session bag layer that predates Symfony's
abstraction in this codebase). The bridge from native PHP session to the
Symfony Session in `turn.php` now reads `$_SESSION['OpenEMR'][$key]` and
copies the relevant keys into a `MockArraySessionStorage`-backed Session
that the controller consumes uniformly with the test fixtures.

**Why:** `PhpBridgeSessionStorage` does not flatten the OpenEMR bag, so
`$session->get('pid')` returned null even though the session had a pid.
Discovered live during the smoke test — first manifested as "Error 400:
No patient context" with the chart explicitly open.

**What we learned:**
1. Trust-boundary code that bridges from a legacy session shape to a
   modern abstraction needs an explicit shape verification step (not just
   "session exists"). The unit-test fixture being top-level keys was a
   distorted model of reality — a more honest fixture would have been a
   nested `$_SESSION['OpenEMR'][...]` shape we then translate.
2. `error_log()` + tail of the apache log is faster than any other PHP
   debugger when the issue is "what does the runtime actually have right
   now in this hosted context." Took 2 round-trips to get to the answer.

**Artifacts:**
[`oe-module-agentforge/public/turn.php`](../interface/modules/custom_modules/oe-module-agentforge/public/turn.php).

---

## 2026-04-30 — Rehydrate Authorization header from `apache_request_headers()`

**Plan:** Internal endpoint reads `Authorization` via Symfony's
`Request::createFromGlobals()->headers->get('Authorization')`, which pulls
from `$_SERVER['HTTP_AUTHORIZATION']` like every other PHP web app.

**Deviation:** Apache + mod_php (the dev-easy container's setup) strips
the `Authorization` header from `$_SERVER` by default — it's only
forwarded when explicitly told via `mod_setenvif` / `CGIPassAuth` /
`.htaccess`. The header IS available via `apache_request_headers()`. We
copy it back into `$_SERVER['HTTP_AUTHORIZATION']` before Symfony reads
the globals.

**Why:** Discovered live during the smoke test — first manifested as the
agent answering "I'm unable to retrieve the patient's information,
including their medication list, due to an authentication error (401
Unauthorized)" because the demographics tool's call into PHP got 401 and
the orchestrator faithfully relayed that to the model.

**What we learned:**
1. There is no portable PHP API for "give me the Authorization header" —
   you have to know whether you're under mod_php, php-fpm + nginx, php-fpm
   + apache, etc., each of which has different defaults. The
   `apache_request_headers()` fallback (combined with the `$_SERVER`
   primary) covers the dev-easy container; production may need a real
   `.htaccess` directive instead.
2. The agent's behavior of relaying tool-layer 401s to the user as a
   user-facing message is *correct* — and arguably more useful than the
   raw stack trace would have been — but it makes "tool layer broken"
   indistinguishable from "I don't know" at the UI. A future verifier
   step should classify tool-error responses and surface them differently
   (e.g. "system error, not a knowledge gap").

**Artifacts:**
[`oe-module-agentforge/public/internal/demographics.php`](../interface/modules/custom_modules/oe-module-agentforge/public/internal/demographics.php).

---

## 2026-05-01 — Streaming verifier `DomainConstraintChecker` is sync, not async

**Plan:** Task 29 sketches `verify_medication_claim()` etc. as `async def`.
Task 28's StreamingVerifier was therefore expected to await the
domain-checker call.

**Deviation:** The `DomainConstraintChecker` Protocol shipped in Task 28
is sync (`def check(...) -> tuple[bool, str | None]`). The
`NullDomainConstraintChecker` and the streaming verifier's call site
are sync to match.

**Why:** None of the five planned constraints (medication-name match,
lab-value tolerance, note-authorization echo, diagnosis traceability,
no-counterfactuals) need I/O. They check claim text against a record
dict already in memory — a regex match and a few `.get()` calls. Making
the protocol async would force every implementation to be `async def`
even when the body never `await`s, and it would push an extra event-loop
hop into every claim's verification (which already runs once per
sentence). The trust boundary stays simpler when the slow path doesn't
exist.

**What we learned:**
1. When the spec says `async def` but the body has no `await`, the
   "async" is a costume, not a mechanism. Better to keep the type
   honest and widen later if a real async constraint emerges (e.g.,
   one that needs to consult a separate tool result not in the
   per-turn cache — currently nothing in the v1 catalog does).
2. Task 29 will need to drop the `async` keyword off the constraint
   methods. That's a straight find-and-replace, not a refactor.

**Artifacts:**
[`sidecar/src/agentforge/verifier/protocols.py`](../sidecar/src/agentforge/verifier/protocols.py).

---

## 2026-05-01 — Streaming verifier rejects label-form citations for MVP

**Plan:** ARCHITECTURE.md S6 lists two citation forms:
`[encounter #38241, 2026-04-12]` (ID-anchored) and
`[Rx: lisinopril 20mg, started 2024-08-15]` (label-anchored).

**Deviation:** Only the ID-anchored form is recognised by Task 28's
`CITATION_PATTERN`. Label-form tokens parse to `None` and any sentence
whose only citation is label-form is rejected as `no_citation`.

**Why:** The cache lookup is ID-anchored — every record returned by a
tool this turn is keyed by `(record_type, record_id)`. A label-form
citation has no ID to look up; validating it would require a different
mechanism (string-matching the label against the cached records). That
mechanism IS the medication-name domain constraint from Task 29 (`Constraint
1: med name in active prescriptions`). Building a parallel label-resolution
path in Task 28 would duplicate it.

**What we learned:**
1. The model's freedom to choose a citation format expands the verifier's
   surface area linearly. Locking the citation grammar to one form during
   MVP is the cheap way to keep the trust boundary small.
2. The system prompt should be updated alongside Task 29 to instruct the
   model to prefer ID-anchored citations until label resolution lands.
   Until then, the model emitting a label-form citation looks identical
   to a hallucination from the verifier's perspective — and that's the
   right behavior (better a false-rejection than a fabricated pass).

**Artifacts:**
[`sidecar/src/agentforge/verifier/citation.py`](../sidecar/src/agentforge/verifier/citation.py).

---
