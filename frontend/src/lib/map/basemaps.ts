// Shared by the dispatch map and the intake location map so both use the same keyless Esri basemaps.
// CARTO's free raster tiles now return an "API KEY REQUIRED" watermark, so they are not used here.
const esri = (service: string) => `https://server.arcgisonline.com/ArcGIS/rest/services/${service}/MapServer/tile/{z}/{y}/{x}`;
export const ESRI_ATTR = 'Tiles &copy; Esri and its data providers';

export const BASEMAPS = {
  night: { name: 'Night', base: esri('Canvas/World_Dark_Gray_Base'), labels: esri('Canvas/World_Dark_Gray_Reference'), attr: ESRI_ATTR, maxNative: 16 },
  satellite: { name: 'Satellite', base: esri('World_Imagery'), labels: esri('Reference/World_Boundaries_and_Places'), attr: ESRI_ATTR, maxNative: 19 },
  light: { name: 'Light', base: esri('Canvas/World_Light_Gray_Base'), labels: esri('Canvas/World_Light_Gray_Reference'), attr: ESRI_ATTR, maxNative: 16 }
} as const;
export type BasemapKey = keyof typeof BASEMAPS;

/** Web Mercator limits: tiles outside this do not exist and come back as "Map data not yet available". */
export const WORLD: [[number, number], [number, number]] = [[-85.05, -180], [85.05, 180]];

/** Default map center (the fictional county) used before any location is known. */
export const DEFAULT_CENTER: [number, number] = [47.6062, -122.3321];
