"""Regenerate backend/app/demo_incidents.json, the synthetic incidents the backend seeds in demo deployments.

    python scripts/seed_incidents.py

The backend loads that file itself whenever it starts with an empty store or its demo data is reset, when
SEED_INCIDENTS=true (set in compose.yml), so the incidents survive deploys, restarts and resets without this script.
Run it only after changing the list below, and commit the regenerated file.

Locations are real street addresses of commercial, industrial and office buildings in North Topeka (never
houses or apartments: the emergencies are made up). Each one is resolved with Photon, the same OpenStreetMap
geocoder the intake screen uses (frontend/src/lib/location/geocode.ts), and recorded the way the intake screen
saves it: the geocoder's point, "<address>, <region>" as the address, and the intake screen's heuristic precision
tier as geocodeConfidence (0.9 for an address-level match; see TIER in frontend/src/lib/location/state.ts). An
address that does not resolve to an address-level match within 25 km of Station 1 Central stops the script rather
than being saved with a made-up confidence. Like the intake screen, this sends the addresses to photon.komoot.io;
the deployed backend never does.
"""

import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "backend" / "app" / "demo_incidents.json"
CENTER = (39.100, -95.700)  # Station 1 Central in backend/app/seed_standin.py
MAX_KM = 25.0
REGION = "Topeka, KS"
PHOTON = "https://photon.komoot.io"
ADDRESS_TIER = 0.9  # TIER.address in frontend/src/lib/location/state.ts

# (narrative, street address, priority or None, intended category)
INCIDENTS = [
    ("Smoke and flames coming from the roof of a commercial building, employees say everyone got out.",
     "2525 Northwest Topeka Boulevard", "P1", "structure_fire"),
    ("Man collapsed at his desk, not breathing, a coworker is starting CPR.",
     "110 Northeast Lyman Road", "P1", "medical_cardiac"),
    ("Two-car collision on the street out front, one driver trapped and the car is leaking fluid.",
     "1900 Northwest Topeka Boulevard", "P1", "traffic_collision"),
    ("Strong chemical smell at the warehouse loading dock, two workers dizzy and coughing.",
     "2720 Northeast Grantville Road", "P1", "hazmat"),
    ("Grass fire spreading across the field behind the building toward a line of trees, wind is picking up.",
     "4055 Northwest Green Hills Road", "P2", "wildland_fire"),
    ("Pickup truck engine on fire in the parking lot, no one inside.",
     "2240 Northwest Tyler Street", "P2", "vehicle_fire"),
    ("Worker fell from a ladder about ten feet, conscious but bleeding from the head and can't move his leg.",
     "2201 Northeast Grantville Road", "P2", "medical_trauma"),
    ("Construction worker stuck in a collapsed trench beside the building, partially buried up to his waist.",
     "1741 Northeast 46th Street", "P1", "technical_rescue"),
    ("Couple screaming at each other in the parking lot, man shoved the woman against a car.",
     "2633 North Kansas Avenue", "P2", "domestic_disturbance"),
    ("Business owner arrived to find the side door pried open and tools missing, suspect may still be nearby.",
     "2406 Northwest Clay Street", "P3", "burglary"),
    ("Driver slumped over in a parked car for over an hour, not responding to knocks on the window.",
     "3617 Northwest 46th Street", "P3", "welfare_check"),
    ("Diabetic teenager confused and sweating in a store, mother says his blood sugar is very low.",
     "2050 Northwest Topeka Boulevard", "P2", "medical_other"),
    ("Man punched and knocked to the ground outside the business, assailant left on foot heading north.",
     "2125 North Kansas Avenue", "P2", "assault"),
    ("Fire in the kitchen area of a commercial building, staff used an extinguisher but smoke is still spreading.",
     "2600 Northeast Grantville Road", "P1", "structure_fire"),
]

_STREET_TYPES = {"rd": "road", "st": "street", "ave": "avenue", "blvd": "boulevard", "dr": "drive"}
_DIRECTIONS = {"n": "north", "s": "south", "e": "east", "w": "west", "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest"}


def km_from_center(lat: float, lon: float) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*CENTER, lat, lon))
    a = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _words(text: str) -> list[str]:
    return [_DIRECTIONS.get(w, _STREET_TYPES.get(w, w)) for w in text.lower().replace(",", " ").split()]


def geocode(street: str) -> tuple[float, float]:
    """The first Photon result, accepted only if it is the address-level match the intake screen would show as one."""
    query = f"{street}, {REGION}"
    url = f"{PHOTON}/api/?limit=6&lang=en&lat={CENTER[0]}&lon={CENTER[1]}&q={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": "dispatchz-seed-incidents"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        features = json.loads(resp.read()).get("features", [])
    if not features:
        raise LookupError(f"{query}: no geocoder result")
    p = features[0]["properties"]
    lon, lat = features[0]["geometry"]["coordinates"]
    # Same rule as photonKind() in geocode.ts: a numbered feature that is a bare house or has no name is an address.
    is_address = bool(p.get("housenumber")) and (p.get("osm_key") == "place" or not p.get("name"))
    matched = " ".join(_words(f"{p.get('housenumber', '')} {p.get('street', '')}"))
    if not is_address or matched != " ".join(_words(street)):
        raise LookupError(f"{query}: best match is {p.get('name') or matched!r}, not an address-level match")
    return lat, lon


def main() -> int:
    records = []
    for narrative, street, priority, category in INCIDENTS:
        try:
            lat, lon = geocode(street)
        except (LookupError, urllib.error.URLError) as exc:
            print(f"geocoding failed: {exc}", file=sys.stderr)
            return 1
        dist = km_from_center(lat, lon)
        if dist > MAX_KM:
            print(f"refusing {street}: {dist:.1f} km from Station 1 Central", file=sys.stderr)
            return 1
        records.append({
            "narrative": narrative, "category": category, "priority": priority,
            "location": {"address": f"{street}, {REGION}", "point": {"lat": lat, "lon": lon}, "geocodeConfidence": ADDRESS_TIER},
        })
        print(f"{dist:4.1f} km  {street}  {category}")
    OUT.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(records)} incidents to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
