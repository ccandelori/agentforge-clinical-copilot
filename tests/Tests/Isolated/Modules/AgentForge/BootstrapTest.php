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
}
