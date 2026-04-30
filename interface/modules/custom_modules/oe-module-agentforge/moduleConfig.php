<?php

declare(strict_types=1);

/**
 * Module configuration entry point. Called by OpenEMR's Module Manager when
 * a user opens this module's configuration page from Administration > Modules.
 *
 * Registers the namespace so configuration UI can autoload module classes
 * before installation has completed (required for pre-install setup screens).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

use OpenEMR\Core\ModulesClassLoader;
use OpenEMR\Core\OEGlobalsBag;

require_once dirname(__FILE__, 4) . '/globals.php';

$classLoader = new ModulesClassLoader(OEGlobalsBag::getInstance()->getProjectDir());
$classLoader->registerNamespaceIfNotExists(
    'OpenEMR\\Modules\\AgentForge\\',
    __DIR__ . DIRECTORY_SEPARATOR . 'src'
);

$module_config = 1;

exit;
