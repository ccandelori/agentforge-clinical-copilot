<?php

declare(strict_types=1);

/**
 * Bootstrap class for the AgentForge Clinical Co-Pilot module.
 *
 * The constructor accepts the EventDispatcher, Kernel, and Logger that
 * OpenEMR's module loader injects, but does not yet retain them — the
 * stored references are added in Task 2 when subscribeToEvents() begins
 * registering listeners. Per CLAUDE.md ("no half-finished
 * implementations"), we keep the dependency surface honest at this stage:
 * the class accepts what the contract requires and does what its current
 * scope allows, no more.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge;

use OpenEMR\Core\Kernel;
use OpenEMR\Core\OEGlobalsBag;
use Psr\Log\LoggerInterface;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;

class Bootstrap
{
    public const MODULE_INSTALLATION_PATH = '/interface/modules/custom_modules/';
    public const MODULE_NAME = 'oe-module-agentforge';

    private readonly string $moduleDirectoryName;

    public function __construct(
        EventDispatcherInterface $eventDispatcher,
        ?Kernel $kernel = null,
        ?LoggerInterface $logger = null,
    ) {
        // EventDispatcher, Kernel, and Logger get retained as readonly
        // properties when Task 2 wires up event subscriptions and template
        // rendering. Until then we accept them per OpenEMR's module-loader
        // contract but leave the storage off.
        unset($eventDispatcher, $kernel, $logger);

        $this->moduleDirectoryName = basename(dirname(__DIR__));
    }

    public function subscribeToEvents(): void
    {
        // Event subscriptions will be added in Task 2.
    }

    public function getTemplatePath(): string
    {
        return dirname(__DIR__) . DIRECTORY_SEPARATOR . 'templates' . DIRECTORY_SEPARATOR;
    }

    public function getURLPath(): string
    {
        return OEGlobalsBag::getInstance()->getWebRoot()
            . self::MODULE_INSTALLATION_PATH
            . $this->moduleDirectoryName
            . '/public/';
    }
}
