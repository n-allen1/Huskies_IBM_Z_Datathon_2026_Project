// Shared by the dispatch map and the intake location map so both use the same keyless Esri basemaps.
// CARTO's free raster tiles now return an "API KEY REQUIRED" watermark, so they are not used here.
const esri = (service: string) => `https://server.arcgisonline.com/ArcGIS/rest/services/${service}/MapServer/tile/{z}/{y}/{x}`;
// Credits are the text Esri publishes in each service's `copyrightText` (checked 2026-10-17), and must stay visible while
// the layer is shown. Each layer carries its own credit because label overlays have their own data providers
// (the place labels draw on OpenStreetMap data); Leaflet merges identical credits into one line.
const link = (href: string, text: string) => `<a href="${href}" target="_blank" rel="noopener noreferrer">${text}</a>`;
const ESRI = link('https://www.esri.com', 'Esri');
const OSM = link('https://www.openstreetmap.org/copyright', 'OpenStreetMap');
const CANVAS_CREDIT = `${ESRI}, HERE, Garmin, &copy; ${OSM} contributors, and the GIS user community`;
const IMAGERY_CREDIT = `Source: ${ESRI}, Vantor, Earthstar Geographics, and the GIS User Community`;

export const BASEMAPS = {
  night: { name: 'Night', base: esri('Canvas/World_Dark_Gray_Base'), labels: esri('Canvas/World_Dark_Gray_Reference'), attr: CANVAS_CREDIT, labelsAttr: CANVAS_CREDIT, maxNative: 16 },
  satellite: { name: 'Satellite', base: esri('World_Imagery'), labels: esri('Reference/World_Boundaries_and_Places'), attr: IMAGERY_CREDIT, labelsAttr: CANVAS_CREDIT, maxNative: 19 },
  light: { name: 'Light', base: esri('Canvas/World_Light_Gray_Base'), labels: esri('Canvas/World_Light_Gray_Reference'), attr: CANVAS_CREDIT, labelsAttr: CANVAS_CREDIT, maxNative: 16 }
} as const;
export type BasemapKey = keyof typeof BASEMAPS;

/** Web Mercator limits: tiles outside this do not exist and come back as "Map data not yet available". */
export const WORLD: [[number, number], [number, number]] = [[-85.05, -180], [85.05, 180]];

/** Default map center (the fictional county) used before any location is known. */
export const DEFAULT_CENTER: [number, number] = [47.6062, -122.3321];
