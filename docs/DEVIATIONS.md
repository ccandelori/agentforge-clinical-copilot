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

## 2026-05-01 — `get_active_allergies` reads `lists` table directly, not FHIR `AllergyIntolerance`

**Plan:** Taskmaster Task 17's description called for the tool to call
the FHIR `AllergyIntolerance` endpoint to source the patient's allergy
list.

**Deviation:** Implemented the tool as a direct read of the `lists`
table (filtered to `type='allergy' AND activity=1`) via a Doctrine DBAL
repository plus a JWT-validated PHP internal endpoint — the same
pattern the other three MVP tools (`get_demographics`,
`get_active_problems`, `get_active_medications`) use.

**Why:** The three sibling tools that already shipped under
ARCHITECTURE.md §4 are direct-DB readers; their internal endpoints
(`/agentforge/internal/{demographics,problems,medications}.php`)
share the same structure (JWT validator + repository + JSON response
wrapper). Routing the fourth tool through OpenEMR's FHIR stack would
have introduced a parallel access pattern (R4 resource fetcher,
SMART scope check, JSON:API parsing) for one tool, increasing the
verifier's surface area and making the fan-out path less uniform.
The direct-DB path also lets the `lists.severity_al` field — which
isn't in the FHIR mapping by default — flow through unchanged for
clinical relevance. Schema confirmed at
[`sql/database.sql:7676–7717`](../sql/database.sql).

**What we learned:** Tool-spec language can drift behind implementation
patterns once a project has settled on one. Better to surface the
choice in the deviation log than to silently break uniformity, but
when three siblings agree on a pattern, conformance to that pattern is
the strong default. The four-tool MVP now has one access shape end-to-end,
which the verifier's record-cache lookup (Task 28) can rely on. If a
future tool genuinely needs FHIR semantics (e.g. condition severity
codings, encounter linkage) it can land alongside this one without
disturbing the existing trio.

**Artifacts:**
[`sidecar/src/agentforge/tools/allergies.py`](../sidecar/src/agentforge/tools/allergies.py),
[`oe-module-agentforge/src/Services/AllergiesRepository.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/AllergiesRepository.php),
[`oe-module-agentforge/src/Controllers/InternalAllergiesController.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Controllers/InternalAllergiesController.php),
[`oe-module-agentforge/public/internal/allergies.php`](../interface/modules/custom_modules/oe-module-agentforge/public/internal/allergies.php).

---

## 2026-05-01 — get_vitals_trend skipped EventAuditLogger and QueryUtils

**Plan:** Task 20 spec called for the internal vitals endpoint to use
`OpenEMR\Common\Database\QueryUtils` for the database read and
`OpenEMR\Common\Logging\EventAuditLogger::recordEvent` to write a per-call
audit record. Both helpers exist on this codebase.

**Deviation:** The implementation follows the established pattern from
`get_active_medications` and `get_active_problems` instead:
- Repository takes a Doctrine `Connection` and runs the query directly via
  `fetchAllAssociative` — no `QueryUtils` indirection.
- Controller relies on the JWT validation chain (browser → PHP `/turn` →
  signed user-bound JWT → sidecar → echoed JWT → `AgentJwtValidator`) as
  the audit path. No `EventAuditLogger.recordEvent` call.

**Why:** Two reasons.

1. The medications / problems endpoints set the precedent two tasks ago.
   Diverging from them on tool number four would split the agent's tool
   layer into two coding styles for no functional gain — and the next
   tools to land (allergies, labs) would have to pick a side.
2. The JWT itself is a tamper-evident record of who initiated the request,
   for which patient, with what breakglass context, signed by the same
   secret OpenEMR uses to mint it. Replaying an internal endpoint without
   a fresh JWT is impossible (5-minute expiry; no refresh path on the
   sidecar). Persisting a separate audit row for every tool call would
   duplicate information already captured at the `/turn` boundary —
   `AgentProxyController` is the right layer for "who asked the agent
   what." A dedicated tool-call audit can be added later without
   rewriting the repositories.

A separate decision worth noting: the repository handles two
schema-induced coercions explicitly because they're easy to get wrong.
`bps` and `bpd` are stored as `VARCHAR(40)` (not numeric), so we
int-coerce and treat empty strings as null. All numeric vitals are
`DECIMAL` defaulting to `'0.00'`; we treat `0.0` as "not recorded" and
return `null` to keep the LLM from interpreting the schema default as a
clinically meaningful "0 systolic." Both rules are documented in the
class docblock so future readers see them once.

