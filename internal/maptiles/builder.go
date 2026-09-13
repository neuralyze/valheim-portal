package maptiles

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"image"
	"image/png"
	"io"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"sync"
)

type BuildOptions struct {
	World           string
	Seed            string
	WorldGenVersion int
	Size            int
	Workers         int
	HeightPath      string
}

type mapColor struct {
	r float32
	g float32
	b float32
}

var webMapBiomeColors = map[uint32]mapColor{
	0x91a75b: {r: 0.573, g: 0.655, b: 0.361}, // Meadows
	0x525252: {r: 0.639, g: 0.447, b: 0.345}, // Swamp
	0xffffff: {r: 1, g: 1, b: 1},             // Mountain / Deep North
	0x345e3b: {r: 0.420, g: 0.455, b: 0.247}, // Black Forest
	0xc7c731: {r: 0.906, g: 0.671, b: 0.470}, // Plains
	0xff0000: {r: 0.690, g: 0.192, b: 0.192}, // Ashlands
	0xa37157: {r: 0.360, g: 0.220, b: 0.400}, // Mistlands
}

// webMapPalette flattens webMapBiomeColors so a sampled row carries one byte per pixel
// instead of a 12-byte mapColor. Entry 0 is the white an unrecognised biome colour has
// always fallen back to. At 12288x12288 this is 151 MB of live data instead of 1.7 GB,
// which is most of why the renderer now fits in the portal's 1 GiB container.
var (
	webMapPalette      []mapColor
	webMapPaletteIndex map[uint32]uint8
)

