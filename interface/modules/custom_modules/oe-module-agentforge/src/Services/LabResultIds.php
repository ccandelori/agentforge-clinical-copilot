<?php

/**
 * Value object returned by LabResultWriter — IDs assigned across the
 * procedure_order/report/result cascade so the caller can include them
 * in the persistence response and the audit event.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

final readonly class LabResultIds
{
    /**
     * @param list<int> $procedureResultIds One per LabValue in the extraction.
     */
    public function __construct(
        public int $procedureOrderId,
        public int $procedureReportId,
        public array $procedureResultIds,
    ) {
    }
}
