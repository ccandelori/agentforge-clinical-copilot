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

use OpenEMR\Modules\AgentForge\Http\AuthHeaderBridge;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Tests the case-insensitive Authorization header pick used by the
 * Apache header bridge.
 *
 * HTTP/1.1 (RFC 7230 §3.2) declares header field names case-insensitive,
 * and apache_request_headers() does NOT normalize them — depending on
 * Apache version, mod_php vs PHP-FPM, and the proxy chain in front, the
 * header surfaces as 'Authorization', 'authorization', or
 * 'AUTHORIZATION'. A case-sensitive check 401s a valid request.
 *
 * The bridge wrapper is hard to unit-test in CLI (apache_request_headers
 * isn't defined off Apache); the case-insensitive pick is extracted as
 * a pure helper so we can lock the matching behavior here without
 * mocking PHP built-ins.
 */
final class AuthHeaderBridgeTest extends TestCase
{
    /**
     * @return array<string, array{array<array-key, mixed>, ?string}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function headerCasingProvider(): array
    {
        return [
            'canonical Authorization' => [
                ['Authorization' => 'Bearer abc'],
                'Bearer abc',
            ],
            'all-lowercase authorization' => [
                ['authorization' => 'Bearer xyz'],
                'Bearer xyz',
            ],
            'all-uppercase AUTHORIZATION' => [
                ['AUTHORIZATION' => 'Bearer 1'],
                'Bearer 1',
            ],
            'mixed-case AuthOrIzAtIon' => [
                ['AuthOrIzAtIon' => 'Bearer 2'],
                'Bearer 2',
            ],
            'header absent' => [
                ['Content-Type' => 'application/json'],
                null,
            ],
            'empty array' => [
                [],
                null,
            ],
            'non-string value is rejected' => [
                ['Authorization' => ['Bearer wrong-shape']],
                null,
            ],
            'numeric-key entry skipped' => [
                [42 => 'noise', 'authorization' => 'Bearer ok'],
                'Bearer ok',
            ],
            'multiple cases — first match wins' => [
                ['Authorization' => 'first', 'authorization' => 'second'],
                'first',
            ],
        ];
    }

    /**
     * @param array<array-key, mixed> $headers
     */
    #[Test]
    #[DataProvider('headerCasingProvider')]
    public function picksAuthorizationRegardlessOfCase(array $headers, ?string $expected): void
    {
        self::assertSame($expected, AuthHeaderBridge::pickAuthorizationCaseInsensitively($headers));
    }
}
