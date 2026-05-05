<?php

/**
 * InternalLabPersistController — sidecar-facing write endpoint that
 * persists a LabPdfExtraction as a procedure_order/report/result
 * cascade. Companion to InternalIntakePersistController; same auth +
 * triple-check shape, different write target.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Controllers;

use Doctrine\DBAL\Exception as DbalException;
use JsonException;
use Lcobucci\JWT\Exception as JwtException;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\DocumentOwnershipVerifier;
use OpenEMR\Modules\AgentForge\Services\LabPersistAuditWriter;
use OpenEMR\Modules\AgentForge\Services\LabResultWriter;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Failure modes mirror InternalIntakePersistController:
 *
 *   401 — missing / malformed / expired JWT
 *   400 — body isn't valid JSON; required fields missing or non-positive;
 *         empty values list (a blank lab PDF is valid as a Pydantic
 *         shape, but persisting zero rows would create a dangling
 *         procedure_order/report — fail at the controller)
 *   403 — any leg of the JWT.patientId == request.patient_id ==
 *         documents[document_id].foreign_id triple-check fails
 *   500 — DB write cascade failure
 *
 * On all failure paths we DO NOT write any procedure_* rows or fire
 * the audit event. The DBAL transaction in LabResultWriter is what
 * keeps the all-or-nothing invariant on the write side; the controller
 * is what keeps the all-or-nothing invariant on the audit side.
 */
class InternalLabPersistController
{
    public function __construct(
        private readonly AgentJwtValidator $validator,
        private readonly DocumentOwnershipVerifier $ownershipVerifier,
        private readonly LabResultWriter $writer,
        private readonly LabPersistAuditWriter $auditWriter,
    ) {
    }

    public function persist(Request $request): Response
    {
        $authHeader = $request->headers->get('Authorization');
        if ($authHeader === null || $authHeader === '') {
            return new JsonResponse(
                ['error' => 'Authorization header is required'],
                Response::HTTP_UNAUTHORIZED,
            );
        }

        try {
            $claims = $this->validator->validateBearer($authHeader);
        } catch (JwtException | RuntimeException) {
            return new JsonResponse(
                ['error' => 'Invalid or expired token'],
                Response::HTTP_UNAUTHORIZED,
            );
        }

        $body = $this->decodeBody($request);
        if (!is_array($body)) {
            return new JsonResponse(
                ['error' => 'Request body must be a JSON object'],
                Response::HTTP_BAD_REQUEST,
            );
        }

        $documentId = self::positiveInt($body['document_id'] ?? null);
        $payloadPatientId = self::positiveInt($body['patient_id'] ?? null);
        if ($documentId === null || $payloadPatientId === null) {
            return new JsonResponse(
                ['error' => 'document_id and patient_id are required and must be positive integers'],
                Response::HTTP_BAD_REQUEST,
            );
        }

        $values = $body['values'] ?? null;
        if (!is_array($values) || count($values) === 0) {
            return new JsonResponse(
                ['error' => 'values array is required and must be non-empty'],
                Response::HTTP_BAD_REQUEST,
            );
        }

        // Triple-check leg 1: JWT vs request payload.
        if ($claims->patientId !== $payloadPatientId) {
            return $this->forbidden();
        }

        // Triple-check leg 2: document ownership.
        $documentOwner = $this->ownershipVerifier->findOwningPatientId($documentId);
        if ($documentOwner === null || $documentOwner !== $claims->patientId) {
            return $this->forbidden();
        }

        $orderingProvider = self::optString($body['ordering_provider'] ?? null);
        $accessionNumber = self::optString($body['accession_number'] ?? null);

        try {
            $ids = $this->writer->persist(
                patientId: $claims->patientId,
                userId: $claims->userId,
                documentId: $documentId,
                orderingProvider: $orderingProvider,
                accessionNumber: $accessionNumber,
                values: self::normalizeValues($values),
            );
        } catch (DbalException | JsonException | RuntimeException) {
            // Same narrow catch as the intake controller — Error
            // subclasses propagate to the global handler.
            return new JsonResponse(
                ['error' => 'Failed to persist lab results'],
                Response::HTTP_INTERNAL_SERVER_ERROR,
            );
        }

        $extractionStatus = self::computeExtractionStatus($body);

        $this->auditWriter->record(
            userId: $claims->userId,
            patientId: $claims->patientId,
            procedureResultIds: $ids->procedureResultIds,
            extractionStatus: $extractionStatus,
        );

        return new JsonResponse([
            'procedure_order_id' => $ids->procedureOrderId,
            'procedure_report_id' => $ids->procedureReportId,
            'procedure_result_ids' => $ids->procedureResultIds,
            'patient_id' => $claims->patientId,
            'extraction_status' => $extractionStatus,
        ], Response::HTTP_CREATED);
    }

    private function forbidden(): JsonResponse
    {
        return new JsonResponse(
            ['error' => 'Patient scope check failed'],
            Response::HTTP_FORBIDDEN,
        );
    }

    /**
     * @return array<array-key, mixed>|null
     */
    private function decodeBody(Request $request): ?array
    {
        $raw = (string) $request->getContent();
        if ($raw === '') {
            return null;
        }
        try {
            $decoded = json_decode($raw, true, flags: JSON_THROW_ON_ERROR);
        } catch (\JsonException) {
            return null;
        }
        return is_array($decoded) ? $decoded : null;
    }

    private static function positiveInt(mixed $value): ?int
    {
        if (is_int($value) && $value > 0) {
            return $value;
        }
        if (is_string($value) && ctype_digit($value)) {
            $parsed = (int) $value;
            return $parsed > 0 ? $parsed : null;
        }
        return null;
    }

    private static function optString(mixed $value): ?string
    {
        return is_string($value) && $value !== '' ? $value : null;
    }

    /**
     * Coerce the values list into list<array<string, mixed>> for the
     * writer. Anything that isn't a dict is dropped — the writer expects
     * to read named keys like 'test_name' off each entry.
     *
     * @param array<array-key, mixed> $values
     * @return list<array<string, mixed>>
     */
    private static function normalizeValues(array $values): array
    {
        $out = [];
        foreach ($values as $v) {
            if (!is_array($v)) {
                continue;
            }
            // Filter to string keys so the writer's @param contract holds.
            $row = [];
            foreach ($v as $k => $val) {
                if (is_string($k)) {
                    $row[$k] = $val;
                }
            }
            $out[] = $row;
        }
        return $out;
    }

    /**
     * @param array<array-key, mixed> $body
     */
    private static function computeExtractionStatus(array $body): string
    {
        $unsupported = $body['unsupported_fields'] ?? null;
        if (is_array($unsupported) && count($unsupported) > 0) {
            return 'partial';
        }
        return 'completed';
    }
}
