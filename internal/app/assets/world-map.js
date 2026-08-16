(() => {
  'use strict';

  const canvas = document.getElementById('world-map');
  if (!canvas) return;
  const context = canvas.getContext('2d', { alpha: false });
  const status = document.getElementById('map-status');
  if (!context) {
    status.textContent = 'This browser cannot create the map canvas.';
    return;
  }

  const WORLD_RADIUS = 12_288;
  const WORLD_DIAMETER = WORLD_RADIUS * 2;
  const PLAYABLE_RADIUS = 10_500;
  const FIT_WORLD_DIAMETER = 22_500;
  const MIN_SCALE = 0.012;
  const MAX_SCALE = 128;
  const ZOOM_STEP = 1.6;
  const WHEEL_RATE = 0.001;
  const DRAG_THRESHOLD = 3;
  const KEYBOARD_PAN_PIXELS = 80;
  const HIT_RADIUS_PIXELS = 14;
  const DEVICE_PIXEL_RATIO_CAP = 2;
  const TERRAIN_TEXTURE_RESOLUTION = 1_536;
  const TERRAIN_PATTERN_TILE = 36;
  const ZONE_SIZE = 64;
  // The playable grid: 10,000 m of world divided into 64 m zones. Anything past this is the game's
  // far-away bookkeeping, not a place.
  const MAX_ZONE_INDEX = 164;
  const ZONE_DETAIL_SCALE = 0.18;
  const COVERAGE_VISIBLE_SCALE = 0.35;
  const COVERAGE_FULL_SCALE = 0.8;
  const CLOSE_TERRAIN_DETAIL_SCALE = 0.55;
  const MAX_TERRAIN_DETAIL_MARKS = 9_000;
  const OBJECT_INDEX_CELL = 256;
  const MAX_ZONE_INSPECTIONS = 50_000;
  const MAX_ZONE_MARKS = 8_000;
  const MAX_LOCATION_INSPECTIONS = 20_000;
  const MAX_LOCATION_GLYPHS = 2_000;
  const MAX_CLUSTER_GLYPHS = 1_500;
  const MAX_OBJECT_GLYPHS_OVERVIEW = 1_500;
  const MAX_OBJECT_GLYPHS_CLOSE = 4_000;
  const rootStyle = getComputedStyle(document.documentElement);
  const monoFont = rootStyle.getPropertyValue('--site-mono').trim();

  function token(name) {
    const value = rootStyle.getPropertyValue(name).trim();
    if (!value) throw new Error(`Missing map color token ${name}`);
    return value;
  }


  // One colour per builder, chosen from the creator id so it is stable across reloads and worlds.
  // Every cluster used to be drawn in one colour, so a map could show that somebody had built
  // something and never that it was two different people - which is how an operator ends up
  // wondering whether a stranger has been on the server.
  const BUILDER_COLOURS = [
    '#6f9ad6', '#71c492', '#d9a514', '#c46f9a', '#7ad6cf', '#d6a06f', '#a58fd6', '#8fd66f'
  ];

  function builderStyle(creator) {
    return (window.__builderStyles || {})[String(creator)] || null;
  }

  function builderColour(creator) {
    // The page decides, including for the unattributed pile: a legend swatch and the cell it
    // describes have to be the same colour or the legend is lying about the map.
    const style = builderStyle(creator || 0);
    if (style && style.colour) return style.colour;
    if (!creator) return colors.build;
    // A small stable hash: the id is a 64-bit number arriving as a JS double, so fold it.
    const n = Math.abs(Number(creator) % 100000);
    return BUILDER_COLOURS[n % BUILDER_COLOURS.length];
  }

  function builderName(creator) {
    if (!creator) return 'unattributed';
    const style = builderStyle(creator);
    if (style && style.name) return style.name;
    return 'builder ' + String(Math.abs(Number(creator) % 10000)).padStart(4, '0');
  }
  const colors = {
    canvas: token('--map-canvas'),
    grid: token('--map-grid'),
    worldEdge: token('--map-world-edge'),
    halo: token('--map-marker-halo'),
    zone: token('--map-zone'),
    location: token('--map-location'),
    locationBoss: token('--map-location-boss'),
    locationSpawn: token('--map-location-spawn'),
    locationSpawnAccent: token('--map-location-spawn-accent'),
    locationTrader: token('--map-location-trader'),
    locationDungeon: token('--map-location-dungeon'),
    locationFortress: token('--map-location-fortress'),
    locationSettlement: token('--map-location-settlement'),
    locationResource: token('--map-location-resource'),
    locationLandmark: token('--map-location-landmark'),
    locationOther: token('--map-location-other'),
    locationPending: token('--map-location-pending'),
    build: token('--map-build'),
    portal: token('--map-portal'),
    container: token('--map-container'),
    production: token('--map-production'),
    creature: token('--map-creature'),
    risk: token('--map-risk'),
    other: token('--map-other'),
  };

  const biomes = [
    { id: 'void', label: 'Beyond world', source: 0x000000, pattern: 'none' },
    { id: 'ocean', label: 'Ocean', source: 0x000099, pattern: 'waves' },
    { id: 'deep-north', label: 'Deep North', source: 0x6666ff, pattern: 'ice' },
    { id: 'meadows', label: 'Meadows', source: 0x91a75b, pattern: 'grass' },
    { id: 'black-forest', label: 'Black Forest', source: 0x345e3b, pattern: 'trees' },
    { id: 'swamp', label: 'Swamp', source: 0x525252, pattern: 'reeds' },
    { id: 'mountain', label: 'Mountain', source: 0xffffff, pattern: 'contours' },
    { id: 'plains', label: 'Plains', source: 0xc7c731, pattern: 'stalks' },
    { id: 'mistlands', label: 'Mistlands', source: 0xa37157, pattern: 'mist' },
    { id: 'ashlands', label: 'Ashlands', source: 0xff0000, pattern: 'cracks' },
  ];
  const biomeBySource = new Map(biomes.map((biome, index) => [biome.source, index]));
  const biomeTextureColors = biomes.map((biome) => token(`--map-biome-${biome.id}-texture`));
  const objectLayers = ['terrain-risk', 'container', 'production', 'creature', 'other', 'portal'];
  const world = document.body.dataset.world;
  // The same renderer serves the operator's map and the players' map. Only two things differ: where
  // the data comes from, and whether ground nobody has visited is covered over.
  const dataBase = document.body.dataset.mapBase || `/admin/worlds/${encodeURIComponent(world)}`;
  const fogOfWar = document.body.dataset.mapFog === '1';
  let fogCanvas = null;
  // The players' own revealed map, unpacked from base64 once when the analysis arrives.
  let maskBits = null;
  const details = document.getElementById('details');
  const coords = document.getElementById('coords');
  const health = document.getElementById('health');
  const diffBox = document.getElementById('diff');
  const recommendations = document.getElementById('recommendations');
  const fitControl = document.getElementById('fit');
  const zoomInControl = document.getElementById('zoom-in');
  const zoomOutControl = document.getElementById('zoom-out');
  const zoomLevel = document.getElementById('zoom-level');
  const coverageNote = document.getElementById('coverage-note');
  const categorySummary = document.getElementById('category-summary');
  const state = {
    data: null,
    scale: 0.035,
    fitScale: 0.035,
    x: 0,
    z: 0,
    drag: null,
    layers: {},
    locationCategories: {},
    objectIndexes: new Map(),
    coverageIndex: new Map(),
    overlayCache: new Map(),
    overlaySnapshot: null,
    overlayViewKey: '',
    frame: 0,
  };
  let terrainSurface = null;
  let terrainBiomeIndices = null;
  let terrainSourceWidth = 0;
  let terrainSourceHeight = 0;
  let terrainFailed = false;
  let terrainManifest = null;
  const terrainTileCache = new Map();

  document.querySelectorAll('[data-layer]').forEach((input) => {
    state.layers[input.dataset.layer] = input.checked;
    input.addEventListener('change', () => {
      state.layers[input.dataset.layer] = input.checked;
      requestDraw();
    });
  });
  const locationControls = [...document.querySelectorAll('[data-location-category]')];
  for (const input of locationControls) {
    state.locationCategories[input.dataset.locationCategory] = input.checked;
    input.addEventListener('change', () => {
      state.locationCategories[input.dataset.locationCategory] = input.checked;
      requestDraw();
    });
  }
  const locationLayerControl = document.querySelector('[data-layer="locations"]');
  const syncLocationControls = () => {
    for (const input of locationControls) input.disabled = !locationLayerControl || !locationLayerControl.checked;
  };
  if (locationLayerControl) locationLayerControl.addEventListener('change', syncLocationControls);
  syncLocationControls();
  const terrainControl = document.querySelector('[data-layer="terrain"]');
  if (terrainControl) {
    const reference = document.createElement('small');
    reference.className = 'muted';
    reference.textContent = 'Terrain tiles become available after analysis';
    terrainControl.nextElementSibling.appendChild(reference);
  }
  fitControl.addEventListener('click', fit);
  zoomInControl.addEventListener('click', () => zoomAt(state.scale * ZOOM_STEP));
  zoomOutControl.addEventListener('click', () => zoomAt(state.scale / ZOOM_STEP));

  const terrainImage = new Image();
  terrainImage.decoding = 'async';
  terrainImage.addEventListener('load', () => {
    try {
      prepareTerrain();
    } catch (error) {
      terrainFailed = true;
      if (state.data) {
        status.hidden = false;
        status.textContent = `Analysis loaded, but terrain detail is unavailable: ${error.message}`;
      }
    }
    requestDraw();
  });
  terrainImage.addEventListener('error', () => {
    terrainFailed = true;
    if (state.data) {
      status.hidden = false;
      status.textContent = 'Analysis loaded, but the embedded terrain reference is unavailable.';
    }
  });
  const terrainManifestReady = loadTerrainManifest();

  async function loadTerrainManifest() {
    try {
      const response = await fetch(`${dataBase}/map/manifest.json`, { cache: 'no-cache' });
      if (response.status === 404) return;
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const manifest = await response.json();
      if (manifest.schema !== 1 || manifest.width < 12288 || manifest.height < 12288 || manifest.tile_size !== 512) {
        throw new Error('invalid terrain tile manifest');
      }
      terrainManifest = manifest;
      const reference = terrainControl?.nextElementSibling?.querySelector('small');
      if (reference) reference.textContent = `${manifest.width.toLocaleString()} × ${manifest.height.toLocaleString()} backend terrain pyramid`;
      requestDraw();
    } catch (error) {
      console.error('terrain tile manifest unavailable', error);
    }
  }

  function terrainTile(level, x, y) {
    const key = `${terrainManifest.key}/${level.zoom}/${x}/${y}`;
    let entry = terrainTileCache.get(key);
    if (entry) return entry;
    const image = new Image();
    entry = { image, ready: false, failed: false };
    terrainTileCache.set(key, entry);
    image.decoding = 'async';
    image.addEventListener('load', () => {
      entry.ready = true;
      requestDraw();
    }, { once: true });
    image.addEventListener('error', () => {
      entry.failed = true;
      requestDraw();
    }, { once: true });
    image.src = `${dataBase}/map/tiles/${terrainManifest.key}/${level.zoom}/${x}/${y}.png`;
    return entry;
  }

  function preferredTerrainZoom() {
    const requiredPixels = WORLD_DIAMETER * state.scale * 1.5;
    for (const level of terrainManifest.levels) {
      if (level.width >= requiredPixels) return level.zoom;
    }
    return terrainManifest.max_zoom;
  }

  function drawTerrainTiles() {
    if (!terrainManifest) return false;
    const requestedZoom = preferredTerrainZoom();
    const bounds = visibleBounds(2);
    const [left, top] = screen(-WORLD_RADIUS, WORLD_RADIUS);
    const [, centerY] = screen(0, 0);
    let drew = false;
    context.save();
    context.translate(0, centerY * 2);
    context.scale(1, -1);
    for (let zoom = 0; zoom <= requestedZoom; zoom += 1) {
      const level = terrainManifest.levels[zoom];
      const pixelsPerWorld = level.width / WORLD_DIAMETER;
      const minTileX = Math.max(0, Math.floor((bounds.minX + WORLD_RADIUS) * pixelsPerWorld / terrainManifest.tile_size));
      const maxTileX = Math.min(level.tiles_wide - 1, Math.floor((bounds.maxX + WORLD_RADIUS) * pixelsPerWorld / terrainManifest.tile_size));
      const minTileY = Math.max(0, Math.floor((bounds.minZ + WORLD_RADIUS) * pixelsPerWorld / terrainManifest.tile_size));
      const maxTileY = Math.min(level.tiles_high - 1, Math.floor((bounds.maxZ + WORLD_RADIUS) * pixelsPerWorld / terrainManifest.tile_size));
      const pixelScreenSize = WORLD_DIAMETER / level.width * state.scale;
      for (let y = minTileY; y <= maxTileY; y += 1) {
        for (let x = minTileX; x <= maxTileX; x += 1) {
          const entry = terrainTile(level, x, y);
          if (!entry.ready) continue;
          context.imageSmoothingEnabled = pixelScreenSize < 2;
          context.imageSmoothingQuality = 'high';
          context.drawImage(
            entry.image,
            left + x * terrainManifest.tile_size * pixelScreenSize,
            top + y * terrainManifest.tile_size * pixelScreenSize,
            entry.image.naturalWidth * pixelScreenSize,
            entry.image.naturalHeight * pixelScreenSize,
          );
          drew = true;
        }
      }
    }
    context.restore();
    return drew;
  }
  function overlayTile(level, x, y) {
    const source = currentSnapshot()?.source?.sha256;
    if (!source) return Promise.resolve(null);
    const key = `${source}/${level.zoom}/${x}/${y}`;
    let pending = state.overlayCache.get(key);
    if (pending) return pending;
    pending = fetch(`${dataBase}/map/overlays/${source}/${level.zoom}/${x}/${y}.json`, {
      cache: 'no-cache',
      credentials: 'same-origin',
    }).then((response) => {
      if (!response.ok) throw new Error(`overlay ${response.status}`);
      return response.json();
    }).catch((error) => {
      state.overlayCache.delete(key);
      console.error('overlay tile unavailable', error);
      return null;
    });
    state.overlayCache.set(key, pending);
    return pending;
  }

  function refreshOverlayView() {
    if (!terrainManifest || !state.data) return;
    const level = terrainManifest.levels[preferredTerrainZoom()];
    const bounds = visibleBounds(24);
    const pixelsPerWorld = level.width / WORLD_DIAMETER;
    const minX = Math.max(0, Math.floor((bounds.minX + WORLD_RADIUS) * pixelsPerWorld / terrainManifest.tile_size));
    const maxX = Math.min(level.tiles_wide - 1, Math.floor((bounds.maxX + WORLD_RADIUS) * pixelsPerWorld / terrainManifest.tile_size));
    const minY = Math.max(0, Math.floor((bounds.minZ + WORLD_RADIUS) * pixelsPerWorld / terrainManifest.tile_size));
    const maxY = Math.min(level.tiles_high - 1, Math.floor((bounds.maxZ + WORLD_RADIUS) * pixelsPerWorld / terrainManifest.tile_size));
    const coordinates = [];
    for (let y = minY; y <= maxY; y += 1) {
      for (let x = minX; x <= maxX; x += 1) coordinates.push([x, y]);
    }
    const viewKey = `${level.zoom}:${minX}:${maxX}:${minY}:${maxY}`;
    if (viewKey === state.overlayViewKey) return;
    state.overlayViewKey = viewKey;
    Promise.all(coordinates.map(([x, y]) => overlayTile(level, x, y))).then((tiles) => {
      if (state.overlayViewKey !== viewKey) return;
      state.overlaySnapshot = mergeOverlayTiles(tiles.filter(Boolean));
      buildIndexes(state.overlaySnapshot);
      requestDraw();
    });
  }

  function mergeOverlayTiles(tiles) {
    const snapshot = {
      ...state.data.snapshot,
      generated_zones: [],
      locations: [],
      clusters: [],
      construction_coverage: null,
      objects: [],
    };
    const zones = new Set();
    const locations = new Set();
    const clusters = new Set();
    const objects = new Set();
    const coverage = new Map();
    let coverageMetadata = null;
    for (const tile of tiles) {
      for (const zone of tile.generated_zones || []) {
        const key = `${zone.x}:${zone.y}`;
        if (!zones.has(key)) { zones.add(key); snapshot.generated_zones.push(zone); }
      }
      for (const location of tile.locations || []) {
        const key = `${location.name}:${location.position.x}:${location.position.z}`;
        if (!locations.has(key)) { locations.add(key); snapshot.locations.push(location); }
      }
      for (const cluster of tile.clusters || []) {
        if (!clusters.has(cluster.id)) { clusters.add(cluster.id); snapshot.clusters.push(cluster); }
      }
      if (tile.construction_coverage) {
        coverageMetadata = tile.construction_coverage;
        for (const cell of tile.construction_coverage.cells || []) coverage.set(`${cell.x}:${cell.z}`, cell);
      }
      const exactObjects = tile.objects || [];
      const aggregateObjects = (tile.markers || []).map((marker, index) => ({
        id: `aggregate-${tile.zoom}-${tile.x}-${tile.y}-${index}`,
        category: marker.category,
        position: marker.position,
        aggregate: true,
        aggregate_count: marker.count,
      }));
      for (const object of [...exactObjects, ...aggregateObjects]) {
        const key = String(object.id);
        if (!objects.has(key)) { objects.add(key); snapshot.objects.push(object); }
      }
    }
    for (const location of state.data.snapshot.locations || []) {
      if (!isSpawnLocation(location)) continue;
      const key = `${location.name}:${location.position.x}:${location.position.z}`;
      if (!locations.has(key)) { locations.add(key); snapshot.locations.push(location); }
    }
    if (coverageMetadata) {
      snapshot.construction_coverage = { ...coverageMetadata, cells: [...coverage.values()] };
    }
    return snapshot;
  }

  function currentSnapshot() {
    return state.overlaySnapshot || state.data.snapshot;
  }

  function rgbToken(name) {
    const value = token(name);
    const match = /^#([0-9a-f]{6})$/i.exec(value);
    if (!match) throw new Error(`Map pixel token ${name} must use six-digit hexadecimal`);
    const packed = Number.parseInt(match[1], 16);
    return [packed >> 16, packed >> 8 & 255, packed & 255];
  }

  function prepareTerrain() {
    const sourceCanvas = document.createElement('canvas');
    sourceCanvas.width = terrainImage.naturalWidth;
    sourceCanvas.height = terrainImage.naturalHeight;
    const sourceContext = sourceCanvas.getContext('2d', { willReadFrequently: true });
    sourceContext.drawImage(terrainImage, 0, 0);
    const sourceImage = sourceContext.getImageData(0, 0, sourceCanvas.width, sourceCanvas.height);
    const mappedCanvas = document.createElement('canvas');
    mappedCanvas.width = sourceCanvas.width;
    mappedCanvas.height = sourceCanvas.height;
    const mappedContext = mappedCanvas.getContext('2d');
    const mappedImage = mappedContext.createImageData(mappedCanvas.width, mappedCanvas.height);
    const biomeColors = biomes.map((biome) => rgbToken(`--map-biome-${biome.id}`));
    terrainBiomeIndices = new Uint8Array(sourceCanvas.width * sourceCanvas.height);
    terrainSourceWidth = sourceCanvas.width;
    terrainSourceHeight = sourceCanvas.height;

    for (let pixel = 0; pixel < terrainBiomeIndices.length; pixel += 1) {
      const offset = pixel * 4;
      const source = sourceImage.data[offset] << 16 | sourceImage.data[offset + 1] << 8 | sourceImage.data[offset + 2];
      const biomeIndex = biomeBySource.get(source) ?? 0;
      const mapped = biomeColors[biomeIndex];
      terrainBiomeIndices[pixel] = biomeIndex;
      mappedImage.data[offset] = mapped[0];
      mappedImage.data[offset + 1] = mapped[1];
      mappedImage.data[offset + 2] = mapped[2];
      mappedImage.data[offset + 3] = 255;
    }
    mappedContext.putImageData(mappedImage, 0, 0);

    terrainSurface = document.createElement('canvas');
    terrainSurface.width = TERRAIN_TEXTURE_RESOLUTION;
    terrainSurface.height = TERRAIN_TEXTURE_RESOLUTION;
    const terrainContext = terrainSurface.getContext('2d');
    terrainContext.imageSmoothingEnabled = true;
    terrainContext.imageSmoothingQuality = 'high';
    terrainContext.filter = 'blur(1.5px)';
    terrainContext.drawImage(mappedCanvas, 0, 0, terrainSurface.width, terrainSurface.height);
    terrainContext.filter = 'none';
    terrainContext.globalAlpha = 0.78;
    terrainContext.drawImage(mappedCanvas, 0, 0, terrainSurface.width, terrainSurface.height);
    terrainContext.globalAlpha = 1;

    const maskCanvas = document.createElement('canvas');
    maskCanvas.width = sourceCanvas.width;
    maskCanvas.height = sourceCanvas.height;
    const maskContext = maskCanvas.getContext('2d');
    const maskImage = maskContext.createImageData(maskCanvas.width, maskCanvas.height);
    const patternLayer = document.createElement('canvas');
    patternLayer.width = terrainSurface.width;
    patternLayer.height = terrainSurface.height;
    const layerContext = patternLayer.getContext('2d');

    for (let biomeIndex = 0; biomeIndex < biomes.length; biomeIndex += 1) {
      const biome = biomes[biomeIndex];
      if (biome.pattern === 'none') continue;
      maskImage.data.fill(0);
      for (let pixel = 0; pixel < terrainBiomeIndices.length; pixel += 1) {
        if (terrainBiomeIndices[pixel] === biomeIndex) maskImage.data[pixel * 4 + 3] = 255;
      }
      maskContext.putImageData(maskImage, 0, 0);
      layerContext.clearRect(0, 0, patternLayer.width, patternLayer.height);
      layerContext.imageSmoothingEnabled = true;
      layerContext.imageSmoothingQuality = 'high';
      layerContext.filter = 'blur(1.5px)';
      layerContext.drawImage(maskCanvas, 0, 0, patternLayer.width, patternLayer.height);
      layerContext.filter = 'none';
      layerContext.globalCompositeOperation = 'source-in';
      layerContext.fillStyle = layerContext.createPattern(createBiomePattern(biome, biomeIndex), 'repeat');
      layerContext.fillRect(0, 0, patternLayer.width, patternLayer.height);
      layerContext.globalCompositeOperation = 'source-over';
      terrainContext.globalAlpha = 0.34;
      terrainContext.drawImage(patternLayer, 0, 0);
    }
    terrainContext.globalAlpha = 1;
  }

  function createBiomePattern(biome, seed) {
    const tile = document.createElement('canvas');
    tile.width = TERRAIN_PATTERN_TILE;
    tile.height = TERRAIN_PATTERN_TILE;
    const patternContext = tile.getContext('2d');
    const quarter = TERRAIN_PATTERN_TILE / 4;
    const half = TERRAIN_PATTERN_TILE / 2;
    patternContext.strokeStyle = token(`--map-biome-${biome.id}-texture`);
    patternContext.fillStyle = patternContext.strokeStyle;
    patternContext.lineWidth = 1.25;
    patternContext.lineCap = 'round';
    patternContext.lineJoin = 'round';

    if (biome.pattern === 'waves') {
      for (const y of [quarter, quarter * 3]) {
        patternContext.beginPath();
        patternContext.moveTo(-quarter, y);
        patternContext.quadraticCurveTo(0, y - quarter / 2, quarter, y);
        patternContext.quadraticCurveTo(half, y + quarter / 2, quarter * 3, y);
        patternContext.quadraticCurveTo(TERRAIN_PATTERN_TILE, y - quarter / 2, TERRAIN_PATTERN_TILE + quarter, y);
        patternContext.stroke();
      }
    } else if (biome.pattern === 'ice') {
      for (let offset = -TERRAIN_PATTERN_TILE; offset < TERRAIN_PATTERN_TILE * 2; offset += quarter) {
        patternContext.beginPath();
        patternContext.moveTo(offset, TERRAIN_PATTERN_TILE);
        patternContext.lineTo(offset + half, 0);
        patternContext.stroke();
      }
    } else if (biome.pattern === 'contours') {
      for (const y of [quarter, half, quarter * 3]) {
        patternContext.beginPath();
        patternContext.moveTo(0, y + quarter / 3);
        patternContext.lineTo(quarter, y - quarter / 3);
        patternContext.lineTo(half, y + quarter / 3);
        patternContext.lineTo(quarter * 3, y - quarter / 3);
        patternContext.lineTo(TERRAIN_PATTERN_TILE, y + quarter / 3);
        patternContext.stroke();
      }
    } else if (biome.pattern === 'reeds') {
      for (const y of [quarter, half, quarter * 3]) {
        patternContext.beginPath();
        patternContext.moveTo(0, y);
        patternContext.lineTo(TERRAIN_PATTERN_TILE, y);
        patternContext.stroke();
      }
      for (let index = 0; index < 6; index += 1) {
        const x = deterministicUnit(index, seed) * TERRAIN_PATTERN_TILE;
        const y = deterministicUnit(index + 7, seed) * TERRAIN_PATTERN_TILE;
        patternContext.beginPath();
        patternContext.moveTo(x, y + quarter / 2);
        patternContext.lineTo(x, y - quarter / 2);
        patternContext.stroke();
      }
    } else if (biome.pattern === 'mist') {
      for (let index = 0; index < 7; index += 1) {
        const x = deterministicUnit(index, seed) * TERRAIN_PATTERN_TILE;
        const y = deterministicUnit(index + 11, seed) * TERRAIN_PATTERN_TILE;
        patternContext.beginPath();
        patternContext.arc(x, y, quarter / 2, 0, Math.PI * 1.35);
        patternContext.stroke();
      }
    } else if (biome.pattern === 'cracks') {
      for (let index = 0; index < 5; index += 1) {
        const x = deterministicUnit(index, seed) * TERRAIN_PATTERN_TILE;
        const y = deterministicUnit(index + 13, seed) * TERRAIN_PATTERN_TILE;
        patternContext.beginPath();
        patternContext.moveTo(x, y);
        patternContext.lineTo(x + quarter / 2, y + quarter);
        patternContext.lineTo(x, y + quarter * 1.5);
        patternContext.stroke();
      }
    } else {
      const marks = biome.pattern === 'trees' ? 8 : 12;
      for (let index = 0; index < marks; index += 1) {
        const x = deterministicUnit(index, seed) * TERRAIN_PATTERN_TILE;
        const y = deterministicUnit(index + 17, seed) * TERRAIN_PATTERN_TILE;
        const height = biome.pattern === 'trees' ? quarter : quarter / 2;
        patternContext.beginPath();
        patternContext.moveTo(x, y + height / 2);
        patternContext.lineTo(x, y - height / 2);
        patternContext.moveTo(x, y - height / 3);
        patternContext.lineTo(x - height / 3, y);
        patternContext.moveTo(x, y - height / 3);
        patternContext.lineTo(x + height / 3, y);
        patternContext.stroke();
      }
    }
    return tile;
  }

  function deterministicUnit(index, seed) {
    let value = Math.imul(index + 1, 0x45d9f3b) ^ Math.imul(seed + 11, 0x27d4eb2d);
    value = Math.imul(value ^ value >>> 16, 0x45d9f3b);
    value ^= value >>> 16;
    return (value >>> 0) / 0xffffffff;
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(devicePixelRatio || 1, DEVICE_PIXEL_RATIO_CAP);
    const width = Math.round(rect.width * ratio);
    const height = Math.round(rect.height * ratio);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    }
    requestDraw();
  }

  function requestDraw() {
    if (state.frame) return;
    state.frame = requestAnimationFrame(() => {
      state.frame = 0;
      draw();
    });
  }

  function screen(x, z) {
    const rect = canvas.getBoundingClientRect();
    return [
      rect.width / 2 + (x - state.x) * state.scale,
      rect.height / 2 - (z - state.z) * state.scale,
    ];
  }

  function ground(pixelX, pixelY) {
    const rect = canvas.getBoundingClientRect();
    return [
      state.x + (pixelX - rect.width / 2) / state.scale,
      state.z - (pixelY - rect.height / 2) / state.scale,
    ];
  }

  function visibleBounds(marginPixels = 0) {
    const rect = canvas.getBoundingClientRect();
    const [leftX, topZ] = ground(-marginPixels, -marginPixels);
    const [rightX, bottomZ] = ground(rect.width + marginPixels, rect.height + marginPixels);
    return {
      minX: Math.min(leftX, rightX),
      minZ: Math.min(topZ, bottomZ),
      maxX: Math.max(leftX, rightX),
      maxZ: Math.max(topZ, bottomZ),
    };
  }

  function fit() {
    const rect = canvas.getBoundingClientRect();
    state.x = 0;
    state.z = 0;
    state.fitScale = Math.max(MIN_SCALE, Math.min(rect.width, rect.height) / FIT_WORLD_DIAMETER);
    state.scale = Math.min(MAX_SCALE, state.fitScale);
    requestDraw();
  }

  function zoomAt(nextScale, pixelX, pixelY) {
    const rect = canvas.getBoundingClientRect();
    const anchorX = pixelX ?? rect.width / 2;
    const anchorY = pixelY ?? rect.height / 2;
    const before = ground(anchorX, anchorY);
    state.scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, nextScale));
    const after = ground(anchorX, anchorY);
    state.x += before[0] - after[0];
    state.z += before[1] - after[1];
    requestDraw();
  }

  // Fog is one pass on an offscreen canvas: fill it, cut out every discovered zone, then lay it over
  // the map. Punching holes on the main canvas would erase the terrain underneath instead of
  // revealing it, which is the whole trick.
  function decodeMask(mask) {
    if (!mask || !mask.bits || !mask.size) return null;
    try {
      const binary = atob(mask.bits);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
      const needed = Math.ceil((mask.size * mask.size) / 8);
      if (bytes.length < needed) throw new Error(`mask is ${bytes.length} bytes, needs ${needed}`);
      return bytes;
    } catch (error) {
      // A mask that cannot be trusted must not be drawn: falling back to the zone fog shows less than
      // the players have seen, where a misread mask would show ground nobody has.
      console.error('reported exploration mask unusable', error);
      return null;
    }
  }

  function drawFog(zones) {
    const rect = canvas.getBoundingClientRect();
    const size = Math.max(1, ZONE_SIZE * state.scale);
    // The margin is in PIXELS, and a zone is 64 metres, so it has to be a zone's on-screen size. It
    // was 64 - fine at world zoom, meaningless close in - so zones anchored just off-screen were
    // skipped while still covering visible ground, and once one zone was wider than the viewport
    // nothing was cut at all and the whole map went blank a few wheel clicks in.
    const bounds = visibleBounds(size + 2);
    if (!fogCanvas) fogCanvas = document.createElement('canvas');
    const width = Math.max(1, Math.ceil(rect.width));
    const height = Math.max(1, Math.ceil(rect.height));
    if (fogCanvas.width !== width || fogCanvas.height !== height) {
      fogCanvas.width = width;
      fogCanvas.height = height;
    }
    const fog = fogCanvas.getContext('2d');
    fog.setTransform(1, 0, 0, 1, 0, 0);
    fog.globalCompositeOperation = 'source-over';
    fog.fillStyle = colors.canvas;
    fog.fillRect(0, 0, width, height);
    fog.globalCompositeOperation = 'destination-out';
    // A mask reported by the players themselves beats the zone approximation: it is their own revealed
    // map, four times finer, and it does not credit anyone for ground the server merely loaded.
    const mask = state.data && state.data.explored_mask;
    if (mask && maskBits) {
      const cell = Math.max(1, mask.cell_size * state.scale);
      const minCellX = Math.max(0, Math.floor((bounds.minX - mask.origin_x) / mask.cell_size));
      const maxCellX = Math.min(mask.size - 1, Math.ceil((bounds.maxX - mask.origin_x) / mask.cell_size));
      const minCellZ = Math.max(0, Math.floor((bounds.minZ - mask.origin_z) / mask.cell_size));
      const maxCellZ = Math.min(mask.size - 1, Math.ceil((bounds.maxZ - mask.origin_z) / mask.cell_size));
      for (let cellZ = minCellZ; cellZ <= maxCellZ; cellZ += 1) {
        for (let cellX = minCellX; cellX <= maxCellX; cellX += 1) {
          const index = cellZ * mask.size + cellX;
          if ((maskBits[index >> 3] & (1 << (index & 7))) === 0) continue;
          const worldX = mask.origin_x + cellX * mask.cell_size;
          const worldZ = mask.origin_z + cellZ * mask.cell_size;
          const [left, top] = screen(worldX, worldZ + mask.cell_size);
          fog.fillRect(left, top, cell, cell);
        }
      }
    } else {
      for (const zone of zones) {
        if (Math.abs(zone.x) > MAX_ZONE_INDEX || Math.abs(zone.y) > MAX_ZONE_INDEX) continue;
        const worldX = zone.x * ZONE_SIZE;
        const worldZ = zone.y * ZONE_SIZE;
        if (worldX < bounds.minX || worldX > bounds.maxX || worldZ < bounds.minZ || worldZ > bounds.maxZ) continue;
        const [pixelX, pixelY] = screen(worldX, worldZ);
        // A zone index is its centre - the game rounds when it maps a position to a zone - so the
        // cleared square is centred too. Anchoring it at the corner offset every hole by 32 metres,
        // which left fog over ground players had walked and cut holes over ground they had not.
        fog.fillRect(pixelX - size / 2, pixelY - size / 2, size, size);
      }
    }
    // Opaque, deliberately. At 0.97 the terrain bled through the fog: a faint but real picture of
    // coastlines nobody has sailed, which is the one thing this map is supposed to withhold.
    context.save();
    context.drawImage(fogCanvas, 0, 0, rect.width, rect.height);
    context.restore();
  }

  function draw() {
    const rect = canvas.getBoundingClientRect();
    context.fillStyle = colors.canvas;
    context.fillRect(0, 0, rect.width, rect.height);
    if (!state.data) {
      updateScaleDisplay();
      return;
    }
    refreshOverlayView();
    const snapshot = currentSnapshot();
    if (state.layers.terrain) drawTerrain();
    // Fog goes straight over the terrain, before anything else is drawn: the players' map shows the
    // ground they have actually walked and nothing else. Valheim only builds a zone when somebody
    // has been near it, so the zone list is the record of where they have been.
    if (fogOfWar) drawFog(snapshot.generated_zones || []);
    if (state.layers.zones) drawZones(snapshot.generated_zones || []);
    if (state.layers.clusters) {
      drawConstructionCoverage(snapshot.construction_coverage);
      drawClusters(snapshot.clusters || [], Boolean(snapshot.construction_coverage));
    }
    if (state.layers.locations) drawLocations(snapshot.locations || []);
    for (const layer of objectLayers) {
      if (state.layers[layer]) drawObjectLayer(layer);
    }
    if (state.layers.pins) drawReportedPins(state.data.pins || []);
    drawOrigin();
    updateScaleDisplay();
  }

  function drawTerrain() {
    if (!terrainSurface && !terrainManifest) return;
    const [left, top] = screen(-WORLD_RADIUS, WORLD_RADIUS);
    const size = WORLD_DIAMETER * state.scale;
    const [centerX, centerY] = screen(0, 0);
    const tiled = drawTerrainTiles();
    if (!tiled && terrainSurface) {
      context.save();
      // Valheim source row zero is world south, so convert it once into the +Z-up screen invariant.
      context.translate(0, centerY * 2);
      context.scale(1, -1);
      const sourcePixelScreenSize = WORLD_DIAMETER / Math.max(1, terrainSourceWidth) * state.scale;
      context.imageSmoothingEnabled = sourcePixelScreenSize < 2;
      context.imageSmoothingQuality = 'high';
      context.drawImage(terrainSurface, left, top, size, size);
      context.restore();
    }
    drawCloseTerrainDetail();
    context.save();
    context.beginPath();
    context.arc(centerX, centerY, PLAYABLE_RADIUS * state.scale, 0, Math.PI * 2);
    context.strokeStyle = colors.worldEdge;
    context.globalAlpha = 0.2;
    context.lineWidth = 1;
    context.stroke();
    context.restore();
  }

  function drawCloseTerrainDetail() {
    if (!terrainBiomeIndices || state.scale < CLOSE_TERRAIN_DETAIL_SCALE) return;
    const bounds = visibleBounds(0);
    const idealWorldSpacing = 18 / state.scale;
    const spacing = Math.min(32, Math.max(0.125, 2 ** Math.round(Math.log2(idealWorldSpacing))));
    const firstX = Math.floor(bounds.minX / spacing);
    const lastX = Math.ceil(bounds.maxX / spacing);
    const firstZ = Math.floor(bounds.minZ / spacing);
    const lastZ = Math.ceil(bounds.maxZ / spacing);
    const columns = Math.max(1, lastX - firstX + 1);
    const rows = Math.max(1, lastZ - firstZ + 1);
    const stride = Math.max(1, Math.ceil(Math.sqrt(columns * rows / MAX_TERRAIN_DETAIL_MARKS)));
    const markSize = Math.min(7, Math.max(2.25, Math.log2(state.scale + 1) + 2));
    context.save();
    context.lineWidth = 1;
    context.lineCap = 'round';
    context.lineJoin = 'round';
    for (let gridX = firstX; gridX <= lastX; gridX += stride) {
      for (let gridZ = firstZ; gridZ <= lastZ; gridZ += stride) {
        const x = (gridX + 0.5) * spacing;
        const z = (gridZ + 0.5) * spacing;
        const biome = biomeAt(x, z);
        if (!biome || biome.pattern === 'none') continue;
        const biomeIndex = biomeBySource.get(biome.source) ?? 0;
        const jitterX = (terrainUnit(gridX, gridZ, biomeIndex) - 0.5) * spacing * 0.55;
        const jitterZ = (terrainUnit(gridZ, gridX, biomeIndex + 19) - 0.5) * spacing * 0.55;
        const [pixelX, pixelY] = screen(x + jitterX, z + jitterZ);
        drawBiomeMark(biome, biomeIndex, pixelX, pixelY, markSize, gridX, gridZ);
      }
    }
    context.restore();
  }

  function drawBiomeMark(biome, biomeIndex, x, y, size, gridX, gridZ) {
    const unit = terrainUnit(gridX, gridZ, biomeIndex + 41);
    const bend = (terrainUnit(gridZ, gridX, biomeIndex + 67) - 0.5) * size;
    const rotation = biome.pattern === 'waves'
      ? (unit - 0.5) * 0.45
      : unit * Math.PI * 2;
    context.save();
    context.translate(x, y);
    context.rotate(rotation);
    context.strokeStyle = biomeTextureColors[biomeIndex];
    context.fillStyle = context.strokeStyle;
    context.globalAlpha = 0.18 + Math.min(0.18, Math.log2(state.scale + 1) * 0.022);
    context.beginPath();
    if (biome.pattern === 'waves') {
      context.moveTo(-size, bend * 0.2);
      context.bezierCurveTo(-size * 0.55, -size * 0.65, -size * 0.15, size * 0.65, size * 0.25, 0);
      context.bezierCurveTo(size * 0.55, -size * 0.45, size * 0.8, -size * 0.2, size, bend * 0.15);
      context.stroke();
    } else if (biome.pattern === 'mist') {
      context.moveTo(-size, size * 0.25);
      context.bezierCurveTo(-size * 0.4, -size, size * 0.45, size, size, -size * 0.15);
      context.stroke();
    } else if (biome.pattern === 'ice') {
      context.moveTo(-size, size * 0.8);
      context.lineTo(bend * 0.3, -size * 0.05);
      context.lineTo(size, -size * 0.7);
      context.moveTo(bend * 0.3, -size * 0.05);
      context.lineTo(size * 0.55, size * 0.55);
      context.stroke();
    } else if (biome.pattern === 'trees') {
      context.moveTo(0, size);
      context.quadraticCurveTo(bend * 0.25, 0, 0, -size);
      context.moveTo(0, -size * 0.7);
      context.lineTo(-size * 0.72, size * 0.35);
      context.quadraticCurveTo(0, size * 0.05, size * 0.72, size * 0.35);
      context.stroke();
    } else if (biome.pattern === 'contours') {
      context.ellipse(0, 0, size, size * (0.38 + unit * 0.2), bend * 0.08, 0, Math.PI * 2);
      context.moveTo(size * 0.55, 0);
      context.ellipse(0, 0, size * 0.55, size * 0.2, -bend * 0.08, 0, Math.PI * 2);
      context.stroke();
    } else if (biome.pattern === 'cracks') {
      context.moveTo(-size, -size * 0.45);
      context.lineTo(bend * 0.4, 0);
      context.lineTo(-size * 0.45, size);
      context.moveTo(bend * 0.4, 0);
      context.lineTo(size, size * 0.35);
      context.stroke();
    } else if (biome.pattern === 'reeds') {
      context.moveTo(-size * 0.55, size);
      context.quadraticCurveTo(-size * 0.65 + bend * 0.2, 0, -size * 0.3, -size);
      context.moveTo(size * 0.45, size);
      context.quadraticCurveTo(size * 0.55 - bend * 0.2, 0, size * 0.2, -size * 0.7);
      context.stroke();
    } else if (biome.pattern === 'grass') {
      context.moveTo(0, size);
      context.quadraticCurveTo(-size * 0.15, 0, bend * 0.25, -size);
      context.moveTo(0, size * 0.55);
      context.quadraticCurveTo(-size * 0.7, 0, -size, -size * 0.2);
      context.moveTo(0, size * 0.45);
      context.quadraticCurveTo(size * 0.65, 0, size, -size * 0.35);
      context.stroke();
    } else {
      context.moveTo(-size * 0.25, size);
      context.quadraticCurveTo(-size * 0.5 + bend * 0.2, 0, -size * 0.15, -size);
      context.moveTo(size * 0.3, size);
      context.quadraticCurveTo(size * 0.55 - bend * 0.2, 0, size * 0.2, -size * 0.75);
      context.stroke();
    }
    context.restore();
  }

  function terrainUnit(x, z, seed) {
    let value = Math.imul(x | 0, 73_856_093) ^ Math.imul(z | 0, 19_349_663) ^ Math.imul(seed + 1, 83_492_791);
    value = Math.imul(value ^ value >>> 16, 0x45d9f3b);
    value ^= value >>> 16;
    return (value >>> 0) / 0xffffffff;
  }

  function drawZones(zones) {
    const bounds = visibleBounds(2);
    const inspectionStride = Math.max(1, Math.ceil(zones.length / MAX_ZONE_INSPECTIONS));
    const overviewStride = state.scale < ZONE_DETAIL_SCALE
      ? Math.max(inspectionStride, Math.ceil(zones.length / MAX_ZONE_MARKS))
      : inspectionStride;
    let drawn = 0;
    context.save();
    context.strokeStyle = colors.zone;
    context.fillStyle = colors.zone;
    for (let index = 0; index < zones.length && drawn < MAX_ZONE_MARKS; index += overviewStride) {
      const zone = zones[index];
      // The game parks global objects in a zone at 1,000,000 metres. Real bookkeeping, nowhere
      // anybody walked, so it is neither shaded nor counted as explored.
      if (Math.abs(zone.x) > MAX_ZONE_INDEX || Math.abs(zone.y) > MAX_ZONE_INDEX) continue;
      const worldX = zone.x * ZONE_SIZE;
      const worldZ = zone.y * ZONE_SIZE;
      if (worldX < bounds.minX || worldX > bounds.maxX || worldZ < bounds.minZ || worldZ > bounds.maxZ) continue;
      const [pixelX, pixelY] = screen(worldX, worldZ);
      if (state.scale >= ZONE_DETAIL_SCALE) {
        const size = ZONE_SIZE * state.scale;
        context.globalAlpha = 0.08;
        context.fillRect(pixelX - size / 2, pixelY - size / 2, size, size);
        context.globalAlpha = 0.24;
        context.strokeRect(pixelX - size / 2, pixelY - size / 2, size, size);
      } else {
        // Zoomed out a zone is under a pixel, but the question is "how much of the map have we
        // seen", so it stays filled at a floor of one pixel rather than becoming a dot.
        const size = Math.max(1, ZONE_SIZE * state.scale);
        context.globalAlpha = 0.16;
        context.fillRect(pixelX - size / 2, pixelY - size / 2, size, size);
      }
      drawn += 1;
    }
    context.restore();
  }

  function drawConstructionCoverage(coverage) {
    if (!coverage || state.scale < COVERAGE_VISIBLE_SCALE) return;
    const fade = Math.min(1, (state.scale - COVERAGE_VISIBLE_SCALE) / (COVERAGE_FULL_SCALE - COVERAGE_VISIBLE_SCALE));
    const bounds = visibleBounds(2);
    const denominator = Math.log1p(Math.max(1, coverage.max_pieces));
    context.save();
    for (const cell of coverage.cells || []) {
      // The cell draws in whoever built most of it. This is the layer an operator sees as "the
      // constructions", so colouring only the cluster glyphs changed nothing anybody could see.
      const cellColour = builderColour(cell.creator);
      context.fillStyle = cellColour;
      context.strokeStyle = cellColour;
      const minX = cell.x * coverage.cell_size;
      const minZ = cell.z * coverage.cell_size;
      const maxX = minX + coverage.cell_size;
      const maxZ = minZ + coverage.cell_size;
      if (maxX < bounds.minX || minX > bounds.maxX || maxZ < bounds.minZ || minZ > bounds.maxZ) continue;
      const [left, top] = screen(minX, maxZ);
      const size = coverage.cell_size * state.scale;
      const density = Math.log1p(cell.pieces) / denominator;
      context.globalAlpha = fade * (0.14 + density * 0.42);
      context.fillRect(left, top, size, size);
      if (state.scale >= COVERAGE_FULL_SCALE) {
        context.globalAlpha = fade * (0.22 + density * 0.35);
        context.lineWidth = 1;
        context.strokeRect(left + 0.5, top + 0.5, Math.max(0, size - 1), Math.max(0, size - 1));
      }
    }
    context.restore();
  }

  // A name drawn straight onto terrain competes with whatever is under it. Glyphs already solve this
  // with a halo disc, so a label gets the same treatment: a backdrop chip, the builder's colour for
  // the text, and a hairline in that colour so the chip itself says whose name it is. Labels that
  // would land on top of each other step down instead, and give up rather than stack illegibly -
  // three people sharing one base is the normal case, not the exception.
  function drawBuilderLabel(text, centerX, bottomY, colour, taken) {
    context.save();
    context.font = '600 11px system-ui, sans-serif';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    const width = context.measureText(text).width;
    const boxWidth = width + 10;
    const boxHeight = 15;
    const left = centerX - boxWidth / 2;
    let top = bottomY - boxHeight;
    let placed = false;
    for (let attempt = 0; attempt < 5 && !placed; attempt += 1) {
      const box = { left, top, right: left + boxWidth, bottom: top + boxHeight };
      const clash = taken.some((other) => box.left < other.right && box.right > other.left
        && box.top < other.bottom && box.bottom > other.top);
      if (clash) {
        top += boxHeight + 2;
        continue;
      }
      taken.push(box);
      placed = true;
      context.beginPath();
      if (typeof context.roundRect === 'function') {
        context.roundRect(box.left, box.top, boxWidth, boxHeight, 3);
      } else {
        context.rect(box.left, box.top, boxWidth, boxHeight);
      }
      context.fillStyle = colors.halo;
      context.globalAlpha = 0.86;
      context.fill();
      context.strokeStyle = colour;
      context.globalAlpha = 0.7;
      context.lineWidth = 1;
      context.stroke();
      context.globalAlpha = 1;
      context.fillStyle = colour;
      context.fillText(text, centerX, box.top + boxHeight / 2 + 0.5);
    }
    context.restore();
  }

  function drawClusters(clusters, haveCoverage) {
    const bounds = visibleBounds(20);
    const coverageVisible = haveCoverage && state.scale >= COVERAGE_VISIBLE_SCALE;
    const visible = clusters.filter((cluster) => cluster.center.x >= bounds.minX && cluster.center.x <= bounds.maxX
      && cluster.center.z >= bounds.minZ && cluster.center.z <= bounds.maxZ).slice(0, MAX_CLUSTER_GLYPHS);
    const bySize = [...visible].sort((a, b) => (b.pieces || 0) - (a.pieces || 0));

    // Smallest first, so the shape an operator came to see ends up on top. Four builders sharing one
    // valley are metres apart: whoever drew last owned those pixels, and that was the 4-piece shed
    // painting over the 58-piece base.
    for (const cluster of [...bySize].reverse()) {
      const [pixelX, pixelY] = screen(cluster.center.x, cluster.center.z);
      // The 35 m floor keeps a cluster findable on a whole-world view, but close in it inflates
      // neighbours into one blob. Zoomed in, a site is drawn the size it actually is.
      const metres = state.scale >= COVERAGE_FULL_SCALE ? Math.max(cluster.radius, 4) : Math.max(cluster.radius, 35);
      context.save();
      context.beginPath();
      context.arc(pixelX, pixelY, Math.max(3, metres * state.scale), 0, Math.PI * 2);
      const builder = builderColour(cluster.creator);
      context.fillStyle = builder;
      context.globalAlpha = coverageVisible ? 0.06 : 0.16;
      context.fill();
      context.strokeStyle = builder;
      context.globalAlpha = coverageVisible ? 0.24 : 0.42;
      context.lineWidth = 1;
      context.stroke();
      context.restore();
      drawGlyph('cluster', pixelX, pixelY, markerSize(), builder);
    }

    // Names in a second pass, biggest first: a label is a claim on scarce space, and the site with
    // the most pieces is the one worth naming when they cannot all fit.
    if (state.scale >= COVERAGE_FULL_SCALE) {
      const labelBoxes = [];
      for (const cluster of bySize) {
        const [pixelX, pixelY] = screen(cluster.center.x, cluster.center.z);
        drawBuilderLabel(builderName(cluster.creator), pixelX, pixelY - markerSize() - 3, builderColour(cluster.creator), labelBoxes);
      }
    }
  }

  function isSpawnLocation(location) {
    return String(location.name || '').toLowerCase().includes('starttemple');
  }

  function locationCategory(location) {
    if (isSpawnLocation(location)) return 'spawn';
    if (location.category) return location.category;
    const name = String(location.name || '').toLowerCase();
    const has = (...parts) => parts.some((part) => name.includes(part));
    if (has('eikthyr', 'gdking', 'bonemass', 'dragonqueen', 'goblinking', 'bossentrance', 'faderlocation')) return 'boss';
    if (has('vendor', 'hildir_camp', 'bogwitch', 'tavern', 'blacksmith')) return 'trader';
    if (has('crypt', 'cave', 'trollcave', 'morgenhole', 'firehole', 'bfd_exterior')) return 'dungeon';
    if (has('fortress', 'guardtower', 'stonetower', 'charredtower')) return 'fortress';
    if (has('camp', 'village', 'farm', 'woodhouse', 'stonehouse', 'swamphut', 'dvergrtown', 'harbour')) return 'settlement';
    if (has('tarpit', 'volturenest', 'drakenest', 'leviathan', 'spawner', 'sulfur', 'infestedtree')) return 'resource';
    if (has('runestone', 'ruin', 'grave', 'dolmen', 'ship', 'statue', 'giant', 'sword', 'viaduct', 'lighthouse', 'roadpost', 'waymarker', 'well', 'excavation', 'arch', 'stonecircle', 'stonehenge', 'starttemple', 'placeofmystery')) return 'landmark';
    return 'other';
  }

  function locationColor(category) {
    return {
      spawn: colors.locationSpawn,
      boss: colors.locationBoss,
      trader: colors.locationTrader,
      dungeon: colors.locationDungeon,
      fortress: colors.locationFortress,
      settlement: colors.locationSettlement,
      resource: colors.locationResource,
      landmark: colors.locationLandmark,
      other: colors.locationOther,
    }[category] || colors.locationOther;
  }

  function drawLocations(locations) {
    const bounds = visibleBounds(20);
    const visible = locations
      .map((location) => ({ location, category: locationCategory(location) }))
      .filter(({ category }) => state.locationCategories[category] !== false);
    const priority = visible.filter(({ category }) => category === 'spawn' || category === 'boss');
    const regular = visible.filter(({ category }) => category !== 'spawn' && category !== 'boss');
    let drawn = 0;
    const drawLocation = ({ location, category }) => {
      if (location.position.x < bounds.minX || location.position.x > bounds.maxX || location.position.z < bounds.minZ || location.position.z > bounds.maxZ) return;
      const [pixelX, pixelY] = screen(location.position.x, location.position.z);
      drawGlyph(
        `location-${category}${location.generated ? '' : '-pending'}`,
        pixelX,
        pixelY,
        category === 'spawn' || category === 'boss' ? markerSize() + 4 : markerSize(),
        locationColor(category),
      );
      drawn += 1;
    };
    for (const location of priority) drawLocation(location);
    const available = Math.max(1, MAX_LOCATION_GLYPHS - drawn);
    const inspectionStride = Math.max(1, Math.ceil(regular.length / MAX_LOCATION_INSPECTIONS));
    const overviewStride = state.scale < ZONE_DETAIL_SCALE
      ? Math.max(inspectionStride, Math.ceil(regular.length / available))
      : inspectionStride;
    for (let index = 0; index < regular.length && drawn < MAX_LOCATION_GLYPHS; index += overviewStride) {
      drawLocation(regular[index]);
    }
  }

  function drawObjectLayer(layer) {
    const index = state.objectIndexes.get(layer);
    if (!index) return;
    const bounds = visibleBounds(20);
    const minCellX = Math.max(index.minX, Math.floor(bounds.minX / OBJECT_INDEX_CELL));
    const maxCellX = Math.min(index.maxX, Math.floor(bounds.maxX / OBJECT_INDEX_CELL));
    const minCellZ = Math.max(index.minZ, Math.floor(bounds.minZ / OBJECT_INDEX_CELL));
    const maxCellZ = Math.min(index.maxZ, Math.floor(bounds.maxZ / OBJECT_INDEX_CELL));
    if (minCellX > maxCellX || minCellZ > maxCellZ) return;
    const columns = maxCellX - minCellX + 1;
    const rows = maxCellZ - minCellZ + 1;
    const cellCount = columns * rows;
    const maximum = state.scale < ZONE_DETAIL_SCALE ? MAX_OBJECT_GLYPHS_OVERVIEW : MAX_OBJECT_GLYPHS_CLOSE;
    const cellStride = state.scale < ZONE_DETAIL_SCALE ? Math.max(1, Math.ceil(cellCount / maximum)) : 1;
    let ordinal = 0;
    let drawn = 0;
    for (let cellX = minCellX; cellX <= maxCellX && drawn < maximum; cellX += 1) {
      for (let cellZ = minCellZ; cellZ <= maxCellZ && drawn < maximum; cellZ += 1) {
        if (ordinal % cellStride !== 0) {
          ordinal += 1;
          continue;
        }
        ordinal += 1;
        const objects = index.cells.get(indexKey(cellX, cellZ));
        if (!objects) continue;
        const perCell = state.scale < ZONE_DETAIL_SCALE ? 1 : objects.length;
        for (let objectIndex = 0; objectIndex < perCell && drawn < maximum; objectIndex += 1) {
          const object = objects[objectIndex];
          if (object.position.x < bounds.minX || object.position.x > bounds.maxX || object.position.z < bounds.minZ || object.position.z > bounds.maxZ) continue;
          const [pixelX, pixelY] = screen(object.position.x, object.position.z);
          const kind = object.category === 'world' ? 'world' : object.category === 'unknown' ? 'unknown' : object.category;
          drawGlyph(kind, pixelX, pixelY, markerSize(), objectColor(object.category));
          drawn += 1;
        }
      }
    }
  }

  function markerSize() {
    if (state.scale >= COVERAGE_FULL_SCALE) return 11;
    if (state.scale >= ZONE_DETAIL_SCALE) return 9;
    return 7;
  }

  function objectColor(category) {
    if (category === 'terrain') return colors.risk;
    return colors[category] || colors.other;
  }

  function drawGlyph(kind, x, y, size, color) {
    const radius = size / 2;
    context.save();
    context.translate(x, y);
    context.lineWidth = Math.max(1.25, size / 6);
    context.lineCap = 'round';
    context.lineJoin = 'round';
    context.strokeStyle = color;
    context.fillStyle = color;
    context.beginPath();
    context.arc(0, 0, radius + 2, 0, Math.PI * 2);
    context.fillStyle = colors.halo;
    context.globalAlpha = 0.74;
    context.fill();
    context.globalAlpha = 1;
    context.fillStyle = color;

    if (kind.startsWith('location-')) {
      const pending = kind.endsWith('-pending');
      const category = kind.slice(9, pending ? -8 : undefined);
      context.beginPath();
      if (category === 'spawn') {
        context.arc(0, 0, radius * 0.82, 0, Math.PI * 2);
        context.strokeStyle = color;
        context.stroke();
        context.beginPath();
        context.strokeStyle = colors.locationSpawnAccent;
        context.moveTo(-radius, 0);
        context.lineTo(radius, 0);
        context.moveTo(0, -radius);
        context.lineTo(0, radius);
      } else if (category === 'boss') {
        for (let point = 0; point < 10; point += 1) {
          const angle = -Math.PI / 2 + point * Math.PI / 5;
          const pointRadius = point % 2 === 0 ? radius : radius * 0.42;
          const pointX = Math.cos(angle) * pointRadius;
          const pointY = Math.sin(angle) * pointRadius;
          if (point === 0) context.moveTo(pointX, pointY);
          else context.lineTo(pointX, pointY);
        }
        context.closePath();
      } else if (category === 'trader') {
        context.arc(0, 0, radius * 0.82, 0, Math.PI * 2);
        context.moveTo(-radius * 0.5, 0);
        context.lineTo(radius * 0.5, 0);
      } else if (category === 'dungeon') {
        context.moveTo(-radius * 0.72, radius);
        context.lineTo(-radius * 0.72, 0);
        context.arc(0, 0, radius * 0.72, Math.PI, 0);
        context.lineTo(radius * 0.72, radius);
        context.closePath();
      } else if (category === 'fortress') {
        context.moveTo(-radius, radius);
        context.lineTo(-radius, -radius * 0.72);
        context.lineTo(-radius * 0.45, -radius * 0.72);
        context.lineTo(-radius * 0.45, -radius * 0.2);
        context.lineTo(radius * 0.45, -radius * 0.2);
        context.lineTo(radius * 0.45, -radius * 0.72);
        context.lineTo(radius, -radius * 0.72);
        context.lineTo(radius, radius);
        context.closePath();
      } else if (category === 'settlement') {
        context.moveTo(-radius, -radius * 0.1);
        context.lineTo(0, -radius);
        context.lineTo(radius, -radius * 0.1);
        context.lineTo(radius * 0.72, -radius * 0.1);
        context.lineTo(radius * 0.72, radius);
        context.lineTo(-radius * 0.72, radius);
        context.lineTo(-radius * 0.72, -radius * 0.1);
        context.closePath();
      } else if (category === 'resource') {
        context.moveTo(0, -radius);
        context.lineTo(radius * 0.82, radius * 0.72);
        context.lineTo(0, radius);
        context.lineTo(-radius * 0.82, radius * 0.72);
        context.closePath();
      } else if (category === 'landmark') {
        context.moveTo(0, -radius);
        context.lineTo(radius, 0);
        context.lineTo(0, radius);
        context.lineTo(-radius, 0);
        context.closePath();
      } else {
        context.arc(0, 0, radius * 0.62, 0, Math.PI * 2);
      }
      if (!pending && category !== 'trader' && category !== 'spawn') context.fill();
      context.stroke();
    } else if (kind === 'cluster') {
      context.beginPath();
      context.moveTo(-radius, 0);
      context.lineTo(0, -radius);
      context.lineTo(radius, 0);
      context.moveTo(-radius * 0.7, -radius * 0.1);
      context.lineTo(-radius * 0.7, radius);
      context.lineTo(radius * 0.7, radius);
      context.lineTo(radius * 0.7, -radius * 0.1);
      context.stroke();
    } else if (kind === 'portal') {
      context.beginPath();
      context.ellipse(0, 0, radius * 0.75, radius, 0, 0, Math.PI * 2);
      context.stroke();
      context.beginPath();
      context.moveTo(-radius * 0.45, 0);
      context.lineTo(radius * 0.45, 0);
      context.stroke();
    } else if (kind === 'container') {
      context.strokeRect(-radius, -radius * 0.45, radius * 2, radius * 1.35);
      context.beginPath();
      context.moveTo(-radius, -radius * 0.45);
      context.lineTo(-radius * 0.6, -radius);
      context.lineTo(radius * 0.6, -radius);
      context.lineTo(radius, -radius * 0.45);
      context.moveTo(-radius * 0.25, 0);
      context.lineTo(radius * 0.25, 0);
      context.stroke();
    } else if (kind === 'production') {
      context.beginPath();
      context.moveTo(0, -radius);
      context.lineTo(radius, radius);
      context.lineTo(-radius, radius);
      context.closePath();
      context.stroke();
      context.beginPath();
      context.arc(0, radius * 0.25, radius * 0.22, 0, Math.PI * 2);
      context.fill();
    } else if (kind === 'creature') {
      context.beginPath();
      context.arc(0, radius * 0.3, radius * 0.48, 0, Math.PI * 2);
      context.fill();
      for (const toeX of [-radius * 0.58, 0, radius * 0.58]) {
        context.beginPath();
        context.arc(toeX, -radius * 0.55, radius * 0.22, 0, Math.PI * 2);
        context.fill();
      }
    } else if (kind === 'terrain') {
      context.beginPath();
      context.moveTo(0, -radius);
      context.lineTo(radius, radius);
      context.lineTo(-radius, radius);
      context.closePath();
      context.stroke();
      context.beginPath();
      context.moveTo(0, -radius * 0.42);
      context.lineTo(0, radius * 0.28);
      context.stroke();
      context.beginPath();
      context.arc(0, radius * 0.62, context.lineWidth / 2, 0, Math.PI * 2);
      context.fill();
    } else if (kind === 'world') {
      context.beginPath();
      context.ellipse(-radius * 0.28, 0, radius * 0.58, radius * 0.34, -0.5, 0, Math.PI * 2);
      context.ellipse(radius * 0.28, 0, radius * 0.58, radius * 0.34, -0.5, 0, Math.PI * 2);
      context.stroke();
    } else if (kind === 'unknown') {
      context.font = `700 ${size * 1.15}px ${monoFont}`;
      context.textAlign = 'center';
      context.textBaseline = 'middle';
      context.fillText('?', 0, 0);
    } else {
      context.beginPath();
      context.arc(0, 0, radius * 0.55, 0, Math.PI * 2);
      context.fill();
    }
    context.restore();
  }

  // The pins players placed on their own maps. Drawn last so a name is never buried under terrain
  // detail, and skipped when zoomed far out, where a hundred labels are a smear rather than a map.
  function drawReportedPins(pins) {
    if (!pins.length) return;
    const bounds = visibleBounds(40);
    const labelBoxes = [];
    const showNames = state.scale >= 0.12;
    for (const pin of pins) {
      if (pin.x < bounds.minX || pin.x > bounds.maxX || pin.z < bounds.minZ || pin.z > bounds.maxZ) continue;
      const [pixelX, pixelY] = screen(pin.x, pin.z);
      const colour = pin.crossed_off ? colors.locationOther : colors.locationLandmark;
      context.save();
      context.globalAlpha = pin.crossed_off ? 0.5 : 0.95;
      context.strokeStyle = colour;
      context.fillStyle = colour;
      context.lineWidth = 1.5;
      const radius = Math.max(3, markerSize() * 0.5);
      context.beginPath();
      context.arc(pixelX, pixelY, radius, 0, Math.PI * 2);
      context.stroke();
      // A crossed-off pin is struck through, the way the player left it.
      if (pin.crossed_off) {
        context.beginPath();
        context.moveTo(pixelX - radius, pixelY - radius);
        context.lineTo(pixelX + radius, pixelY + radius);
        context.stroke();
      }
      context.restore();
      if (showNames && pin.name) {
        drawBuilderLabel(pin.name, pixelX, pixelY - radius - 2, colour, labelBoxes);
      }
    }
  }

  function drawOrigin() {
    const [centerX, centerY] = screen(0, 0);
    context.save();
    context.strokeStyle = colors.grid;
    context.globalAlpha = 0.3;
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(centerX - 8, centerY);
    context.lineTo(centerX + 8, centerY);
    context.moveTo(centerX, centerY - 8);
    context.lineTo(centerX, centerY + 8);
    context.stroke();
    context.restore();
  }

  function updateScaleDisplay() {
    if (state.scale >= 1) {
      zoomLevel.textContent = `${state.scale.toFixed(1)} pixels / metre`;
    } else {
      zoomLevel.textContent = `${Math.round(1 / state.scale)} metres / pixel`;
    }
    const coverage = state.data?.snapshot?.construction_coverage;
    if (!coverage) {
      coverageNote.textContent = state.data
        ? 'Legacy snapshot: construction remains available as settlement clusters.'
        : 'Player-build coverage appears at close zoom.';
    } else if (state.scale < COVERAGE_VISIBLE_SCALE) {
      coverageNote.textContent = `Zoom closer to reveal ${coverage.cells.length.toLocaleString()} bounded build-coverage cells.`;
    } else {
      coverageNote.textContent = `Showing ${coverage.cells.length.toLocaleString()} × ${coverage.cell_size} m cells for ${coverage.total_pieces.toLocaleString()} pieces.`;
    }
  }

  function biomeAt(x, z) {
    if (!terrainBiomeIndices || x < -WORLD_RADIUS || x >= WORLD_RADIUS || z < -WORLD_RADIUS || z >= WORLD_RADIUS) return null;
    const column = Math.min(terrainSourceWidth - 1, Math.max(0, Math.floor((x + WORLD_RADIUS) / WORLD_DIAMETER * terrainSourceWidth)));
    // Source row zero is world south, matching the terrain transform.
    const row = Math.min(terrainSourceHeight - 1, Math.max(0, Math.floor((z + WORLD_RADIUS) / WORLD_DIAMETER * terrainSourceHeight)));
    return biomes[terrainBiomeIndices[row * terrainSourceWidth + column]];
  }

  function buildIndexes(snapshot) {
    state.objectIndexes = new Map();
    for (const object of snapshot.objects || []) {
      const layer = layerForCategory(object.category);
      let index = state.objectIndexes.get(layer);
      if (!index) {
        index = { cells: new Map(), minX: Infinity, maxX: -Infinity, minZ: Infinity, maxZ: -Infinity };
        state.objectIndexes.set(layer, index);
      }
      const cellX = Math.floor(object.position.x / OBJECT_INDEX_CELL);
      const cellZ = Math.floor(object.position.z / OBJECT_INDEX_CELL);
      const key = indexKey(cellX, cellZ);
      let cell = index.cells.get(key);
      if (!cell) {
        cell = [];
        index.cells.set(key, cell);
      }
      cell.push(object);
      index.minX = Math.min(index.minX, cellX);
      index.maxX = Math.max(index.maxX, cellX);
      index.minZ = Math.min(index.minZ, cellZ);
      index.maxZ = Math.max(index.maxZ, cellZ);
    }
    state.coverageIndex = new Map();
    const coverage = snapshot.construction_coverage;
    if (coverage) {
      for (const cell of coverage.cells || []) state.coverageIndex.set(indexKey(cell.x, cell.z), cell);
    }
  }

  function layerForCategory(category) {
    if (category === 'terrain') return 'terrain-risk';
    if (['portal', 'container', 'production', 'creature'].includes(category)) return category;
    return 'other';
  }

  function indexKey(x, z) {
    return `${x}:${z}`;
  }

  function selectAt(pixelX, pixelY) {
    if (!state.data) return;
    let best = null;
    let bestDistance = HIT_RADIUS_PIXELS;
    const snapshot = currentSnapshot();
    const consider = (kind, x, z, data) => {
      const [candidateX, candidateY] = screen(x, z);
      const distance = Math.hypot(candidateX - pixelX, candidateY - pixelY);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = { kind, x, z, data };
      }
    };

    if (state.layers.locations) {
      for (const location of snapshot.locations || []) {
        const category = locationCategory(location);
        if (state.locationCategories[category] === false) continue;
        consider(`${category} location`, location.position.x, location.position.z, location);
      }
    }
    if (state.layers.clusters) {
      for (const cluster of snapshot.clusters || []) {
        consider('settlement cluster', cluster.center.x, cluster.center.z, cluster);
      }
    }
    const worldRadius = HIT_RADIUS_PIXELS / state.scale;
    const [groundX, groundZ] = ground(pixelX, pixelY);
    for (const layer of objectLayers) {
      if (!state.layers[layer]) continue;
      const index = state.objectIndexes.get(layer);
      if (!index) continue;
      const minCellX = Math.floor((groundX - worldRadius) / OBJECT_INDEX_CELL);
      const maxCellX = Math.floor((groundX + worldRadius) / OBJECT_INDEX_CELL);
      const minCellZ = Math.floor((groundZ - worldRadius) / OBJECT_INDEX_CELL);
      const maxCellZ = Math.floor((groundZ + worldRadius) / OBJECT_INDEX_CELL);
      for (let cellX = minCellX; cellX <= maxCellX; cellX += 1) {
        for (let cellZ = minCellZ; cellZ <= maxCellZ; cellZ += 1) {
          for (const object of index.cells.get(indexKey(cellX, cellZ)) || []) {
            consider(object.category, object.position.x, object.position.z, object);
          }
        }
      }
    }

    let coverageSelection = null;
    const coverage = snapshot.construction_coverage;
    if (state.layers.clusters && coverage && state.scale >= COVERAGE_VISIBLE_SCALE) {
      const cellX = Math.floor(groundX / coverage.cell_size);
      const cellZ = Math.floor(groundZ / coverage.cell_size);
      const cell = state.coverageIndex.get(indexKey(cellX, cellZ));
      if (cell) {
        coverageSelection = {
          kind: 'construction coverage',
          x: (cell.x + 0.5) * coverage.cell_size,
          z: (cell.z + 0.5) * coverage.cell_size,
          data: { ...cell, cell_size: coverage.cell_size, aggregate: true },
        };
      }
    }
    showSelection(best || coverageSelection);
  }

  function showSelection(selection) {
    if (!selection) {
      details.textContent = 'No visible marker at that coordinate.';
      return;
    }
    const data = selection.data;
    const lines = [
      selection.kind.toUpperCase(),
      `x ${selection.x.toFixed(1)} · z ${selection.z.toFixed(1)}`,
    ];
    if (data.aggregate_count) {
      lines.push(`clustered markers: ${data.aggregate_count}`);
    } else if (data.aggregate) {
      lines.push(`aggregated pieces: ${data.pieces}`, `coverage cell: ${data.cell_size} m × ${data.cell_size} m`);
    }
    if (data.creator !== undefined || data.aggregate) {
      // Naming the builder is the point of the colour: over terrain, at map scale, a hue alone does
      // not answer "whose is this".
      lines.push(`builder: ${builderName(data.creator || 0)}`);
      if (data.builders > 1) {
        lines.push(`shared with ${data.builders - 1} other builder(s) - the colour shows the majority`);
      }
    }
    if (data.name) lines.push(`name: ${data.name}`);
    if (data.generated !== undefined) lines.push(`generated: ${data.generated ? 'yes' : 'not yet'}`);
    if (data.id !== undefined) lines.push(`id: ${data.id}`);
    if (data.prefab) lines.push(`prefab: ${data.prefab}`);
    if (data.prefab_hash !== undefined) lines.push(`prefab hash: ${data.prefab_hash}`);
    if (data.pieces && !data.aggregate) lines.push(`pieces: ${data.pieces}`, `radius: ${data.radius.toFixed(1)} m`);
    if (data.connection_hash) lines.push(`connection: ${data.connection_hash}`);
    if (data.inventory) {
      lines.push(`inventory v${data.inventory.version}: ${data.inventory.items.length} stacks`);
      for (const item of data.inventory.items) {
        lines.push(`  ${item.name} × ${item.stack}${item.quality > 1 ? ` (quality ${item.quality})` : ''}`);
      }
    }
    if (data.inventory_warning) lines.push(`inventory warning: ${data.inventory_warning}`);
    if (data.properties) {
      for (const property of data.properties) {
        const value = typeof property.value === 'object' ? JSON.stringify(property.value) : property.value;
        lines.push(`${property.name || property.hash}: ${value}`);
      }
    }
    details.textContent = lines.join('\n');
  }

  function renderCategorySummary(snapshot) {
    const summarized = snapshot.summary.categories || {};
    const counts = { ...summarized };
    for (const object of snapshot.objects || []) {
      if (summarized[object.category] === undefined) {
        counts[object.category] = (counts[object.category] || 0) + 1;
      }
    }
    const preferred = ['construction', 'portal', 'container', 'production', 'creature', 'terrain', 'world', 'unknown'];
    const categories = [
      ...preferred.filter((category) => counts[category] !== undefined),
      ...Object.keys(counts).filter((category) => !preferred.includes(category)).sort(),
    ];
    const locationCounts = { ...(snapshot.summary.location_categories || {}) };
    if (Object.keys(locationCounts).length === 0) {
      for (const location of snapshot.locations || []) {
        const category = locationCategory(location);
        locationCounts[category] = (locationCounts[category] || 0) + 1;
      }
    }
    const locationOrder = ['boss', 'trader', 'dungeon', 'fortress', 'settlement', 'resource', 'landmark', 'other'];
    categorySummary.replaceChildren();
    for (const category of categories) {
      const row = document.createElement('div');
      const term = document.createElement('dt');
      const description = document.createElement('dd');
      term.textContent = categoryLabel(category);
      description.textContent = `${Number(counts[category]).toLocaleString()} · ${categoryRepresentation(category, snapshot)}`;
      row.append(term, description);
      categorySummary.appendChild(row);
    }
    for (const category of locationOrder) {
      if (locationCounts[category] === undefined) continue;
      const row = document.createElement('div');
      const term = document.createElement('dt');
      const description = document.createElement('dd');
      term.textContent = `${categoryLabel(category)} locations`;
      description.textContent = `${Number(locationCounts[category]).toLocaleString()} · independently filterable map glyphs`;
      row.append(term, description);
      categorySummary.appendChild(row);
    }
    if (categories.length === 0 && Object.keys(locationCounts).length === 0) {
      const row = document.createElement('div');
      const term = document.createElement('dt');
      const description = document.createElement('dd');
      term.textContent = 'Objects';
      description.textContent = 'No categorized objects in this snapshot.';
      row.append(term, description);
      categorySummary.appendChild(row);
    }
  }

  function categoryLabel(category) {
    return {
      construction: 'Player-built pieces',
      portal: 'Portals',
      container: 'Containers',
      production: 'Production',
      creature: 'Persistent creatures',
      terrain: 'Terrain edits',
      boss: 'Boss',
      trader: 'Trader',
      dungeon: 'Dungeon',
      fortress: 'Fortress',
      settlement: 'Settlement',
      resource: 'Resource',
      landmark: 'Landmark',
      other: 'Other',
      world: 'Routine world state',
      unknown: 'Uncatalogued prefabs',
    }[category] || category;
  }

  function categoryRepresentation(category, snapshot) {
    if (category === 'construction') {
      const coverage = snapshot.construction_coverage;
      return coverage
        ? `${coverage.cells.length.toLocaleString()} bounded ground-coverage cells`
        : 'aggregated settlement clusters (legacy snapshot)';
    }
    if (category === 'world') return 'summary count; routine non-spatial ZDOs omitted';
    if (category === 'unknown') return 'summary count; connected entries use the Other layer';
    if (category === 'terrain') return 'Terrain / upgrade risk glyph layer';
    if (['portal', 'container', 'production', 'creature'].includes(category)) return `${categoryLabel(category)} glyph layer`;
    return 'Other glyph layer when retained; otherwise summary count';
  }

  function renderAnalysis(data) {
    state.data = data;
    maskBits = decodeMask(data.explored_mask);
    buildIndexes(data.snapshot);
    renderCategorySummary(data.snapshot);
    const snapshotHealth = data.snapshot.health;
    const healthLines = [
      `${snapshotHealth.level.toUpperCase()} · ${data.snapshot.summary.objects.toLocaleString()} objects · ${data.snapshot.summary.generated_zones.toLocaleString()} generated zones`,
      ...(snapshotHealth.findings || []),
    ];
    health.textContent = healthLines.join('\n');
    if (data.diff) {
      const categoryDeltas = Object.entries(data.diff.category_delta || {})
        .filter(([, delta]) => delta !== 0)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([category, delta]) => `${categoryLabel(category)}: ${signed(delta)}`);
      diffBox.textContent = [
        `${data.diff.older} → ${data.diff.newer}`,
        `Objects: ${signed(data.diff.object_delta)}`,
        `Zones: ${signed(data.diff.zone_delta)}`,
        ...categoryDeltas,
        ...(data.diff.findings || []),
      ].join('\n');
    }
    recommendations.replaceChildren();
    for (const text of data.recommendations || []) {
      const item = document.createElement('li');
      item.textContent = text;
      recommendations.appendChild(item);
    }
    if (!terrainFailed) status.hidden = true;
    fit();
  }

  function signed(number) {
    return `${number >= 0 ? '+' : ''}${number.toLocaleString()}`;
  }

  canvas.addEventListener('pointerdown', (event) => {
    canvas.setPointerCapture(event.pointerId);
    state.drag = {
      x: event.clientX,
      y: event.clientY,
      centerX: state.x,
      centerZ: state.z,
      moved: false,
    };
  });
  canvas.addEventListener('pointermove', (event) => {
    const rect = canvas.getBoundingClientRect();
    const [x, z] = ground(event.clientX - rect.left, event.clientY - rect.top);
    const biome = biomeAt(x, z);
    coords.textContent = `x ${x.toFixed(0)} · z ${z.toFixed(0)}${biome ? ` · ${biome.label}` : ''}`;
    if (!state.drag) return;
    const deltaX = event.clientX - state.drag.x;
    const deltaY = event.clientY - state.drag.y;
    if (Math.hypot(deltaX, deltaY) > DRAG_THRESHOLD) state.drag.moved = true;
    state.x = state.drag.centerX - deltaX / state.scale;
    state.z = state.drag.centerZ + deltaY / state.scale;
    requestDraw();
  });
  canvas.addEventListener('pointerup', (event) => {
    if (state.drag && !state.drag.moved) {
      const rect = canvas.getBoundingClientRect();
      selectAt(event.clientX - rect.left, event.clientY - rect.top);
    }
    state.drag = null;
  });
  canvas.addEventListener('pointercancel', () => {
    state.drag = null;
  });
  for (const button of document.querySelectorAll('[data-locate]')) {
    button.addEventListener('click', (event) => {
      // The button sits inside a <summary>, which would otherwise toggle the row open on a click
      // that only asked to look at the map.
      event.preventDefault();
      event.stopPropagation();
      const x = Number(button.getAttribute('data-locate-x'));
      const z = Number(button.getAttribute('data-locate-z'));
      if (!Number.isFinite(x) || !Number.isFinite(z)) return;
      state.x = x;
      state.z = z;
      // Close enough that the coverage layer paints, which is what makes a builder visible at all.
      state.scale = Math.max(state.scale, COVERAGE_FULL_SCALE);
      const layer = document.querySelector('[data-layer=clusters]');
      if (layer && !layer.checked) layer.click();
      canvas.focus();
      requestDraw();
    });
  }
  canvas.addEventListener('wheel', (event) => {
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const factor = Math.exp(-event.deltaY * WHEEL_RATE);
    zoomAt(state.scale * factor, event.clientX - rect.left, event.clientY - rect.top);
  }, { passive: false });
  canvas.addEventListener('keydown', (event) => {
    const distance = KEYBOARD_PAN_PIXELS / state.scale;
    if (event.key === 'ArrowLeft') state.x -= distance;
    else if (event.key === 'ArrowRight') state.x += distance;
    else if (event.key === 'ArrowUp') state.z += distance;
    else if (event.key === 'ArrowDown') state.z -= distance;
    else if (event.key === '+' || event.key === '=') zoomAt(state.scale * ZOOM_STEP);
    else if (event.key === '-') zoomAt(state.scale / ZOOM_STEP);
    else if (event.key === 'Home') fit();
    else return;
    event.preventDefault();
    requestDraw();
  });

  terrainManifestReady.then(() => fetch(
    `${dataBase}/analysis.json${terrainManifest ? '?summary=1' : ''}`,
    { credentials: 'same-origin' },
  ))
    .then((response) => {
      if (!response.ok) throw new Error(`analysis ${response.status}`);
      return response.json();
    })
    .then(renderAnalysis)
    .catch((error) => {
      status.hidden = false;
      status.textContent = `Unable to load analysis: ${error.message}`;
      details.textContent = 'Map markers and save-state details are unavailable.';
    });

  new ResizeObserver(resize).observe(canvas);
  resize();
})();