func init() {
	keys := make([]uint32, 0, len(webMapBiomeColors))
	for key := range webMapBiomeColors {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
	webMapPalette = make([]mapColor, 0, len(keys)+1)
	webMapPalette = append(webMapPalette, mapColor{r: 1, g: 1, b: 1})
	webMapPaletteIndex = make(map[uint32]uint8, len(keys))
	for _, key := range keys {
		webMapPaletteIndex[key] = uint8(len(webMapPalette))
		webMapPalette = append(webMapPalette, webMapBiomeColors[key])
	}
}

var (
	webMapDeepWater    = mapColor{r: 0.361058831, g: 0.361058831, b: 0.431372553}
	webMapShallowWater = mapColor{r: 0.574, g: 0.507092059, b: 0.478920251}
	webMapShore        = mapColor{r: 0.198113203, g: 0.122419007, b: 0.150394306}
	webMapLight        = [3]float32{-0.57735002, 0.57735002, 0.57735002}
)

const webMapStyleIdentity = "WebMap-2.7.1-ZoneSystemPatch.GetPixelColor+height-normal+water-lerp"

type tileResult struct {
	key, etag string
	err       error
}

// sampleSource yields the renderer's two inputs for one row of the size x size output
// grid. Rows are always requested in ascending order, which is what lets the streaming
// implementation read the PNG scanlines once and keep nothing behind it.
type sampleSource interface {
	sampleRow(y int, heights []float32, biomes []uint8) error
}

func Build(ctx context.Context, root, sourcePath string, options BuildOptions) (Manifest, error) {
	if !validName(options.World) {
		return Manifest{}, fmt.Errorf("invalid world name %q", options.World)
	}
	if options.Size == 0 {
		options.Size = DefaultSize
	}
	if options.Size < MinimumSize {
		return Manifest{}, fmt.Errorf("map size %d is below minimum %d", options.Size, MinimumSize)
	}
	if options.Workers <= 0 {
		options.Workers = max(1, min(runtime.GOMAXPROCS(0), 8))
	}
	if options.HeightPath == "" {
		return Manifest{}, fmt.Errorf("authoritative height source is required")
	}
	// Hashed by streaming rather than os.ReadFile: height.png is 177 MiB on every world
	// measured on this host, and the manifest only ever needed the digest.
	sourceHash, err := hashFile(sourcePath)
	if err != nil {
		return Manifest{}, err
	}
	heightHash, err := hashFile(options.HeightPath)
	if err != nil {
		return Manifest{}, err
	}
	styleHash := sha256.Sum256([]byte(webMapStyleIdentity))
	identity := sha256.New()
	fmt.Fprintf(identity, "%s\x00%s\x00%d\x00%d\x00%x\x00%x\x00%x", RendererVersion, options.Seed, options.WorldGenVersion, options.Size, sourceHash, heightHash, styleHash)
	key := hex.EncodeToString(identity.Sum(nil))
	if existing, err := LoadManifest(filepath.Join(root, "manifests", key+".json")); err == nil {
		if err := publishCurrent(root, existing); err != nil {
			return Manifest{}, err
		}
		return existing, nil
	}

	sourceHeader, err := readPNGHeader(sourcePath)
	if err != nil {
		return Manifest{}, fmt.Errorf("decode terrain source: %w", err)
	}
	heightHeader, err := readPNGHeader(options.HeightPath)
	if err != nil {
		return Manifest{}, fmt.Errorf("decode height source: %w", err)
	}
	if sourceHeader.width < options.Size || sourceHeader.height < options.Size {
		return Manifest{}, fmt.Errorf("terrain source dimensions (%d,%d) are below requested %dx%d", sourceHeader.width, sourceHeader.height, options.Size, options.Size)
	}
	// The renderer's live set is now a function of the source WIDTH, so an IHDR is the one
	// remaining input that could still make it unbounded. Every map source measured on this
	// host is exactly 12288x12288 - the same as the requested size - so four times that is
	// headroom no legitimate export has ever needed, and it keeps the per-level tile bands
	// (about 4 KB per source column across the whole pyramid) inside a few hundred MB.
	if sourceHeader.width > 4*options.Size {
		return Manifest{}, fmt.Errorf("terrain source dimensions (%d,%d) exceed four times the requested %dx%d", sourceHeader.width, sourceHeader.height, options.Size, options.Size)
	}
	if heightHeader.width != sourceHeader.width || heightHeader.height != sourceHeader.height {
		return Manifest{}, fmt.Errorf("height source dimensions (%d,%d) do not match terrain (%d,%d)", heightHeader.width, heightHeader.height, sourceHeader.width, sourceHeader.height)
	}
	sampler, err := newStreamSampler(sourcePath, options.HeightPath, options.Size)
	if err != nil {
		return Manifest{}, err
	}
	defer sampler.Close()

	levels := pyramidLevels(options.Size)
	manifest := Manifest{
		Schema: SchemaVersion, World: options.World, Seed: options.Seed, WorldGenVersion: options.WorldGenVersion,
		Renderer: RendererVersion, Key: key, SourceSHA256: hex.EncodeToString(sourceHash[:]),
		HeightSHA256: hex.EncodeToString(heightHash[:]), TextureSHA256: hex.EncodeToString(styleHash[:]),
		SourceWidth: sourceHeader.width, SourceHeight: sourceHeader.height, Width: options.Size, Height: options.Size,
		TileSize: TileSize, MaxZoom: len(levels) - 1, Format: "png",
		Bounds: Bounds{-WorldRadius, -WorldRadius, WorldRadius, WorldRadius}, Levels: levels,
		TileETags: make(map[string]string),
	}

	stage, err := os.MkdirTemp(filepath.Join(root, "staging"), key+"-")
	if os.IsNotExist(err) {
		if err := os.MkdirAll(filepath.Join(root, "staging"), 0o750); err != nil {
			return Manifest{}, err
		}
		stage, err = os.MkdirTemp(filepath.Join(root, "staging"), key+"-")
	}
	if err != nil {
		return Manifest{}, err
	}
	defer os.RemoveAll(stage)

	// Every zoom level is filled from the same single pass over the rendered rows. The old
	// shape held one 12288x12288 RGBA image (576 MiB) and re-sampled it per level; this
	// holds one tile-row band per level, 24,192 rows of output in total, about 50 MB.
	bands := make([]*levelBand, len(levels))
	for i, level := range levels {
		bands[i] = newLevelBand(level, options.Size)
	}
	writer := &tileWriter{ctx: ctx, stage: stage, workers: options.Workers, etags: manifest.TileETags}
	err = renderWebMapRows(sampler, options.Size, func(y int, row []byte) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		for _, band := range bands {
			if err := band.consume(y, row, writer); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		return Manifest{}, err
	}
	if err := ctx.Err(); err != nil {
		return Manifest{}, err
	}

	objectRoot := filepath.Join(root, "objects", key)
	if err := os.MkdirAll(objectRoot, 0o750); err != nil {
		return Manifest{}, err
	}
	if err := os.Rename(filepath.Join(stage, "terrain"), filepath.Join(objectRoot, "terrain")); err != nil {
		if !os.IsExist(err) {
			return Manifest{}, err
		}
	}
	if err := writeManifest(filepath.Join(root, "manifests", key+".json"), manifest); err != nil {
		return Manifest{}, err
	}
	if err := publishCurrent(root, manifest); err != nil {
		return Manifest{}, err
	}
	return manifest, nil
}

func hashFile(path string) ([32]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return [32]byte{}, err
	}
	defer file.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return [32]byte{}, err
	}
	var sum [32]byte
	copy(sum[:], digest.Sum(nil))
	return sum, nil
}

