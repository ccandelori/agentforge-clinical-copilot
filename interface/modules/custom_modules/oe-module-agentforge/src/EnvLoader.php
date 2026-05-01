<?php

declare(strict_types=1);

/**
 * EnvLoader — loads the module's .env file into getenv() / $_ENV.
 *
 * The module's PHP entry points (turn.php, internal/*.php) read shared
 * secrets like AGENTFORGE_JWT_SECRET from getenv(). The Apache/PHP-FPM
 * environment in OpenEMR's dev-easy container doesn't propagate env
 * vars to web requests by default, so we keep a module-local .env that
 * the loader reads on every request. Production deployments can set
 * the same vars at the container/process level and skip this file.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge;

use Dotenv\Dotenv;

final class EnvLoader
{
    public static function load(): void
    {
        $moduleRoot = dirname(__DIR__);
        $envPath = $moduleRoot . DIRECTORY_SEPARATOR . '.env';
        if (!file_exists($envPath)) {
            return;
        }
        // `createImmutable` writes to $_ENV / $_SERVER only. We want
        // getenv() to see the values too, so use createMutable.
        Dotenv::createMutable($moduleRoot)->safeLoad();
    }
}
