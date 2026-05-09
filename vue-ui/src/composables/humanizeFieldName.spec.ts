import { describe, expect, it } from 'vitest'

import { humanizeFieldName } from './humanizeFieldName'

describe('humanizeFieldName', () => {
    it('returns an empty string when given an empty string', () => {
        expect(humanizeFieldName('')).toBe('')
    })

    it('returns empty when input is only whitespace or only underscores', () => {
        expect(humanizeFieldName('   ')).toBe('')
        expect(humanizeFieldName('___')).toBe('')
    })

    it('capitalizes a single word', () => {
        expect(humanizeFieldName('phone')).toBe('Phone')
        expect(humanizeFieldName('PHONE')).toBe('Phone')
    })

    it('title-cases each token in a snake_case multi-word name', () => {
        expect(humanizeFieldName('chief_concern')).toBe('Chief Concern')
        expect(humanizeFieldName('medical_history')).toBe('Medical History')
    })

    it('preserves lowercase glue words in interior positions', () => {
        expect(humanizeFieldName('date_of_birth')).toBe('Date of Birth')
        expect(humanizeFieldName('cause_of_death')).toBe('Cause of Death')
    })

    it('still capitalizes a glue word when it leads the field name', () => {
        expect(humanizeFieldName('of_age')).toBe('Of Age')
    })

    it('upper-cases recognised acronyms in the map', () => {
        expect(humanizeFieldName('mrn')).toBe('MRN')
        expect(humanizeFieldName('dob')).toBe('DOB')
        expect(humanizeFieldName('npi')).toBe('NPI')
        expect(humanizeFieldName('ssn')).toBe('SSN')
    })

    it('upper-cases an acronym embedded inside a multi-token name', () => {
        expect(humanizeFieldName('patient_mrn')).toBe('Patient MRN')
        expect(humanizeFieldName('provider_npi_number')).toBe('Provider NPI Number')
    })

    it('tolerates leading and trailing underscores', () => {
        expect(humanizeFieldName('_chief_concern')).toBe('Chief Concern')
        expect(humanizeFieldName('chief_concern_')).toBe('Chief Concern')
        expect(humanizeFieldName('_dob_')).toBe('DOB')
    })

    it('collapses multiple consecutive underscores', () => {
        expect(humanizeFieldName('chief__concern')).toBe('Chief Concern')
        expect(humanizeFieldName('date___of___birth')).toBe('Date of Birth')
    })

    it('trims surrounding whitespace before splitting', () => {
        expect(humanizeFieldName('  chief_concern  ')).toBe('Chief Concern')
    })
})
