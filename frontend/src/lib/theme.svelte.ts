export const BASEMAP_KEY = 'rm.basemap.v3';
export const DEFAULT_BASEMAP = 'Dark Gray';
const DARK_BASEMAPS = new Set(['Dark Gray', 'Terrain (dark)']);

function savedBasemap(): string {
  try {
    return localStorage.getItem(BASEMAP_KEY) ?? DEFAULT_BASEMAP;
  } catch {
    return DEFAULT_BASEMAP;
  }
}

/** The page theme follows the map: dark basemaps switch the surrounding UI to dark. */
class Theme {
  dark = $state(DARK_BASEMAPS.has(savedBasemap()));
  setBasemap(name: string): void {
    this.dark = DARK_BASEMAPS.has(name);
  }
}

export const theme = new Theme();