func pyramidLevels(size int) []Level {
	widths := []int{size}
	for widths[len(widths)-1] > TileSize {
		widths = append(widths, (widths[len(widths)-1]+1)/2)
	}
	sort.Ints(widths)
	levels := make([]Level, len(widths))
	for zoom, width := range widths {
		levels[zoom] = Level{Zoom: zoom, Width: width, Height: width, TilesWide: (width + TileSize - 1) / TileSize, TilesHigh: (width + TileSize - 1) / TileSize}
	}
	return levels
}

// levelBand accumulates one tile-row of a single zoom level. The pixel a level takes for
// output column c and output row o is rendered pixel (c*size/Width, o*size/Width) -- the
// same nearest-neighbour mapping the previous per-level re-sampling used, so the tiles
// are byte-identical.
type levelBand struct {
	level   Level
	size    int
	columns []int32
	band    []byte
	rows    int
	first   int
	next    int
}

func newLevelBand(level Level, size int) *levelBand {
	columns := make([]int32, level.Width)
	for c := range columns {
		columns[c] = int32(min(size-1, c*size/level.Width))
	}
	height := min(TileSize, level.Width)
	return &levelBand{level: level, size: size, columns: columns, band: make([]byte, height*level.Width*4)}
}

func (b *levelBand) sourceRow(output int) int {
	return min(b.size-1, output*b.size/b.level.Width)
}

func (b *levelBand) consume(renderedRow int, row []byte, writer *tileWriter) error {
	if b.next >= b.level.Width || b.sourceRow(b.next) != renderedRow {
		return nil
	}
	destination := b.band[b.rows*b.level.Width*4:]
	for c, sx := range b.columns {
		copy(destination[c*4:c*4+4], row[sx*4:sx*4+4])
	}
	b.rows++
	b.next++
	if b.rows == TileSize || b.next == b.level.Width {
		return b.flush(writer)
	}
	return nil
}

func (b *levelBand) flush(writer *tileWriter) error {
	err := writer.writeBand(b.level, b.first/TileSize, b.rows, b.level.Width, b.band)
	b.first += b.rows
	b.rows = 0
	return err
}

// tileWriter encodes the tiles of one band. The band is reused as soon as it returns, so
// writeBand is synchronous; the parallelism is across the tiles within a band, which at
// full zoom is 24 of them.
type tileWriter struct {
	ctx     context.Context
	stage   string
	workers int
	etags   map[string]string
}

func (w *tileWriter) writeBand(level Level, tileY, rows, width int, band []byte) error {
	if rows == 0 {
		return nil
	}
	jobs := make(chan int, level.TilesWide)
	results := make(chan tileResult, level.TilesWide)
	var group sync.WaitGroup
	for range min(w.workers, level.TilesWide) {
		group.Add(1)
		go func() {
			defer group.Done()
			for tileX := range jobs {
				results <- w.writeTile(level, tileX, tileY, rows, width, band)
			}
		}()
	}
	for tileX := range level.TilesWide {
		jobs <- tileX
	}
	close(jobs)
	group.Wait()
	close(results)
	for result := range results {
		if result.err != nil {
			return result.err
		}
		w.etags[result.key] = result.etag
	}
	return nil
}

