<?php

/**
 * InternalIntakePromoteController — clinician-approved write path that
 * lands AI-extracted intake-form rows in OpenEMR's structured
 * ``lists`` table after a per-row review on the dashboard.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Controllers;

use Doctrine\DBAL\Connection;
use Doctrine\DBAL\Exception as DbalException;
use JsonException;
use Lcobucci\JWT\Exception as JwtException;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\IntakePromoteAuditWriter;
use OpenEMR\Modules\AgentForge\Services\IntakePromotionWriter;
use OpenEMR\Modules\AgentForge\Services\PromotionItem;
use OpenEMR\Modules\AgentForge\Services\PromotionResult;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * The endpoint accepts a POST with a JSON body of accepted items the
 * clinician approved on the dashboard's extraction-review panel.
 *
 * Request shape::
 *
 *   {
 *     "patient_id": <int>,
 *     "questionnaire_response_id": "<uuid>"|null,
 *     "document_id": <int>|null,
 *     "items": [
 *       {"kind": "allergy",        "title": "Penicillin", "details": "rash"},
 *       {"kind": "medical_problem","title": "Type 2 diabetes"},
 *       {"kind": "medication",     "title": "Metformin",  "details": "500mg / bid"},
 *       {"kind": "family_history", "title": "Mother: hypertension"},
 *       ...
 *     ]
 *   }
 *
 * Failure modes:
 *
 *   401 — missing / malformed / expired JWT
 *   400 — body isn't valid JSON; required fields missing; items list
 *         empty; any item has an invalid ``kind`` or empty ``title``
 *   403 — JWT.patientId != request body's patient_id
 *   500 — DB write failed (whole batch rolled back)
 *
 * Why we don't re-fetch the QR by id and project items server-side:
 *
 * The intake extraction the user just reviewed was their own
 * approval surface — the items that arrive here are the ones the
 * clinician explicitly checked. Re-fetching the QR to project items
 * would add a round-trip without changing what gets written: the
 * substance / drug name / condition strings would still come from
 * the extraction, just laundered through one more layer. The safety
 * property is the per-row checkbox + explicit Commit click, not
 * server-side QR re-projection.
 *
 * The QR id is still accepted (and logged) so the audit trail can
 * walk back from a chart row's comments ("qr_id=…") to the upstream
 * extraction-time audit event.
 *
 * Triple-check is collapsed to a double-check here because the
 * promote endpoint is not document-scoped: a clinician may approve
 * items from one extraction without that extraction's source
 * document still being attached. JWT.patientId vs request.patient_id
 * is the load-bearing scope check; the writer's username comes from
 * the JWT's user_id so the ``lists.user`` column reflects the
 * approver, not the extraction's authoring AI.
 */
class InternalIntakePromoteController
{
    /**
     * Closed set of accepted ``kind`` values, mirroring the four
     * intake-form section types the worker emits. Adding a new kind
     * is a coordinated change with the writer's class constants and
     * the dashboard's checkbox group.
     */
    private const ALLOWED_KINDS = [
        IntakePromotionWriter::TYPE_ALLERGY,
        IntakePromotionWriter::TYPE_PROBLEM,
        IntakePromotionWriter::TYPE_MEDICATION,
        IntakePromotionWriter::TYPE_FAMILY_HISTORY,
    ];

    /**
     * Item-count cap. Defense in depth: a 100-row promote request is
     * already pathological for an intake form (no reasonable form
     * has that many entries). Past the cap we 400 — the dashboard's
     * Commit button only sends rows the clinician actually checked,
     * so hitting this means a programmer error, not a user error.
     */
    private const MAX_ITEMS = 100;

    /** ``lists.title`` is varchar(255); titles longer than this are clamped. */
    private const MAX_TITLE_LEN = 255;

    /** Soft cap on ``details`` so ``lists.comments`` doesn't grow unbounded. */
    private const MAX_DETAILS_LEN = 1024;

    public function __construct(
        private readonly AgentJwtValidator $validator,
        private readonly Connection $connection,
        private readonly IntakePromotionWriter $writer,
        private readonly IntakePromoteAuditWriter $auditWriter,
    ) {
    }

    public function promote(Request $request): Response
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

        $payloadPatientId = self::positiveInt($body['patient_id'] ?? null);
        if ($payloadPatientId === null) {
            return new JsonResponse(
                ['error' => 'patient_id is required and must be a positive integer'],
                Response::HTTP_BAD_REQUEST,
            );
        }

