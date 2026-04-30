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
use OpenEMR\Modules\AgentForge\Services\BreakglassContext;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Behavior tests for BreakglassContext.
 *
 * The contract enforced by the constructor: when the breakglass flag is
 * set, a non-empty reason must accompany it. The flag-without-reason
 * combination is a HIPAA documentation hole — the agent must refuse
 * tokens for that case rather than silently log an empty audit reason.
 */
final class BreakglassContextTest extends TestCase
{
    #[Test]
    public function inactiveContextWithNullReasonIsValid(): void
    {
        $context = new BreakglassContext(flag: false, reason: null);

        self::assertFalse($context->flag);
        self::assertNull($context->reason);
    }

    #[Test]
    public function activeContextWithNonEmptyReasonIsValid(): void
    {
        $context = new BreakglassContext(
            flag: true,
            reason: 'Emergency consult; primary record locked.'
        );

        self::assertTrue($context->flag);
        self::assertSame('Emergency consult; primary record locked.', $context->reason);
    }

    #[Test]
    public function activeContextRejectsNullReason(): void
    {
        $this->expectException(InvalidArgumentException::class);
        $this->expectExceptionMessageMatches('/reason/i');

        new BreakglassContext(flag: true, reason: null);
    }

    #[Test]
    public function activeContextRejectsEmptyReason(): void
    {
        $this->expectException(InvalidArgumentException::class);
        $this->expectExceptionMessageMatches('/reason/i');

        new BreakglassContext(flag: true, reason: '');
    }

    #[Test]
    public function activeContextRejectsWhitespaceOnlyReason(): void
    {
        // A reason of "   " is functionally empty — accepting it would
        // let callers bypass the documentation requirement with a single
        // space. Trim before checking.
        $this->expectException(InvalidArgumentException::class);

        new BreakglassContext(flag: true, reason: "   \t\n");
    }
}