**What we learned:** Established-pattern continuity beats spec literalism
when the spec is older than the pattern. Worth surfacing the same call
when the upcoming allergies / labs / immunizations tools (Tasks 17, 18,
21+) hit the same fork: don't reintroduce `QueryUtils` /
`EventAuditLogger` as the convention unless the security review of the
JWT-as-audit chain says otherwise.

**Artifacts:**
[`oe-module-agentforge/src/Services/VitalsRepository.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/VitalsRepository.php),
[`oe-module-agentforge/src/Controllers/InternalVitalsController.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Controllers/InternalVitalsController.php),
[`oe-module-agentforge/public/internal/vitals_trend.php`](../interface/modules/custom_modules/oe-module-agentforge/public/internal/vitals_trend.php).

---

## 2026-05-01 — get_recent_labs reads MariaDB directly, not FHIR Observation

**Plan:** Task 18 spec said the lab tool should query the FHIR
`Observation?category=laboratory` endpoint, with the sidecar treating the
results like any other FHIR resource (consistent with the FHIR-first
direction the OpenEMR mainline is taking).

**Deviation:** Skipped FHIR. Implemented `get_recent_labs` as a direct
Doctrine DBAL read of `procedure_order` → `procedure_report` →
`procedure_result`, matching the existing `get_active_medications` and
`get_active_problems` tools.

**Why:**
1. Pattern consistency. The other three tools all bypass FHIR; doing
   this one differently means two parallel "how does an MVP tool talk to
   data" idioms in the codebase before the third tool even ships.
2. Auth surface. The FHIR layer expects an OAuth2 access token; the
   sidecar carries a short-lived `AGENTFORGE_JWT_SECRET`-signed JWT
   that's already wired into the existing `/agentforge/internal/*`
   endpoints. Going FHIR-first means standing up an OAuth2 client
   credential flow inside the sidecar — for one tool — purely so we can
   then validate it in PHP, while the JWT path already validates and
   already enforces patient-scope. Net cost: real new code surface for
   no agent-side benefit.
3. Schema cost. FHIR Observation flattens `procedure_order/report/result`
   into a single resource type; the agent doesn't need or use the FHIR
   facets we'd be paying to translate (Identifier, Subject, Encounter
   refs, ValueQuantity vs ValueCodeableConcept polymorphism). The 10
   fields it actually uses come straight from `procedure_result` columns.
4. Index leverage. Task 40 already added the composite index
   `idx_procedure_order_patient_date`. Hitting `procedure_order` directly
   uses that index; the FHIR layer's joins go through ORM glue that
   doesn't.

**What we learned:**
- Bypassing FHIR is a load-bearing MVP convention in this fork, not a
  one-off shortcut. When the verifier (Task 28) ships and we add
  redaction, the boundary will need to know which fields are sensitive
  per tool, regardless of FHIR vs SQL — so postponing FHIR doesn't
  postpone the redaction work either.
- The 200-row analyte cap matters more than the 90-day window. A single
  CMP + CBC easily emits 30+ analytes per report; a chronically ill
  patient inside a 90-day window can saturate context fast. The cap is
  a deliberate floor, not a placeholder.
- The `since_days` parameter is the first tool input the LLM controls;
  the controller clamps to `1..365` server-side as defense-in-depth
  against the model emitting `since_days: 99999`.

