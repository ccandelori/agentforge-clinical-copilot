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

use OpenEMR\Events\Core\TwigEnvironmentEvent;
use OpenEMR\Events\PatientDemographics\RenderEvent as PatientDemographicsRenderEvent;
use OpenEMR\Modules\AgentForge\Bootstrap;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Symfony\Component\EventDispatcher\EventDispatcher;
use Twig\Environment;
use Twig\Loader\ArrayLoader;
use Twig\Loader\FilesystemLoader;

/**
 * Behavior tests for the AgentForge module Bootstrap.
 *
 * Tests focus on event-subscription wiring and handler behavior in
 * isolation from OpenEMR's runtime — Symfony's real EventDispatcher is
 * used directly so we verify what listeners actually get registered, not
 * what mocks claim was called.
 */
final class BootstrapTest extends TestCase
{
    #[Test]
    public function subscribeToEventsRegistersTwigEnvironmentCreatedListener(): void
    {
        $dispatcher = new EventDispatcher();
        $bootstrap = new Bootstrap($dispatcher);

        $bootstrap->subscribeToEvents();

        $listeners = $dispatcher->getListeners(TwigEnvironmentEvent::EVENT_CREATED);
        self::assertCount(1, $listeners);
        self::assertSame([$bootstrap, 'addTemplateOverrideLoader'], $listeners[0]);
    }

    #[Test]
    public function subscribeToEventsRegistersPatientDemographicsSectionListAfterListener(): void
    {
        $dispatcher = new EventDispatcher();
        $bootstrap = new Bootstrap($dispatcher);

        $bootstrap->subscribeToEvents();

        $listeners = $dispatcher->getListeners(
            PatientDemographicsRenderEvent::EVENT_SECTION_LIST_RENDER_AFTER
        );
        self::assertCount(1, $listeners);
        self::assertSame([$bootstrap, 'renderAgentPanel'], $listeners[0]);
    }

    #[Test]
    public function addTemplateOverrideLoaderPrependsModulePathToFilesystemLoader(): void
    {
        $existingPath = sys_get_temp_dir();
        $loader = new FilesystemLoader([$existingPath]);
        $event = new TwigEnvironmentEvent(new Environment($loader));
        $bootstrap = new Bootstrap(new EventDispatcher());

        $bootstrap->addTemplateOverrideLoader($event);

        // FilesystemLoader stores paths without a trailing separator.
        $expected = rtrim($bootstrap->getTemplatePath(), DIRECTORY_SEPARATOR);
        self::assertSame($expected, $loader->getPaths()[0]);
        self::assertSame(rtrim($existingPath, DIRECTORY_SEPARATOR), $loader->getPaths()[1]);
    }

    #[Test]
    public function addTemplateOverrideLoaderIsNoOpForNonFilesystemLoader(): void
    {
        $loader = new ArrayLoader(['template.twig' => 'unchanged']);
        $event = new TwigEnvironmentEvent(new Environment($loader));
        $bootstrap = new Bootstrap(new EventDispatcher());

        // Should complete without throwing; loader is unchanged.
        $bootstrap->addTemplateOverrideLoader($event);

        self::assertTrue($loader->exists('template.twig'));
    }
}
