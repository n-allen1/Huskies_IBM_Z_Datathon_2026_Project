import type { components } from './schema';

type S = components['schemas'];
export type Incident = S['Incident'];
export type IncidentCreate = S['IncidentCreate'];
export type IncidentCategory = S['IncidentCategory'];
export type Classification = S['Classification'];
export type RequirementSet = S['RequirementSet'];
export type Recommendation = S['Recommendation'];
export type Candidate = S['Candidate'];
export type CapabilityCheck = S['CapabilityCheck'];
export type Unit = S['Unit'];
export type Facility = S['Facility'];
export type EventEnvelope = S['EventEnvelope'];
export type ApiError = S['Error'];
export type Provenance = S['Provenance'];
