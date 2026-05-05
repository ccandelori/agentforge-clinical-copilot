<?php

/**
 * DocumentIngestAuditWriter — fires the `agentforge.document_ingest`
 * event-log entry when a clinician uploads a PDF (lab result or intake
 * form) through the AgentForge browser-upload endpoint (Task 6).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

use Doctrine\DBAL\Connection;
use OpenEMR\Common\Logging\EventAuditLogger;

/**
 * Mirrors :class:`IntakePersistAuditWriter` and
 * :class:`LabPersistAuditWriter` for the upload flow. The audit row
 * records who (the active OpenEMR session's user_id, looked up to a
 * username), for which patient (the SESSION-derived patient_id, NOT
 * any payload-supplied value), what kind of document went in
 * (``doc_type`` is the closed two-element set ``"lab_pdf"`` /
 * ``"intake_form"``), and the new ``document_id`` for downstream
 * traceability. Breakglass context, when present, lands in the
 * comments string so the audit-log encryption pipeline covers it.
 *
 * Failure paths do NOT call here. The controller wires this only on
 * the successful-upload code path so the audit log is always exactly
 * one entry per successful upload (no orphaned events on 401/403/400,
 * no double-events on retries).
 */
readonly class DocumentIngestAuditWriter
{
    public const EVENT_NAME = 'agentforge.document_ingest';

    public function __construct(
        private Connection $connection,
        private EventAuditLogger $auditLogger,
    ) {
    }

    public function record(
        int $userId,
        int $patientId,
        int $documentId,
        string $docType,
        bool $breakglassFlag,
        ?string $breakglassReason,
    ): void {
        $username = $this->lookupUsername($userId);
        $comments = $this->buildComments(
            documentId: $documentId,
            docType: $docType,
            breakglassFlag: $breakglassFlag,
            breakglassReason: $breakglassReason,
        );

        $this->auditLogger->newEvent(
            self::EVENT_NAME,
            $username,
            '',
            1,
            $comments,
            $patientId,
        );
    }

    private function buildComments(
        int $documentId,
        string $docType,
        bool $breakglassFlag,
        ?string $breakglassReason,
    ): string {
        $parts = [
            sprintf('AgentForge document upload: document_id=%d', $documentId),
            sprintf('doc_type=%s', $docType),
        ];
        if ($breakglassFlag) {
            // Reason text rides with the audit row's encrypted comments
            // so the breakglass justification is recoverable. Empty
            // reason gets a literal placeholder rather than being
            // dropped — silently dropping a breakglass marker would
            // remove a load-bearing trail.
            $reason = ($breakglassReason !== null && $breakglassReason !== '')
                ? $breakglassReason
                : '(no reason provided)';
            $parts[] = sprintf('breakglass_reason=%s', $reason);
        }
        return implode(', ', $parts);
    }

    private function lookupUsername(int $userId): string
    {
        $row = $this->connection->fetchOne(
            'SELECT username FROM users WHERE id = ?',
            [$userId],
        );

        if (is_string($row) && $row !== '') {
            return $row;
        }

        // Mirror BreakglassAuditWriter's fallback: never let a missing
        // users row block the audit write itself.
        return "user-{$userId}";
    }
}
