<?php

declare(strict_types=1);

/**
 * Bootstrap entry point for the AgentForge Clinical Co-Pilot module.
 *
 * Registers the module's PSR-4 namespace so the sidecar-facing
 * Internal* controllers under `public/internal/*.php` autoload. Called
 * automatically by OpenEMR's ModulesApplication during module
 * initialization.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge;

use OpenEMR\Core\ModulesClassLoader;

/**
 * @global ModulesClassLoader $classLoader Injected by OpenEMR's ModulesApplication
 */

assert(isset($classLoader) && $classLoader instanceof ModulesClassLoader);
$classLoader->registerNamespaceIfNotExists(
    'OpenEMR\\Modules\\AgentForge\\',
    __DIR__ . DIRECTORY_SEPARATOR . 'src'
);
