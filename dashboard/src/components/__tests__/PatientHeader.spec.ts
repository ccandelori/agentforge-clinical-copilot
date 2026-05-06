import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import PatientHeader from '@/components/PatientHeader.vue'
import patientFixture from '@/__tests__/fixtures/patient-andrea.json'

const patient = patientFixture as unknown as fhir4.Patient

describe('<PatientHeader>', () => {
  beforeEach(() => {
    // Pin "now" so the age computation is deterministic regardless of
    // when the test runs.
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-06T12:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders the official name from HumanName.given + family', () => {
    const wrapper = mount(PatientHeader, { props: { patient } })
    expect(wrapper.text()).toContain('Andrea7 Latonya462 Schumm995')
  })

  it('renders DOB in a readable format and a computed age in years', () => {
    const wrapper = mount(PatientHeader, { props: { patient } })
    // Synthea fixture is 1957-09-11 → 68 y at the pinned 2026-05-06.
    expect(wrapper.text()).toMatch(/Sep\s*11,?\s*1957/)
    expect(wrapper.text()).toContain('68 y')
  })

  it('renders sex (gender) capitalized', () => {
    const wrapper = mount(PatientHeader, { props: { patient } })
    expect(wrapper.text()).toContain('Female')
  })

  it('picks the MR-coded identifier as the MRN', () => {
    const wrapper = mount(PatientHeader, { props: { patient } })
    expect(wrapper.text()).toContain('09003697-c566-9db0-ba1b-47925e36c460')
    // SSN must not bleed through.
    expect(wrapper.text()).not.toContain('999-36-3856')
  })

  it('does not render an Inactive badge when active is undefined (FHIR default)', () => {
    const wrapper = mount(PatientHeader, { props: { patient } })
    expect(wrapper.text()).not.toContain('Inactive')
  })

  it('shows the Inactive badge and strikes through the name when active=false', () => {
    const wrapper = mount(PatientHeader, {
      props: { patient: { ...patient, active: false } },
    })
    expect(wrapper.text()).toContain('Inactive')
    const heading = wrapper.find('h1')
    expect(heading.classes()).toContain('text-decoration-line-through')
  })

  it('falls back to em-dash when DOB is missing and omits the age', () => {
    const stripped: fhir4.Patient = { ...patient, birthDate: undefined }
    const wrapper = mount(PatientHeader, { props: { patient: stripped } })
    expect(wrapper.text()).toContain('DOB: —')
    expect(wrapper.text()).not.toContain('y)')
  })

  it('falls back to (unknown) when name is missing', () => {
    const stripped: fhir4.Patient = { ...patient, name: undefined }
    const wrapper = mount(PatientHeader, { props: { patient: stripped } })
    expect(wrapper.text()).toContain('(unknown)')
  })

  it('falls back to em-dash when no MR-coded identifier is present', () => {
    const ssnOnly: fhir4.Patient = {
      ...patient,
      identifier: [
        {
          type: { coding: [{ code: 'SS' }] },
          value: '999-99-9999',
        },
      ],
    }
    const wrapper = mount(PatientHeader, { props: { patient: ssnOnly } })
    expect(wrapper.text()).toContain('MRN:')
    expect(wrapper.text()).toContain('—')
  })

  it('prefers the official name over a maiden name', () => {
    // Synthea Andrea has both an official ("Schumm995") and maiden
    // ("Beier427") name. We pick the official one.
    const wrapper = mount(PatientHeader, { props: { patient } })
    expect(wrapper.text()).not.toContain('Beier427')
  })
})
