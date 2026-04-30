<?php

declare(strict_types=1);

/**
 * Bootstrap class for the AgentForge Clinical Co-Pilot module.
 *
 * Wires module-level event listeners into OpenEMR's event dispatcher.
 * Two listeners are registered: one to give our templates directory
 * priority over the default Twig FilesystemLoader paths, and one to
 * render the agent chat panel inside the patient demographics section
 * list. Both handlers begin life as stubs and gain behavior in
 * subsequent subtasks (2.3 and 2.4).
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
use OpenEMR\Events\Core\TwigEnvironmentEvent;
use OpenEMR\Events\PatientDemographics\RenderEvent as PatientDemographicsRenderEvent;
use Psr\Log\LoggerInterface;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;
use Twig\Loader\FilesystemLoader;

class Bootstrap
{
    public const MODULE_INSTALLATION_PATH = '/interface/modules/custom_modules/';
    public const MODULE_NAME = 'oe-module-agentforge';

    private readonly string $moduleDirectoryName;

    public function __construct(
        private readonly EventDispatcherInterface $eventDispatcher,
        ?Kernel $kernel = null,
        ?LoggerInterface $logger = null,
    ) {
        // Kernel and Logger get retained as readonly properties when the
        // handler implementations in subtasks 2.3 / 2.4 actually need them.
        unset($kernel, $logger);

        $this->moduleDirectoryName = basename(dirname(__DIR__));
    }

    public function subscribeToEvents(): void
    {
        $this->eventDispatcher->addListener(
            TwigEnvironmentEvent::EVENT_CREATED,
            [$this, 'addTemplateOverrideLoader']
        );
        $this->eventDispatcher->addListener(
            PatientDemographicsRenderEvent::EVENT_SECTION_LIST_RENDER_AFTER,
            [$this, 'renderAgentPanel']
        );
    }

    public function addTemplateOverrideLoader(TwigEnvironmentEvent $event): void
    {
        // Prepend our templates dir so module overrides win against the
        // default OpenEMR Twig paths. Non-Filesystem loaders (ArrayLoader,
        // ChainLoader, etc.) are left alone.
        $loader = $event->getTwigEnvironment()->getLoader();
        if ($loader instanceof FilesystemLoader) {
            $loader->prependPath($this->getTemplatePath());
        }
    }

    public function renderAgentPanel(PatientDemographicsRenderEvent $event): void
    {
        // Implementation lands in subtask 2.4.
        unset($event);
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