**Artifacts:**
[`oe-module-agentforge/src/Services/LabsRepository.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Services/LabsRepository.php),
[`oe-module-agentforge/src/Controllers/InternalLabsController.php`](../interface/modules/custom_modules/oe-module-agentforge/src/Controllers/InternalLabsController.php),
[`sidecar/src/agentforge/tools/labs.py`](../sidecar/src/agentforge/tools/labs.py).

---

## 2026-05-01 — Sensitivity policy keyed `agentforge:policy:loaded`, not the role-clearance sentinel

**Plan:** Task 8 had already shipped a sentinel at `agentforge:policy:version`
that the gateway checks before loading per-role clearances. Tasks 9 + 10
could have reused that key as the "policy loaded" indicator.

**Deviation:** Introduced a separate sentinel — `agentforge:policy:loaded`
— for the sensitivity policy (Task 9), holding the policy's version
integer. The role-clearances sentinel at `agentforge:policy:version` is
unchanged and still gates the per-role membership lookup.

**Why:** The two policies are loaded by different mechanisms and could
fall out of sync. Role clearances come from a still-undefined loader
(deferred to a sibling subtask of 8); the sensitivity policy comes from
a YAML file via the new `load_sensitivity_policy`. Sharing one sentinel
would conflate "I have one policy loaded" with "I have both", and a
partial Redis flush could leave the system reporting healthy while one
table was empty. Two sentinels is the cheap honest answer.

**What we learned:** Sentinels are cheap; conflating them is not. When
two independent loads each need a fail-closed indicator, give each its
own key. Future audit-log + verifier-prompt loads will follow the same
pattern.

**Artifacts:**
[`sidecar/src/agentforge/gateway/policy_loader.py`](../sidecar/src/agentforge/gateway/policy_loader.py),
[`sidecar/src/agentforge/gateway/auth_gateway.py`](../sidecar/src/agentforge/gateway/auth_gateway.py).

---

## 2026-05-01 — Declared `pyyaml` as an explicit sidecar dependency

**Plan:** Task 9 anticipated PyYAML being available as a transitive
dependency (langfuse pulls it).

**Deviation:** Added `pyyaml>=6.0` to `[project.dependencies]` in
`sidecar/pyproject.toml`. Also extended `[[tool.mypy.overrides]]` for
the bare `yaml` import (no first-party type stubs).

**Why:** PyYAML *is* available transitively, but the policy loader is
the first first-party caller. Relying on a transitive dep for a load-
bearing import is a footgun: a future bump of the parent that drops
yaml would silently break the policy loader. Declaring the dep
explicitly puts the lock surface in line with what we actually use.

**What we learned:** The CLAUDE.md rule against new deps applies to
genuinely new packages, not to surfacing existing transitives — the
honest move when first-party code starts importing a module is to put
it in `pyproject.toml` regardless of how it got onto the venv.

**Artifacts:**
[`sidecar/pyproject.toml`](../sidecar/pyproject.toml).

---

## 2026-05-01 — `check_record_visibility` fail-closes on missing metadata for a fired rule

**Plan:** Task 10 spec called out fail-closed for a missing `attending_user_id`
when `attending_only=True`, but didn't expand the rule to other matchers.

**Deviation:** Documented + implemented the same fail-closed posture for
any future rule whose match needs metadata not in the `RecordMetadata`
shape. The current MVP only has the attending case, but the
`_user_satisfies_rule` helper is structured so adding a new matcher
that needs an absent field is a one-line return-False.

**Why:** A "missing-metadata = allow" default is the audit failure mode
ARCHITECTURE.md §2 specifically warned against ("a sensitivity model
that has to read the secret to know it's secret is no model at all"
— and a model that defaults open when fields are absent is structurally
similar). The default-allow-on-no-rule-fires is *only* safe because no
rule fires; once a rule fires, the user must actively satisfy it.

**What we learned:** Two distinct cases that look similar:
1. No rule matches the metadata → allow (the record isn't classified
   as sensitive by any structural rule).
2. A rule matches but its required metadata is absent → deny (the
   classifier fired but we can't evaluate against the principal).

The visibility check encodes this distinction; future rule additions
that introduce new metadata fields should follow the same pattern.

**Artifacts:**
[`sidecar/src/agentforge/gateway/auth_gateway.py`](../sidecar/src/agentforge/gateway/auth_gateway.py).

---

## 2026-05-01 — Breakglass does not silently bypass record visibility (MVP)

**Plan:** ARCHITECTURE.md §2 says break-the-glass "propagates to three
distinct log destinations" but is ambiguous on whether it changes the
visibility decision itself.

**Deviation:** For MVP, breakglass does NOT flip a record-visibility
deny to an allow. The decision logic ignores `ctx.breakglass_reason`
entirely. Audit-log routing (Task 34, future) will still see the
breakglass intent on `RequestContext` and emit it to OpenEMR's
`log.comments`.

**Why:** A silent bypass embedded in the visibility check would let the
shape "I had a reason, so I saw the record" leak through the agent's
output without any sentinel. Whether breakglass should ever be a
*technical* override (vs. an audit-only signal) is a clinical-policy
decision that belongs to a downstream review, not to MVP wiring.
Keeping the decision conservative now preserves the option to add a
narrow breakglass-aware override later under controlled circumstances.

**What we learned:** "We logged it" and "it was allowed" are different
guarantees. The agent's tool layer should keep them separate even when
the JWT carries both — the audit path consumes the intent, the
visibility path consumes only the structural metadata.

**Artifacts:**
[`sidecar/src/agentforge/gateway/auth_gateway.py`](../sidecar/src/agentforge/gateway/auth_gateway.py).

---

## 2026-05-01 — Timeout/Retry shipped as per-tool budget; phase + turn budgets deferred

**Plan:** Task 41 + ARCHITECTURE.md §9 spec a four-level budget hierarchy
(`per_tool=2s` → `tool_phase=4s` → `total_turn=7s` → `max_steps=7`) and a
`per_attempt_timeout=0.5s` inside `RetryPolicy`.

**Deviation:** Three narrower-than-spec decisions:

1. The retry helper enforces only the `per_tool` budget. `tool_phase`,
   `total_turn`, and `max_steps` are config fields on `TimeoutPolicy`
   but no orchestrator code currently reads them.
2. `RetryPolicy.per_attempt_timeout` is a config value but is not wired
   through the httpx layer. Each fetcher call still uses httpx's
   default 5-second timeout, not 0.5s.
3. The new `timeouts.py` module sits at `sidecar/src/agentforge/timeouts.py`
   rather than the spec's `sidecar/src/agentforge/config/timeouts.py`.
   The existing `agentforge/config.py` is a flat file (`Settings`
   class); promoting it to a package just to host one new policy
   module would touch every import site for a cosmetic gain.

**Why:** The retry-on-transient and graceful-degradation behaviours are
the user-visible contract Task 41 was added to deliver — they fix the
cold-start 503 the droplet smoke test surfaced. The phase/turn budgets
and per-attempt HTTP timeouts are orchestrator-level coordination
features whose useful shape depends on Task 27 (Planner restructure)
and per-fetcher timeout wiring, which are larger refactors. Shipping
them now would either invent infrastructure they don't yet need or
foreclose on the Planner's design.

**What we learned:** Retry policy values that fit normal transient
errors do not absorb a cold-start. With `backoff_base=0.1`,
`backoff_factor=2.0`, `max_attempts=3` the total inter-attempt wait
is 0.3 s — fine for a flaky upstream, useless against a 5-second
container boot. If cold-start absorption matters in production, the
right fix is a pre-warm ping at deploy time (or a longer-backoff
profile applied only on the first request after process start), not
larger retry counts.

**Artifacts:**
[`sidecar/src/agentforge/timeouts.py`](../sidecar/src/agentforge/timeouts.py),
[`sidecar/src/agentforge/orchestrator/__init__.py`](../sidecar/src/agentforge/orchestrator/__init__.py).

---

## 2026-05-01 — Breakglass dedup is in-memory and lives only for the sidecar process

**Plan:** Task 34's spec describes idempotency ("called once per session,
not per tool call") but does not specify the dedup mechanism.

**Deviation:** Dedup is an in-memory `set[tuple[int, int, str]]`
keyed on `(user_id, patient_id, session_id_or_sentinel)` and lives
for the sidecar's process lifetime. There is no Redis SETNX or
shared bookkeeping. A sidecar restart wipes the dedup table; a
multi-replica deployment would write one audit row per replica per
session.

**Why:** Dedup correctness is bounded by the 75-min session TTL —
even at high turn rates, the per-process unique-session count over
that window is small. The cost of a duplicate audit row on
restart / multi-replica is bounded and observable; the cost of
cross-process coordination is real plumbing (SETNX semantics, key
TTL choice, error paths when Redis is down). For MVP one replica
is the deployment shape, so the in-memory variant is adequate.

**What we learned:** "Once per session" has two readable meanings —
"once across the system" and "once per process per session." The
first is what the auditor wants to read; the second is what we
ship. The gap is bounded (one row per replica boot), and a worse
failure mode is "we silently failed to audit" — which the in-memory
variant avoids by never marking AUDIT_FAILED outcomes as logged
(the next turn retries).

**Artifacts:**
[`sidecar/src/agentforge/breakglass.py`](../sidecar/src/agentforge/breakglass.py).

---

## 2026-05-01 — Breakglass audit fires from the orchestrator, not the auth gateway

**Plan:** Task 34 subtask 34.4 says "Integrate BreakglassAuditTool into
Auth Gateway flow."

**Deviation:** The audit fires at the orchestrator's turn entry
point, not inside `AuthGateway.validate_request`.

**Why:** Dedup is keyed on `session_id`, which arrives on
`TurnRequest` — not in the JWT. The auth gateway is a stateless JWT
validator that doesn't see `session_id`. Wiring the audit there
would mean either passing `session_id` into auth (which couples auth
to body parsing) or always-audit (which would write per-tool-call
rather than per-session, breaking the idempotency contract).
Orchestrator-level integration keeps `AuthGateway` stateless and
preserves session-keyed dedup.

**What we learned:** "Auth audits" is a layer-of-abstraction
trap when the dedup key isn't part of the auth artifact. The right
question is "what does the dedup key live on?" — and `session_id`
lives on the turn, not the token.

**Artifacts:**
[`sidecar/src/agentforge/orchestrator/__init__.py`](../sidecar/src/agentforge/orchestrator/__init__.py).

---

## 2026-05-01 — Eval framework ships with hand-authored fixtures and skips LLM-as-judge

**Plan:** Tasks 37, 38, 39 spec a 3-layer eval setup:
  1. MockToolLayer fixtures pinned to a specific OpenEMR demo DB SHA
     (`demo_5_0_0_5.sql + openemr/openemr:flex image SHA`).
  2. EvalHarness with programmatic grounding + LLM-as-judge for
     relevance scoring.
  3. RegressionLockTestSuite of 8 canonical Q&A run against the
     orchestrator end-to-end.

**Deviation:** Five concrete narrowings:

1. Fixtures are hand-authored against the typed Pydantic schemas, not
   captured from a populated demo DB. The two patient phenotypes
   ("Susan Underwood — complex chronic" / "Alex Newman — sparse")
   exercise the contracts the eval depends on, but a future
   capture-pass against a real demo image would be more authoritative.

2. The mock layer covers 8 tools, not 9 — encounters (Task 21) is
   still pending. When 21 lands, add an `encounters` block to each
   patient in `agent_eval.json` and a `get_encounters` method on
   `MockToolLayer`.

3. LLM-as-judge for relevance is not implemented. The harness checks
   *grounding* (every citation resolves to a real record) and
   *behavior* (a per-case callable assertion). Adding a third
   relevance score would need a real LLM client in CI — costly and
   flaky for what's a tertiary signal alongside grounding.

4. RegressionLocks ship as 6 canonical (response, case, fixture)
   triples, not 8. Four positive locks (UC1 complex / UC1 sparse /
   UC2 NSAID-renal / vitals citation) and two adversarial locks
   (fabricated citation; hallucinated labs for a sparse chart).
   Two more cases worth adding when there's clear product intent —
   the framework grows trivially.

5. The regression locks do **not** invoke the orchestrator or the
   real LLM. They pin canonical agent-style response strings to the
   committed fixtures and verify that the harness scores them
   correctly. What the locks catch is drift in the eval primitives
   (citation parser, citation index builder, fixture schemas) — not
   drift in the model itself. End-to-end model regression tests
   need a separate manual / scheduled eval run with real LLM access
   and are an open follow-up.

**Why:** The framework's value is two-fold: (a) deterministic CI
gating on the eval-side primitives, (b) a foundation that a manual
eval can call into to score real model outputs. Both work without a
real DB or real LLM. The cost of pinning fixtures to a specific
docker SHA today (capture pass + maintenance burden) outweighs the
value when the fixtures are themselves new — we'd be pinning to
ourselves. The cost of LLM-as-judge in CI (API keys, $, flakiness)
likewise outweighs its incremental signal alongside grounding +
behavior callables.

**What we learned:** The phrase "regression lock" hides a design
choice — what you lock against. Locking the *eval primitives*
catches schema/parser drift in CI without requiring a model. Locking
the *model* requires real-LLM runs and is necessarily off-CI. Both
are useful; they answer different questions.

**Artifacts:**
[`sidecar/tests/fixtures/agent_eval.json`](../sidecar/tests/fixtures/agent_eval.json),
[`sidecar/tests/mocks/tools.py`](../sidecar/tests/mocks/tools.py),
[`sidecar/tests/eval/harness.py`](../sidecar/tests/eval/harness.py),
[`sidecar/tests/eval/regression_locks.py`](../sidecar/tests/eval/regression_locks.py).

---

## 2026-05-02 — Task 44 reframed: `api_log_option` is global, not per-user

**Plan:** Task 44 specs a deploy script that does

```php
QueryUtils::sqlStatementThrowException(
    "UPDATE users SET api_log_option = 1 WHERE id = ?",
    [$agentUserId]
);
```

with the rationale "suppress body logging for the agent's API user."

**Deviation:** Two factual problems with the spec, fixed by
reframing what we ship:

1. There is no `users.api_log_option` column. `api_log_option` is a
   site-wide global (`globals.gl_name = 'api_log_option'`) defined in
   `library/globals.inc.php` with three valid values (`0`, `1`, `2`).
   The `users` table has no per-row override, and the REST listener
   (`ApiResponseLoggerListener`) reads only the global.
2. AgentForge's internal endpoints don't pass through
   `ApiResponseLoggerListener` because the listener fires only on
   `HttpRestRequest` — our `public/internal/*.php` scripts use bare
   `Symfony\Component\HttpFoundation\Request`. So the body-logging
   the spec wants to suppress is *already not happening* for
   AgentForge calls today.

We ship `scripts/configure_api_logging.php` instead — sets the
**global** to `1` (minimal logging) idempotently, with `--check` for
read-only inspection. That's the real lever, and shipping the script
puts a defense-in-depth control in operators' hands for any future
calls that DO route through the REST stack. The spec's per-user
fantasy is documented as not-applicable.

Subtask 44.5 (integration test for "API logging suppression
behavior") is intentionally a no-op: with no AgentForge call
flowing through the listener, there is no behaviour to suppress and
nothing to assert beyond the global value, which the script's
`--check` mode already reports.

**What we learned:** Task specs at this fork's level can encode
data-model assumptions that don't match the upstream OpenEMR
schema. When the assumption breaks, the right move is to ship the
*intent* (PHI hygiene in `api_log`) rather than the literal
mechanism (per-user UPDATE). Always grep the schema before trusting
a spec's column references.

**Artifacts:**
[`scripts/configure_api_logging.php`](../scripts/configure_api_logging.php),
[`docs/DEPLOYMENT.md`](DEPLOYMENT.md) (new "Optional: tighten REST
api_log body logging" section).

---

## 2026-05-02 — Encounters tool reads form_encounter directly, not via FHIR

**Plan:** Task 21 spec calls for the agent's encounters tool to call
OpenEMR's standard FHIR `Encounter` endpoint (`/apis/fhir/r4/Encounter`).

**Deviation:** Mirrors the AgentForge custom-internal-endpoint pattern
(`/agentforge/internal/recent_encounters.php`) instead, reading
`form_encounter` directly via Doctrine DBAL — same shape as every
other AgentForge tool.

**Why:** Routing through FHIR would require:
  * OpenEMR OAuth2 client credentials provisioned for the agent.
  * A token-management layer in the sidecar (acquire, refresh, scope).
  * A second authorization story in addition to the per-user JWT we
    already mint and forward.

None of this infrastructure exists yet. Building it just to fetch
encounters duplicates the trust boundary the existing internal
endpoints already establish — JWT-validated, pid-scoped, no session
state. The custom-endpoint path also keeps the sensitivity-gating
contract coherent: the gateway sees `RecordMetadata` with both
`encounter_category` (pc_catid) and `note_type` (the `sensitivity`
column), which a FHIR Encounter resource doesn't surface as cleanly.

**What we learned:** Reusing existing OpenEMR REST surfaces is a real
option but it's not free — the sidecar's auth model would have to
grow. Picking the consistent-internal-endpoint path keeps the auth
story flat and the gating logic in one shape.

**Artifacts:**
[`sidecar/src/agentforge/tools/encounters.py`](../sidecar/src/agentforge/tools/encounters.py),
[`interface/modules/custom_modules/oe-module-agentforge/public/internal/recent_encounters.php`](../interface/modules/custom_modules/oe-module-agentforge/public/internal/recent_encounters.php).

---
