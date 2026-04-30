<?php

declare(strict_types=1);

/**
 * Bootstrap entry point for the AgentForge Clinical Co-Pilot module.
 *
 * Registers the module's PSR-4 namespace and wires the Bootstrap class
 * into OpenEMR's event dispatcher. Called automatically by OpenEMR's
 * ModulesApplication during module initialization.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge;

use OpenEMR\Core\ModulesClassLoader;
use OpenEMR\Core\OEGlobalsBag;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;

/**
 * @global ModulesClassLoader $classLoader Injected by OpenEMR's ModulesApplication
 * @global EventDispatcherInterface $eventDispatcher Injected by OpenEMR's ModulesApplication
 */

assert(isset($classLoader) && $classLoader instanceof ModulesClassLoader);
$classLoader->registerNamespaceIfNotExists(
    'OpenEMR\\Modules\\AgentForge\\',
    __DIR__ . DIRECTORY_SEPARATOR . 'src'
);

assert(isset($eventDispatcher) && $eventDispatcher instanceof EventDispatcherInterface);
$bootstrap = new Bootstrap($eventDispatcher, OEGlobalsBag::getInstance()->getKernel());
$bootstrap->subscribeToEvents();