        // Scope check — JWT vs request body.
        if ($claims->patientId !== $payloadPatientId) {
            return new JsonResponse(
                ['error' => 'Patient scope check failed'],
                Response::HTTP_FORBIDDEN,
            );
        }

        $rawItems = $body['items'] ?? null;
        if (!is_array($rawItems) || count($rawItems) === 0) {
            return new JsonResponse(
                ['error' => 'items must be a non-empty array'],
                Response::HTTP_BAD_REQUEST,
            );
        }
        if (count($rawItems) > self::MAX_ITEMS) {
            return new JsonResponse(
                ['error' => 'items exceeds maximum batch size'],
                Response::HTTP_BAD_REQUEST,
            );
        }

        $parsedItems = [];
        foreach ($rawItems as $idx => $rawItem) {
            $parsed = self::parseItem($rawItem);
            if ($parsed === null) {
                return new JsonResponse(
                    [
                        'error' => 'invalid item at index ' . (int) $idx,
                        'allowed_kinds' => self::ALLOWED_KINDS,
                    ],
                    Response::HTTP_BAD_REQUEST,
                );
            }
            $parsedItems[] = $parsed;
        }

        $questionnaireResponseId = self::nonEmptyString(
            $body['questionnaire_response_id'] ?? null,
        );
        $documentId = self::positiveInt($body['document_id'] ?? null);

        $username = $this->lookupUsername($claims->userId);

        try {
            $result = $this->writer->persist(
                patientId: $claims->patientId,
                username: $username,
                questionnaireResponseId: $questionnaireResponseId,
                documentId: $documentId,
                items: $parsedItems,
            );
        } catch (DbalException | JsonException | RuntimeException) {
            // Narrow catch matches the writer's actual failure surface:
            // DBAL throws on DB errors, RuntimeException from the
            // writer's lastInsertId guard. Error subclasses are
            // programmer bugs that should propagate to the global
            // handler. CLAUDE.md: don't expose getMessage() in
            // user-facing output.
            return new JsonResponse(
                ['error' => 'Failed to commit selected items to chart'],
                Response::HTTP_INTERNAL_SERVER_ERROR,
            );
        }

        $this->auditWriter->record(
            userId: $claims->userId,
            patientId: $claims->patientId,
            questionnaireResponseId: $questionnaireResponseId,
            promotedCount: count($result->handles),
        );

        return new JsonResponse(
            self::serialise($result),
            Response::HTTP_CREATED,
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
        } catch (JsonException) {
            return null;
        }
        return is_array($decoded) ? $decoded : null;
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

        // Same fallback as IntakePersistAuditWriter — a missing users
        // row should not block the write; the audit log still
        // captures the JWT-claimed user_id via the lookup it does
        // separately.
        return "user-{$userId}";
    }

    private static function parseItem(mixed $raw): ?PromotionItem
    {
        if (!is_array($raw)) {
            return null;
        }
        $kind = $raw['kind'] ?? null;
        if (!is_string($kind) || !in_array($kind, self::ALLOWED_KINDS, true)) {
            return null;
        }
        $title = $raw['title'] ?? null;
        if (!is_string($title)) {
            return null;
        }
        $title = trim($title);
        if ($title === '') {
            return null;
        }
        if (strlen($title) > self::MAX_TITLE_LEN) {
            $title = substr($title, 0, self::MAX_TITLE_LEN);
        }
        $detailsRaw = $raw['details'] ?? null;
        $details = null;
        if (is_string($detailsRaw)) {
            $details = trim($detailsRaw);
            if ($details === '') {
                $details = null;
            } elseif (strlen($details) > self::MAX_DETAILS_LEN) {
                $details = substr($details, 0, self::MAX_DETAILS_LEN);
            }
        }
        return new PromotionItem(kind: $kind, title: $title, details: $details);
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

    private static function nonEmptyString(mixed $value): ?string
    {
        if (is_string($value) && $value !== '') {
            return $value;
        }
        return null;
    }

    /**
     * @return array{promoted: list<array{kind: string, lists_id: int, title: string}>, count: int}
     */
    private static function serialise(PromotionResult $result): array
    {
        $promoted = [];
        foreach ($result->handles as $handle) {
            $promoted[] = [
                'kind' => $handle->kind,
                'lists_id' => $handle->listsId,
                'title' => $handle->title,
            ];
        }
        return [
            'promoted' => $promoted,
            'count' => count($promoted),
        ];
    }
}