func (w *tileWriter) writeTile(level Level, tileX, tileY, rows, width int, band []byte) tileResult {
	if err := w.ctx.Err(); err != nil {
		return tileResult{err: err}
	}
	tileWidth := min(TileSize, width-tileX*TileSize)
	tile := image.NewRGBA(image.Rect(0, 0, tileWidth, rows))
	for y := range rows {
		start := y*width*4 + tileX*TileSize*4
		copy(tile.Pix[y*tile.Stride:(y+1)*tile.Stride], band[start:start+tileWidth*4])
	}
	path := filepath.Join(w.stage, "terrain", fmt.Sprint(level.Zoom), fmt.Sprint(tileX), fmt.Sprintf("%d.png", tileY))
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return tileResult{err: err}
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_EXCL, 0o640)
	if err != nil {
		return tileResult{err: err}
	}
	hash := sha256.New()
	err = png.Encode(io.MultiWriter(file, hash), tile)
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return tileResult{err: err}
	}
	return tileResult{key: fmt.Sprintf("%d/%d/%d", level.Zoom, tileX, tileY), etag: hex.EncodeToString(hash.Sum(nil))}
}

// renderWebMapRows emits the rendered map one row at a time. The normals need the rows
// above and below, and the left/right neighbours are the adjacent entries in row-major
// order -- including across a row boundary, which is what the previous flat-index version
// did and is therefore reproduced here rather than corrected.
func renderWebMapRows(source sampleSource, size int, emit func(y int, row []byte) error) error {
	var heights [3][]float32
	for i := range heights {
		heights[i] = make([]float32, size)
	}
	biomes := [2][]uint8{make([]uint8, size), make([]uint8, size)}
	row := make([]byte, size*4)
	if err := source.sampleRow(0, heights[0], biomes[0]); err != nil {
		return err
	}
	for y := range size {
		if y+1 < size {
			if err := source.sampleRow(y+1, heights[(y+1)%3], biomes[(y+1)%2]); err != nil {
				return err
			}
		}
		current := heights[y%3]
		above, below := current, current
		if y > 0 {
			above = heights[(y+2)%3]
		}
		if y+1 < size {
			below = heights[(y+1)%3]
		}
		biome := biomes[y%2]
		for x := range size {
			height := current[x]
			left, right := height, height
			switch {
			case x > 0:
				left = current[x-1]
			case y > 0:
				left = heights[(y+2)%3][size-1]
			}
			switch {
			case x+1 < size:
				right = current[x+1]
			case y+1 < size:
				right = heights[(y+1)%3][0]
			}
			first := normalize3(2, 0, right-left)
			second := normalize3(0, 2, below[x]-above[x])
			normal := [3]float32{
				first[1]*second[2] - first[2]*second[1],
				first[2]*second[0] - first[0]*second[2],
				first[0]*second[1] - first[1]*second[0],
			}
			light := (normal[0]*webMapLight[0]+normal[1]*webMapLight[1]+normal[2]*webMapLight[2])*0.25 + 0.75
			pixel := lerpMapColor(webMapShore, webMapPalette[biome[x]], height-30)
			pixel = lerpMapColor(webMapShallowWater, pixel, (height-30+2.5)*0.5)
			pixel = lerpMapColor(webMapDeepWater, pixel, (height-30+12.5)*0.1)
			row[x*4] = colorByte(pixel.r * light)
			row[x*4+1] = colorByte(pixel.g * light)
			row[x*4+2] = colorByte(pixel.b * light)
			row[x*4+3] = 255
		}
		if err := emit(y, row); err != nil {
			return err
		}
	}
	return nil
}

// sampleStep is the source-to-output ratio both samplers share, and the place the
// "source must be an exact multiple of the output" rule is enforced.
func sampleStep(width, height, size int) (int, error) {
	if width != height || width%size != 0 {
		return 0, fmt.Errorf("terrain source (%d,%d) cannot produce an exact %dx%d WebMap image", width, height, size, size)
	}
	step := width / size
	if step < 1 {
		return 0, fmt.Errorf("terrain source (%d,%d) is smaller than output %dx%d", width, height, size, size)
	}
	return step, nil
}

// streamSampler walks both PNGs forward together. It never holds more than the scanlines
// it is on, which is the whole point: the pair decoded in full is 1.15 GiB.
type streamSampler struct {
	biomes  *pngRows
	heights *pngRows
	size    int
	step    int
	cursor  int
}

