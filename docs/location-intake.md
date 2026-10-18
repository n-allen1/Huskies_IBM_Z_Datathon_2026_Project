# Incident location entry: UI behavior, API gaps, backend dependencies

Status: UI implemented against the **currently approved** `contracts/openapi.json`. Nothing here changes the contract.
The proposals in section 3 need explicit human approval before anyone edits the contract or the generator.

## 1. What the approved contract allows today

| Need | Contract today | Consequence |
|---|---|---|
| Create incident from an address | `POST /incidents` requires `location.point` (`lat`, `lon`, non-null). There is no geocoding endpoint. | The browser must supply coordinates. |
| Unknown / pending location | `point` is required and non-null. | **Cannot be saved.** The UI shows it as a labeled placeholder and sends nothing. |
| Correct location after creation | `PATCH /incidents/{id}` accepts `location` and `version` (409 on conflict). | **Supported.** Implemented. |
| Dispatcher's original wording | `Location.address` (max 300 chars). | The dispatcher's text (plus apartment/floor details) is saved there. |
| Source / reliability of a position | Only `Location.geocodeConfidence` (0..1). No source, precision, accuracy radius or status field. | Heuristic tiers are sent (below). Manual placement omits the field. |

`geocodeConfidence` values sent by the UI are **precision tiers, not provider scores**: address 0.9, named place 0.7
(landmark entries are capped at 0.7), street only 0.4, area only 0.2. Dispatcher-placed markers and external feeds send no value.
The dispatch screen reads the same field back: >= 0.8 "Address-level match", 0.5-0.8 "Approximate: place",
< 0.5 "Approximate: low confidence", absent "Source not recorded".

## 2. Workflow implemented

Intake (`/intake`): describe the incident, then give the location by one of:

- **Street address**: typeahead matches as you type (4+ characters), apartment/floor/suite field, editable search area.
- **Landmark / area**: businesses, parks, highways, descriptive places. Always shown as approximate.
- **Pick on map**: click, or pan with the keyboard and press "Place marker at map center"; drag to adjust.
- **Not yet known**: UI placeholder only (see section 1). Save stays disabled with the reason.

Rules enforced in `src/lib/location/state.ts` (unit tested):

- Several matches are never auto-selected: the dispatcher must choose, and Save stays disabled until then.
- Every state shows a reliability badge (verified / approximate / ambiguous / unresolved / not yet determined) plus
  "Caller reported" versus "Map position", and a warning when the matched address reads differently from the caller's wording.
- A failed or empty lookup keeps everything typed and offers Retry or "Pick on map"; the draft also survives a reload (tab storage).
- Coordinates are never shown as form fields and never invented; no position means nothing is sent.
- A location update from another source is held as "incoming" with Use / Keep current. It never replaces a
  dispatcher-confirmed or dispatcher-placed location. No automated source is wired up and none is claimed.

Dispatch (`/dispatch`): the details panel has a Location section (address, reliability, no coordinates) and
"Correct location...", which reuses the same picker, shows the saved position as a dashed reference ring, and sends `PATCH` with the incident `version`.
Incidents with low-confidence positions get a dashed ring on the map. A correction marks an existing recommendation outdated.

## 3. Proposed contract changes (need human approval)

1. **Optional `point` on create/update when location is pending.**
   `POST /incidents`, `PATCH /incidents/{id}`: allow `location` without `point` (and an explicit "pending" state), so incidents can be
   documented before the location is known. Likely needs separate request and response `Location` shapes, because responses can then
   have no point. Non-breaking for existing clients that send `point`. Incidents without a point stay in the queue and are simply not drawn.
2. **Server-side resolution.** Either accept free text (`address`, `kind`: address | landmark | intersection) and return the resolved
   `point`, or add a resolve operation returning ranked candidates (`id`, label, detail, `point`, kind, confidence), so the browser no
   longer calls third-party geocoders directly. Define behavior for ambiguous and unresolved results.
3. **Location provenance fields** on `Location`: `source` (dispatcher_map | geocoder | caller_reported | external feed name),
   `precision` or `accuracyMeters` (enables a real uncertainty circle), `reportedText` (original wording, separate from the normalized
   `address`), `details` (apartment/floor/suite), and `confirmedByDispatcher`.
4. **Location update event** (for example `incident.location_updated`, payload: source, previous and new location) and a rule that
   automated updates never overwrite a dispatcher-confirmed location server-side.
5. **Street intersections** are not offered in the UI (decision: removed). If wanted later, server-side resolution (item 2) is the
   right place; browser-side lookup was too slow and unreliable.

## 4. Backend dependencies

- A geocoding/resolution service (item 2) replaces `src/lib/location/geocode.ts`; the UI consumes `Candidate[]` and does not change.
- Until then the browser sends what the dispatcher types to **Photon (OpenStreetMap)** for lookup and uses Esri tiles for the map.
  That is acceptable for synthetic demo data only; real incidents must not leave the system.
- Wireless caller location needs an authorized integration. Nothing in this UI implements or claims it.

## 5. Verification (browser, live geocoder, fixture server)

Address (single match, verified), ambiguous address (6 candidates, none preselected, Save disabled until chosen), landmark
(approximate, capped confidence), no match (unresolved, text kept, Save disabled), unknown (placeholder, forced submit sends nothing),
lookup failure (error, Retry recovers, work kept), reload (draft restored), manual placement (no confidence sent), and
post-creation correction via `PATCH`. Request bodies were checked against the contract shape. Unit tests cover the state
rules, including incoming-update protection. axe-core reports no violations on the new screens.
