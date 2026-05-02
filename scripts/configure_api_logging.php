<?php

/**
 * Sets OpenEMR's `api_log_option` global to "minimal" (1), suppressing
 * REST request/response body logging into the `api_log` table.
 *
 * IMPORTANT — context for this script (worth the read before running it):
 *
 *   * `api_log_option` is a SITE-WIDE global (`globals.gl_name =
 *     'api_log_option'`), not a per-user column. OpenEMR's REST listener
 *     (`ApiResponseLoggerListener`) reads it from the globals bag.
 *   * AgentForge's sidecar→PHP internal endpoints under
 *     `interface/modules/custom_modules/oe-module-agentforge/public/internal/`
 *     use bare Symfony Requests, NOT `HttpRestRequest`, so the listener
 *     never fires for those calls. There is no AgentForge-bound body
 *     logging in `api_log` today.
 *   * This script therefore acts as defense-in-depth for any FUTURE
 *     AgentForge calls that DO route through the REST stack, plus a
 *     general "minimize PHI in api_log" hygiene step. It does not fix
 *     a present-day leak.
 *
 * Idempotent — safe to re-run. Reports the prior value so you can tell
 * whether the change was a no-op.
 *
 * Usage (from inside the OpenEMR docker container or on a host with the
 * codebase mounted at the standard `/var/www/.../openemr` path):
 *
 *   php scripts/configure_api_logging.php           # set + report
 *   php scripts/configure_api_logging.php --check   # report only
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

if (php_sapi_name() !== 'cli') {
    fwrite(STDERR, "configure_api_logging.php must be run from the CLI.\n");
    exit(1);
}

$checkOnly = in_array('--check', $argv, true);

// Bootstrap OpenEMR. CLI scripts must seed `$_GET['site']` before
// requiring globals.php (matches the contrib/util convention) and
// must set HTTP_HOST so globals' URL-validation pass doesn't bail.
$_GET['site'] = $_GET['site'] ?? 'default';
$_SERVER['HTTP_HOST'] = $_SERVER['HTTP_HOST'] ?? 'localhost';
$ignoreAuth = true;
require_once __DIR__ . '/../interface/globals.php';

$desired = '1';

$current = sqlQuery(
    "SELECT gl_value FROM globals WHERE gl_name = ? AND gl_index = ?",
    ['api_log_option', 0]
);

$priorValue = is_array($current) && isset($current['gl_value'])
    ? (string) $current['gl_value']
    : null;

if ($priorValue === null) {
    echo "[api_log_option] no row in globals; current behaviour follows the\n";
    echo "                 default (full logging, value=2).\n";
} else {
    echo "[api_log_option] current value: {$priorValue}\n";
}

if ($checkOnly) {
    echo "[--check] not modifying globals.\n";
    exit(0);
}

if ($priorValue === $desired) {
    echo "[api_log_option] already at desired value '{$desired}'; no change.\n";
    exit(0);
}

if ($priorValue === null) {
    sqlStatement(
        "INSERT INTO globals (gl_name, gl_index, gl_value) VALUES (?, ?, ?)",
        ['api_log_option', 0, $desired]
    );
    echo "[api_log_option] inserted globals row with value '{$desired}'.\n";
} else {
    sqlStatement(
        "UPDATE globals SET gl_value = ? WHERE gl_name = ? AND gl_index = ?",
        [$desired, 'api_log_option', 0]
    );
    echo "[api_log_option] updated from '{$priorValue}' to '{$desired}'.\n";
}

echo "Done. Restart OpenEMR (apache reload) so the globals bag picks up the change.\n";
