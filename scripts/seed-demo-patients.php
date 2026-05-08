<?php

declare(strict_types=1);

/**
 * One-shot CLI seeder for the W2 demo personas.
 *
 * Creates four minimal patient shells in OpenEMR matching the
 * fabricated intake forms in week2/example-documents/intake-forms/
 * (Chen / Whitaker / Reyes / Kowalski). Idempotent on the MRN
 * (``pubpid``): re-running skips already-seeded personas.
 *
 * Run inside the openemr container:
 *
 *   docker exec development-easy-openemr-1 php /tmp/seed-demo-patients.php
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

// phpcs:disable PSR1.Files.SideEffects.FoundWithSymbols

$_GET['site'] = 'default';
$ignoreAuth = true;

require '/var/www/localhost/htdocs/openemr/interface/globals.php';

use OpenEMR\Common\Uuid\UuidMapping;
use OpenEMR\Services\PatientService;

/** @var array<int, array<string, string>> $personas */
$personas = [
    [
        'fname' => 'Margaret',
        'mname' => 'L',
        'lname' => 'Chen',
        'DOB' => '1967-08-14',
        'sex' => 'Female',
        'pubpid' => 'MRN-2026-04481',
        'street' => '4421 Magnolia Ave Apt 3B',
        'city' => 'Berkeley',
        'state' => 'CA',
        'postal_code' => '94705',
        'phone_cell' => '5105550148',
        'email' => 'mchen.demo@example.test',
    ],
    [
        'fname' => 'James',
        'mname' => 'E',
        'lname' => 'Whitaker',
        'DOB' => '1958-11-03',
        'sex' => 'Male',
        'pubpid' => 'MRN-2026-04492',
        'street' => '812 Rio Grande Blvd NW',
        'city' => 'Albuquerque',
        'state' => 'NM',
        'postal_code' => '87107',
        'phone_cell' => '5055550193',
        'email' => 'jwhitaker.demo@example.test',
    ],
    [
        'fname' => 'Sofia',
        'lname' => 'Reyes',
        'DOB' => '1972-01-01',
        'sex' => 'Female',
        'pubpid' => 'MRN-2026-DEMO-03',
        'city' => 'Austin',
        'state' => 'TX',
    ],
    [
        'fname' => 'Robert',
        'lname' => 'Kowalski',
        'DOB' => '1965-01-01',
        'sex' => 'Male',
        'pubpid' => 'MRN-2026-DEMO-04',
        'city' => 'Chicago',
        'state' => 'IL',
    ],
];

$svc = new PatientService();

foreach ($personas as $p) {
    $existing = sqlQuery(
        'SELECT pid FROM patient_data WHERE pubpid = ? LIMIT 1',
        [$p['pubpid']],
    );
    if (is_array($existing) && isset($existing['pid'])) {
        echo "skip {$p['fname']} {$p['lname']} — already exists (pid {$existing['pid']})\n";
        continue;
    }

    $result = $svc->insert($p);
    if ($result->isValid()) {
        $data = $result->getData()[0];
        echo "created {$p['fname']} {$p['lname']} → pid {$data['pid']} · uuid {$data['uuid']}\n";
    } else {
        echo "FAILED {$p['fname']} {$p['lname']}: "
            . json_encode($result->getValidationMessages())
            . "\n";
    }
}

// Backfill the FHIR resource uuids so the new patients are reachable
// via /apis/default/fhir/Patient/<uuid>. Idempotent; safe to re-run.
echo "\nbackfilling uuid_mapping...\n";
echo UuidMapping::createAllMissingResourceUuids() . "\n";
echo "done\n";
