<?php

declare(strict_types=1);

/**
 * Bootstrap class for the AgentForge Clinical Co-Pilot module.
 *
 * Wires module-level services (Twig, logger) and registers event listeners
 * with OpenEMR's event dispatcher. Instantiated by openemr.bootstrap.php at
 * module load time. Event subscriptions are added in subscribeToEvents().
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge;

use OpenEMR\BC\ServiceContainer;
use OpenEMR\Common\Twig\TwigContainer;
use OpenEMR\Core\Kernel;
use OpenEMR\Core\OEGlobalsBag;
use Psr\Log\LoggerInterface;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;
use Twig\Environment;

class Bootstrap
{
    public const MODULE_INSTALLATION_PATH = '/interface/modules/custom_modules/';
    public const MODULE_NAME = 'oe-module-agentforge';

    private readonly string $moduleDirectoryName;
    private readonly Environment $twig;
    private readonly LoggerInterface $logger;

    public function __construct(
        private readonly EventDispatcherInterface $eventDispatcher,
        ?Kernel $kernel = null,
        ?LoggerInterface $logger = null,
    ) {
        $kernel ??= OEGlobalsBag::getInstance()->getKernel();
        $this->twig = (new TwigContainer($this->getTemplatePath(), $kernel))->getTwig();
        $this->moduleDirectoryName = basename(dirname(__DIR__));
        $this->logger = $logger ?? ServiceContainer::getLogger();
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