func newStreamSampler(biomePath, heightPath string, size int) (*streamSampler, error) {
	biomes, err := openPNGRows(biomePath)
	if err != nil {
		return nil, fmt.Errorf("decode terrain source: %w", err)
	}
	heights, err := openPNGRows(heightPath)
	if err != nil {
		biomes.Close()
		return nil, fmt.Errorf("decode height source: %w", err)
	}
	step, err := sampleStep(biomes.header.width, biomes.header.height, size)
	if err != nil {
		biomes.Close()
		heights.Close()
		return nil, err
	}
	return &streamSampler{biomes: biomes, heights: heights, size: size, step: step}, nil
}

func (s *streamSampler) Close() {
	s.biomes.Close()
	s.heights.Close()
}

func (s *streamSampler) sampleRow(y int, heights []float32, biomes []uint8) error {
	target := y*s.step + s.step/2
	var biomeRow, heightRow []byte
	for s.cursor <= target {
		var err error
		if biomeRow, err = s.biomes.next(); err != nil {
			return fmt.Errorf("decode terrain source: %w", err)
		}
		if heightRow, err = s.heights.next(); err != nil {
			return fmt.Errorf("decode height source: %w", err)
		}
		s.cursor++
	}
	gray := s.heights.gray16Source()
	for x := range s.size {
		sx := x*s.step + s.step/2
		if gray {
			heights[x] = float32(s.heights.gray16(heightRow, sx))/4 - 512
		} else {
			heights[x] = float32(s.heights.packedRGB(heightRow, sx))/8192 - 512
		}
		biomes[x] = webMapPaletteIndex[s.biomes.packedRGB(biomeRow, sx)]
	}
	return nil
}

// imageSampler is the in-memory counterpart, for callers that already hold decoded
// images. It shares renderWebMapRows with the streaming path so there is one copy of the
// pixel maths.
type imageSampler struct {
	biomes  image.Image
	heights image.Image
	bounds  image.Rectangle
	size    int
	step    int
}

func newImageSampler(biomes, heights image.Image, size int) (*imageSampler, error) {
	bounds := biomes.Bounds()
	step, err := sampleStep(bounds.Dx(), bounds.Dy(), size)
	if err != nil {
		return nil, err
	}
	return &imageSampler{biomes: biomes, heights: heights, bounds: bounds, size: size, step: step}, nil
}

func (s *imageSampler) sampleRow(y int, heights []float32, biomes []uint8) error {
	sy := s.bounds.Min.Y + y*s.step + s.step/2
	for x := range s.size {
		sx := s.bounds.Min.X + x*s.step + s.step/2
		heights[x] = sourceHeight(s.heights, sx, sy)
		r, g, b, _ := s.biomes.At(sx, sy).RGBA()
		biomes[x] = webMapPaletteIndex[uint32(r>>8)<<16|uint32(g>>8)<<8|uint32(b>>8)]
	}
	return nil
}

func normalize3(x, y, z float32) [3]float32 {
	length := float32(math.Sqrt(float64(x*x + y*y + z*z)))
	if length == 0 {
		return [3]float32{}
	}
	return [3]float32{x / length, y / length, z / length}
}

func sourceHeight(source image.Image, x, y int) float32 {
	if gray, ok := source.(*image.Gray16); ok {
		return float32(gray.Gray16At(x, y).Y)/4 - 512
	}
	r, g, b, _ := source.At(x, y).RGBA()
	encoded := uint32(r>>8)<<16 | uint32(g>>8)<<8 | uint32(b>>8)
	return float32(encoded)/8192 - 512
}

func lerpMapColor(from, to mapColor, amount float32) mapColor {
	amount = min(float32(1), max(float32(0), amount))
	return mapColor{
		r: from.r + (to.r-from.r)*amount,
		g: from.g + (to.g-from.g)*amount,
		b: from.b + (to.b-from.b)*amount,
	}
}

func colorByte(value float32) uint8 {
	return uint8(min(float64(255), max(float64(0), math.Round(float64(value*255)))))
}

func writeManifest(path string, manifest Manifest) error {
	if err := manifest.Validate(); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	data, err := json.Marshal(manifest)
	if err != nil {
		return err
	}
	temp := path + ".tmp"
	if err := os.WriteFile(temp, data, 0o640); err != nil {
		return err
	}
	return os.Rename(temp, path)
}

func publishCurrent(root string, manifest Manifest) error {
	path, err := CurrentManifestPath(root, manifest.World)
	if err != nil {
		return err
	}
	return writeManifest(path, manifest)
}
