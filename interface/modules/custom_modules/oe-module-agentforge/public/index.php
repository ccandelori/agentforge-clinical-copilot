<?php

declare(strict_types=1);

/**
 * Public entry point for the AgentForge Clinical Co-Pilot module.
 *
 * Placeholder for module public pages. The module's live entry points
 * are the sidecar-facing JWT-authed handlers under `public/internal/`;
 * this file exists so the module's public/ surface is reachable and
 * CSRF-checked from day one.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

require_once dirname(__FILE__, 5) . '/globals.php';

use OpenEMR\Common\Csrf\CsrfUtils;

CsrfUtils::checkCsrfInput(INPUT_GET, dieOnFail: true);

echo 'AgentForge Module';
