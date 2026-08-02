package maptiles

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"image"
	"image/color"
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

var (
	webMapDeepWater    = mapColor{r: 0.361058831, g: 0.361058831, b: 0.431372553}
	webMapShallowWater = mapColor{r: 0.574, g: 0.507092059, b: 0.478920251}
	webMapShore        = mapColor{r: 0.198113203, g: 0.122419007, b: 0.150394306}
	webMapLight        = [3]float32{-0.57735002, 0.57735002, 0.57735002}
)

const webMapStyleIdentity = "WebMap-2.7.1-ZoneSystemPatch.GetPixelColor+height-normal+water-lerp"

type tileJob struct{ zoom, x, y, size, detailSize int }
type tileResult struct {
	key, etag string
	err       error
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
	sourceData, err := os.ReadFile(sourcePath)
	if err != nil {
		return Manifest{}, err
	}
	if options.HeightPath == "" {
		return Manifest{}, fmt.Errorf("authoritative height source is required")
	}
	heightData, err := os.ReadFile(options.HeightPath)
	if err != nil {
		return Manifest{}, err
	}
	styleHash := sha256.Sum256([]byte(webMapStyleIdentity))
	sourceHash := sha256.Sum256(sourceData)
	heightHash := sha256.Sum256(heightData)
	identity := sha256.New()
	fmt.Fprintf(identity, "%s\x00%s\x00%d\x00%d\x00%x\x00%x\x00%x", RendererVersion, options.Seed, options.WorldGenVersion, options.Size, sourceHash, heightHash, styleHash)
	key := hex.EncodeToString(identity.Sum(nil))
	if existing, err := LoadManifest(filepath.Join(root, "manifests", key+".json")); err == nil {
		if err := publishCurrent(root, existing); err != nil {
			return Manifest{}, err
		}
		return existing, nil
	}

	imageSource, err := png.Decode(bytes.NewReader(sourceData))
	if err != nil {
		return Manifest{}, fmt.Errorf("decode terrain source: %w", err)
	}
	heightSource, err := png.Decode(bytes.NewReader(heightData))
	if err != nil {
		return Manifest{}, fmt.Errorf("decode height source: %w", err)
	}
	bounds := imageSource.Bounds()
	if bounds.Dx() <= 0 || bounds.Dy() <= 0 {
		return Manifest{}, fmt.Errorf("empty terrain source")
	}
	if bounds.Dx() < options.Size || bounds.Dy() < options.Size {
		return Manifest{}, fmt.Errorf("terrain source dimensions %v are below requested %dx%d", bounds.Size(), options.Size, options.Size)
	}
	if heightSource.Bounds().Dx() != bounds.Dx() || heightSource.Bounds().Dy() != bounds.Dy() {
		return Manifest{}, fmt.Errorf("height source dimensions %v do not match terrain %v", heightSource.Bounds().Size(), bounds.Size())
	}
	renderedSource, err := renderWebMap(imageSource, heightSource, options.Size)
	if err != nil {
		return Manifest{}, err
	}
	levels := pyramidLevels(options.Size)
	manifest := Manifest{
		Schema: SchemaVersion, World: options.World, Seed: options.Seed, WorldGenVersion: options.WorldGenVersion,
		Renderer: RendererVersion, Key: key, SourceSHA256: hex.EncodeToString(sourceHash[:]),
		HeightSHA256: hex.EncodeToString(heightHash[:]), TextureSHA256: hex.EncodeToString(styleHash[:]),
		SourceWidth: bounds.Dx(), SourceHeight: bounds.Dy(), Width: options.Size, Height: options.Size,
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

	for _, level := range levels {
		count := level.TilesWide * level.TilesHigh
		jobs := make(chan tileJob, count)
		results := make(chan tileResult, count)
		var workers sync.WaitGroup
		for range min(options.Workers, count) {
			workers.Add(1)
			go func() {
				defer workers.Done()
				for job := range jobs {
					results <- renderTile(ctx, stage, renderedSource, job)
				}
			}()
		}
		for y := 0; y < level.TilesHigh; y++ {
			for x := 0; x < level.TilesWide; x++ {
				jobs <- tileJob{zoom: level.Zoom, x: x, y: y, size: level.Width, detailSize: options.Size}
			}
		}
		close(jobs)
		workers.Wait()
		close(results)
		for result := range results {
			if result.err != nil {
				return Manifest{}, result.err
			}
			manifest.TileETags[result.key] = result.etag
		}
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

func renderTile(ctx context.Context, stage string, source image.Image, job tileJob) tileResult {
	select {
	case <-ctx.Done():
		return tileResult{err: ctx.Err()}
	default:
	}
	width := min(TileSize, job.size-job.x*TileSize)
	height := min(TileSize, job.size-job.y*TileSize)
	tile := image.NewRGBA(image.Rect(0, 0, width, height))
	sb := source.Bounds()
	for py := 0; py < height; py++ {
		globalY := job.y*TileSize + py
		sy := sb.Min.Y + min(sb.Dy()-1, globalY*sb.Dy()/job.size)
		for px := 0; px < width; px++ {
			globalX := job.x*TileSize + px
			sx := sb.Min.X + min(sb.Dx()-1, globalX*sb.Dx()/job.size)
			tile.Set(px, py, source.At(sx, sy))
		}
	}
	path := filepath.Join(stage, "terrain", fmt.Sprint(job.zoom), fmt.Sprint(job.x), fmt.Sprintf("%d.png", job.y))
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return tileResult{err: err}
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_EXCL, 0o640)
	if err != nil {
		return tileResult{err: err}
	}
	hash := sha256.New()
	writer := io.MultiWriter(file, hash)
	err = png.Encode(writer, tile)
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return tileResult{err: err}
	}
	return tileResult{key: fmt.Sprintf("%d/%d/%d", job.zoom, job.x, job.y), etag: hex.EncodeToString(hash.Sum(nil))}
}

func renderWebMap(biomes, heights image.Image, size int) (*image.RGBA, error) {
	bounds := biomes.Bounds()
	if bounds.Dx() != bounds.Dy() || bounds.Dx()%size != 0 {
		return nil, fmt.Errorf("terrain source %v cannot produce an exact %dx%d WebMap image", bounds.Size(), size, size)
	}
	step := bounds.Dx() / size
	if step < 1 {
		return nil, fmt.Errorf("terrain source %v is smaller than output %dx%d", bounds.Size(), size, size)
	}

	count := size * size
	sampledHeights := make([]float32, count)
	sampledBiomes := make([]mapColor, count)
	for y := 0; y < size; y++ {
		sy := bounds.Min.Y + y*step + step/2
		for x := 0; x < size; x++ {
			sx := bounds.Min.X + x*step + step/2
			index := y*size + x
			sampledHeights[index] = sourceHeight(heights, sx, sy)
			r, g, b, _ := biomes.At(sx, sy).RGBA()
			base, ok := webMapBiomeColors[uint32(r>>8)<<16|uint32(g>>8)<<8|uint32(b>>8)]
			if !ok {
				base = mapColor{r: 1, g: 1, b: 1}
			}
			sampledBiomes[index] = base
		}
	}

	rendered := image.NewRGBA(image.Rect(0, 0, size, size))
	for index, height := range sampledHeights {
		up := index - size
		if up < 0 {
			up = index
		}
		down := index + size
		if down >= count {
			down = index
		}
		right := index + 1
		if right >= count {
			right = index
		}
		left := index - 1
		if left < 0 {
			left = index
		}
		first := normalize3(2, 0, sampledHeights[right]-sampledHeights[left])
		second := normalize3(0, 2, sampledHeights[down]-sampledHeights[up])
		normal := [3]float32{
			first[1]*second[2] - first[2]*second[1],
			first[2]*second[0] - first[0]*second[2],
			first[0]*second[1] - first[1]*second[0],
		}
		light := (normal[0]*webMapLight[0]+normal[1]*webMapLight[1]+normal[2]*webMapLight[2])*0.25 + 0.75
		pixel := lerpMapColor(webMapShore, sampledBiomes[index], height-30)
		pixel = lerpMapColor(webMapShallowWater, pixel, (height-30+2.5)*0.5)
		pixel = lerpMapColor(webMapDeepWater, pixel, (height-30+12.5)*0.1)
		x, y := index%size, index/size
		rendered.SetRGBA(x, y, color.RGBA{
			R: colorByte(pixel.r * light),
			G: colorByte(pixel.g * light),
			B: colorByte(pixel.b * light),
			A: 255,
		})
	}
	return rendered, nil
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
