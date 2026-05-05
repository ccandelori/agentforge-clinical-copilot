<?php

/**
 * InternalIntakePersistController — sidecar-facing write endpoint that
 * persists an IntakeFormExtraction as a FHIR QuestionnaireResponse
 * against the canonical AgentForge intake-form Questionnaire seeded
 * by W2 Task 5.
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
use OpenEMR\Modules\AgentForge\Services\IntakeFormFhirMapper;
use OpenEMR\Modules\AgentForge\Services\IntakePersistAuditWriter;
use OpenEMR\Modules\AgentForge\Services\IntakeQuestionnaireLookup;
use OpenEMR\Modules\AgentForge\Services\IntakeQuestionnaireResponseWriter;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * The endpoint accepts a POST with a JSON body matching the Task 4
 * IntakeFormExtraction shape. Failure modes:
 *
 *   401 — missing / malformed / expired JWT
 *   400 — body isn't valid JSON; required fields missing or non-positive
 *   500 — canonical Questionnaire seed missing (Task 5 didn't run)
 *   403 — any leg of the JWT.patientId == request.patient_id ==
 *         documents[document_id].foreign_id triple-check fails
 *
 * The 403 path covers all four scoping mismatches (JWT vs request,
 * JWT vs document, request vs document, document missing/deleted).
 * All four collapse to the same response body — disclosing which
 * specific check failed would leak information about which patients
 * own which documents.
 *
 * On the 401/403/400/500 paths we DO NOT:
 *   - write a QuestionnaireResponse
 *   - fire the audit event
 *   - touch any structured EHR table
 *
 * The audit event is only fired on the successful 200 path so the
 * `agentforge.questionnaire_persist` event log corresponds 1:1 with
 * the rows actually inserted into questionnaire_response.
 *
 * The sidecar speaks JSON over HTTPS to this endpoint; CSRF is not a
 * concern (no browser session) and Authorization carries the JWT.
 */
class InternalIntakePersistController
{
    public function __construct(
        private readonly AgentJwtValidator $validator,
        private readonly DocumentOwnershipVerifier $ownershipVerifier,
        private readonly IntakeQuestionnaireLookup $questionnaireLookup,
        private readonly IntakeQuestionnaireResponseWriter $writer,
        private readonly IntakePersistAuditWriter $auditWriter,
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
        } catch (JwtException | RuntimeException $e) {
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

        // Triple-check leg 1: JWT vs request body.
        if ($claims->patientId !== $payloadPatientId) {
            return $this->forbidden();
        }

        // Triple-check leg 2 (also catches deleted/missing documents):
        // the document's recorded foreign_id (patient owner) must
        // also match. A null return collapses missing/deleted/null-
        // owner cases into the same 403 — see DocumentOwnershipVerifier.
        $documentOwner = $this->ownershipVerifier->findOwningPatientId($documentId);
        if ($documentOwner === null || $documentOwner !== $claims->patientId) {
            return $this->forbidden();
        }

        // Fail closed if the canonical Questionnaire isn't seeded.
        // Writing an unanchored QuestionnaireResponse would lose the
        // structural connection to the canonical schema.
        $seeded = $this->questionnaireLookup->findCanonicalQuestionnaire();
        if ($seeded === null) {
            return new JsonResponse(
                ['error' => 'Canonical intake Questionnaire is not seeded; cannot persist'],
                Response::HTTP_INTERNAL_SERVER_ERROR,
            );
        }

        $fhirResponse = IntakeFormFhirMapper::toQuestionnaireResponse(
            $body,
            IntakeQuestionnaireLookup::CANONICAL_URL,
        );

        try {
            $responseId = $this->writer->insert(
                patientId: $claims->patientId,
                questionnaireForeignId: $seeded->id,
                questionnaireName: $seeded->name,
                questionnaireResponse: $fhirResponse,
                questionnaireJson: $seeded->questionnaireJson,
            );
        } catch (DbalException | JsonException | RuntimeException $e) {
            // Narrow catch matches the writer's actual failure surface:
            // DBAL throws on DB errors, JsonException on encode failure,
            // RuntimeException from UuidRegistry. We deliberately do
            // NOT catch Exception/Throwable — Error subclasses are
            // programmer bugs that should propagate to the global
            // handler (per ForbiddenCatchTypeRule). CLAUDE.md: don't
            // expose getMessage() in user-facing output.
            return new JsonResponse(
                ['error' => 'Failed to persist QuestionnaireResponse'],
                Response::HTTP_INTERNAL_SERVER_ERROR,
            );
        }

        $extractionStatus = self::computeExtractionStatus($body);

        $this->auditWriter->record(
            userId: $claims->userId,
            patientId: $claims->patientId,
            questionnaireResponseId: $responseId,
            extractionStatus: $extractionStatus,
        );

        return new JsonResponse([
            'questionnaire_response_id' => $responseId,
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
