<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\AgentForge;

use InvalidArgumentException;
use OpenEMR\Modules\AgentForge\Services\AgentJwtService;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use RuntimeException;

/**
 * Behavior tests for AgentJwtService.
 *
 * Subtask 6.2 covers the constructor's secret-validation contract and
 * the fromEnvironment() factory's env-var loading. Tests use the AGENTFORGE_JWT_SECRET
 * env var via putenv() / getenv() so PHP's variables_order ini setting
 * doesn't change behavior between environments.
 */
final class AgentJwtServiceTest extends TestCase
{
    protected function setUp(): void
    {
        // Each test starts with the env var explicitly cleared so prior
        // test pollution can't leak in. Real production secrets live in
        // the container env, not the test runner.
        putenv('AGENTFORGE_JWT_SECRET=');
    }

    protected function tearDown(): void
    {
        putenv('AGENTFORGE_JWT_SECRET=');
    }

    #[Test]
    public function constructorAcceptsSecretOfAtLeast32Bytes(): void
    {
        // Verifies the happy-path contract: the constructor does not throw
        // for a 32-byte secret. There's no observable state to assert
        // beyond "no exception."
        $this->expectNotToPerformAssertions();
        new AgentJwtService(secret: str_repeat('a', 32));
    }

    #[Test]
    public function constructorRejectsSecretShorterThan32Bytes(): void
    {
        $this->expectException(InvalidArgumentException::class);
        $this->expectExceptionMessageMatches('/at least 32/');

        new AgentJwtService(secret: str_repeat('a', 31));
    }

    #[Test]
    public function constructorRejectsEmptySecret(): void
    {
        $this->expectException(InvalidArgumentException::class);

        new AgentJwtService(secret: '');
    }

    #[Test]
    public function fromEnvironmentReadsAgentForgeJwtSecret(): void
    {
        putenv('AGENTFORGE_JWT_SECRET=' . str_repeat('x', 64));

        // Asserts that fromEnvironment() does not throw when the env var
        // is set to a sufficiently long secret. There's no observable
        // state to assert beyond "construction succeeded."
        $this->expectNotToPerformAssertions();
        AgentJwtService::fromEnvironment();
    }

    #[Test]
    public function fromEnvironmentThrowsWhenSecretMissing(): void
    {
        // setUp left it empty; treat as missing.
        $this->expectException(RuntimeException::class);
        $this->expectExceptionMessageMatches('/AGENTFORGE_JWT_SECRET/');

        AgentJwtService::fromEnvironment();
    }
}
