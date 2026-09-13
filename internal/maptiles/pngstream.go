package maptiles

import (
	"bufio"
	"compress/zlib"
	"encoding/binary"
	"errors"
	"fmt"
	"hash"
	"hash/crc32"
	"io"
	"os"
)

// A scanline reader exists because image/png has no incremental API and the map sources
// are 12288x12288: png.Decode materialises 12288*12288*4 = 576 MiB per source, and Build
// needs both at once. On 2026-09-12 that cost the portal its container --
//
//	kernel: Memory cgroup out of memory: Killed process 1619955 (valheim-portal)
//	        total-vm:5093196kB, anon-rss:1010756kB ... oom_memcg=/system.slice/docker-fbdabf62....scope
//	dockerd: restarting container ... exitCode=137 restartCount=2
//
// against compose.yaml's mem_limit: 1g. Reading scanlines instead costs two rows, so the
// renderer's peak no longer scales with the square of the map.

var pngSignature = [8]byte{0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'}

type pngHeader struct {
	width     int
	height    int
	depth     int
	colorType int
	interlace int
}

func (h pngHeader) channels() int {
	switch h.colorType {
	case 0:
		return 1
	case 2:
		return 3
	case 4:
		return 2
	case 6:
		return 4
	}
	return 0
}

// bytesPerPixel is the PNG filter unit, which is what unfilter steps back by.
func (h pngHeader) bytesPerPixel() int { return h.channels() * h.depth / 8 }

func (h pngHeader) validate() error {
	if h.width <= 0 || h.height <= 0 {
		return fmt.Errorf("png: bad dimensions %dx%d", h.width, h.height)
	}
	if h.interlace != 0 {
		return errors.New("png: interlaced sources are not supported")
	}
	if h.depth != 8 && h.depth != 16 {
		return fmt.Errorf("png: bit depth %d is not supported", h.depth)
	}
	if h.channels() == 0 {
		return fmt.Errorf("png: colour type %d is not supported", h.colorType)
	}
	return nil
}

// readPNGHeader parses IHDR only, so Build can validate dimensions and reuse a cached
// manifest without decoding a single scanline.
func readPNGHeader(path string) (pngHeader, error) {
	file, err := os.Open(path)
	if err != nil {
		return pngHeader{}, err
	}
	defer file.Close()
	var head [33]byte
	if _, err := io.ReadFull(file, head[:]); err != nil {
		return pngHeader{}, fmt.Errorf("png: %s: %w", path, err)
	}
	if string(head[0:8]) != string(pngSignature[:]) || string(head[12:16]) != "IHDR" {
		return pngHeader{}, fmt.Errorf("png: %s is not a PNG", path)
	}
	header := pngHeader{
		width:     int(binary.BigEndian.Uint32(head[16:20])),
		height:    int(binary.BigEndian.Uint32(head[20:24])),
		depth:     int(head[24]),
		colorType: int(head[25]),
		interlace: int(head[28]),
	}
	if err := header.validate(); err != nil {
		return pngHeader{}, fmt.Errorf("%s: %w", path, err)
	}
	return header, nil
}

// idatReader concatenates the IDAT chunks into the single zlib stream PNG defines them
// to be, and verifies each chunk's CRC on the way past: these tiles are durable output,
// so a source that rotted on disk must fail the build rather than render as garbage.
type idatReader struct {
	source *bufio.Reader
	remain uint32
	crc    hash.Hash32
	done   bool
}

func (d *idatReader) Read(p []byte) (int, error) {
	for d.remain == 0 {
		if d.done {
			return 0, io.EOF
		}
		if err := d.nextChunk(); err != nil {
			return 0, err
		}
	}
	if uint32(len(p)) > d.remain {
		p = p[:d.remain]
	}
	n, err := io.ReadFull(d.source, p)
	d.crc.Write(p[:n])
	d.remain -= uint32(n)
	if err != nil {
		return n, err
	}
	if d.remain == 0 {
		var want [4]byte
		if _, err := io.ReadFull(d.source, want[:]); err != nil {
			return n, err
		}
		if binary.BigEndian.Uint32(want[:]) != d.crc.Sum32() {
			return n, errors.New("png: IDAT checksum mismatch")
		}
	}
	return n, nil
}

func (d *idatReader) nextChunk() error {
	for {
		var head [8]byte
		if _, err := io.ReadFull(d.source, head[:]); err != nil {
			return err
		}
		length := binary.BigEndian.Uint32(head[0:4])
		switch string(head[4:8]) {
		case "IDAT":
			if length == 0 {
				if _, err := io.CopyN(io.Discard, d.source, 4); err != nil {
					return err
				}
				continue
			}
			d.crc = crc32.NewIEEE()
			d.crc.Write(head[4:8])
			d.remain = length
			return nil
		case "IEND":
			d.done = true
			return io.EOF
		default:
			if _, err := io.CopyN(io.Discard, d.source, int64(length)+4); err != nil {
				return err
			}
		}
	}
}

type pngRows struct {
	file   *os.File
	header pngHeader
	zlib   io.ReadCloser
	bpp    int
	stride int
	cur    []byte
	prev   []byte
	filter [1]byte
	row    int
}

func openPNGRows(path string) (*pngRows, error) {
	header, err := readPNGHeader(path)
	if err != nil {
		return nil, err
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	source := bufio.NewReaderSize(file, 1<<16)
	if _, err := io.CopyN(io.Discard, source, 8); err != nil {
		file.Close()
		return nil, err
	}
	zr, err := zlib.NewReader(&idatReader{source: source})
	if err != nil {
		file.Close()
		return nil, fmt.Errorf("png: %s: %w", path, err)
	}
	rows := &pngRows{file: file, header: header, zlib: zr, bpp: header.bytesPerPixel()}
	rows.stride = header.width * rows.bpp
	rows.cur = make([]byte, rows.stride)
	rows.prev = make([]byte, rows.stride)
	return rows, nil
}

func (p *pngRows) Close() error {
	if p.zlib != nil {
		p.zlib.Close()
	}
	return p.file.Close()
}

// next returns the next unfiltered scanline. The slice is reused, so a caller that needs
// to keep a row must copy it.
func (p *pngRows) next() ([]byte, error) {
	if p.row >= p.header.height {
		return nil, io.EOF
	}
	if _, err := io.ReadFull(p.zlib, p.filter[:]); err != nil {
		return nil, err
	}
	if _, err := io.ReadFull(p.zlib, p.cur); err != nil {
		return nil, err
	}
	if err := unfilter(p.cur, p.prev, p.bpp, p.filter[0]); err != nil {
		return nil, err
	}
	p.cur, p.prev = p.prev, p.cur
	p.row++
	return p.prev, nil
}

func unfilter(cur, prev []byte, bpp int, method byte) error {
	switch method {
	case 0:
	case 1:
		for i := bpp; i < len(cur); i++ {
			cur[i] += cur[i-bpp]
		}
	case 2:
		for i := range cur {
			cur[i] += prev[i]
		}
	case 3:
		for i := 0; i < bpp && i < len(cur); i++ {
			cur[i] += prev[i] / 2
		}
		for i := bpp; i < len(cur); i++ {
			cur[i] += uint8((int(cur[i-bpp]) + int(prev[i])) / 2)
		}
	case 4:
		for i := 0; i < bpp && i < len(cur); i++ {
			cur[i] += prev[i]
		}
		for i := bpp; i < len(cur); i++ {
			cur[i] += paeth(cur[i-bpp], prev[i], prev[i-bpp])
		}
	default:
		return fmt.Errorf("png: unknown filter %d", method)
	}
	return nil
}

func paeth(a, b, c byte) byte {
	pc := int(c)
	pa := int(b) - pc
	pb := int(a) - pc
	pc = abs(pa + pb)
	pa = abs(pa)
	pb = abs(pb)
	if pa <= pb && pa <= pc {
		return a
	}
	if pb <= pc {
		return b
	}
	return c
}

func abs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

// gray16Source reports the one case sourceHeight treats specially: image/png decodes a
// 16-bit greyscale PNG to *image.Gray16, and the height maths reads Y directly instead of
// going through the alpha-premultiplied colour model.
func (p *pngRows) gray16Source() bool {
	return p.header.depth == 16 && p.header.colorType == 0
}

func (p *pngRows) gray16(row []byte, x int) uint16 {
	return binary.BigEndian.Uint16(row[x*2:])
}

// packedRGB returns (r>>8)<<16 | (g>>8)<<8 | (b>>8) for pixel x, which is what the callers
// computed from image.Image.At(x, y).RGBA(). RGBA() is alpha-premultiplied, so the
// premultiplication is reproduced here exactly rather than approximated.
func (p *pngRows) packedRGB(row []byte, x int) uint32 {
	switch {
	case p.header.depth == 8:
		switch p.header.colorType {
		case 0:
			y := uint32(row[x])
			return y<<16 | y<<8 | y
		case 2:
			i := x * 3
			return uint32(row[i])<<16 | uint32(row[i+1])<<8 | uint32(row[i+2])
		case 4:
			i := x * 2
			y := premultiply8(row[i], row[i+1])
			return y<<16 | y<<8 | y
		default:
			i := x * 4
			alpha := row[i+3]
			return premultiply8(row[i], alpha)<<16 | premultiply8(row[i+1], alpha)<<8 | premultiply8(row[i+2], alpha)
		}
	default:
		switch p.header.colorType {
		case 0:
			y := uint32(p.gray16(row, x) >> 8)
			return y<<16 | y<<8 | y
		case 2:
			i := x * 6
			return uint32(row[i])<<16 | uint32(row[i+2])<<8 | uint32(row[i+4])
		case 4:
			i := x * 4
			y := premultiply16(binary.BigEndian.Uint16(row[i:]), binary.BigEndian.Uint16(row[i+2:]))
			return y<<16 | y<<8 | y
		default:
			i := x * 8
			alpha := binary.BigEndian.Uint16(row[i+6:])
			return premultiply16(binary.BigEndian.Uint16(row[i:]), alpha)<<16 |
				premultiply16(binary.BigEndian.Uint16(row[i+2:]), alpha)<<8 |
				premultiply16(binary.BigEndian.Uint16(row[i+4:]), alpha)
		}
	}
}

// premultiply8 mirrors color.NRGBA.RGBA() and then takes the high byte.
func premultiply8(component, alpha uint8) uint32 {
	value := uint32(component)
	value |= value << 8
	value *= uint32(alpha)
	value /= 0xff
	return value >> 8
}

// premultiply16 mirrors color.NRGBA64.RGBA() and then takes the high byte.
func premultiply16(component, alpha uint16) uint32 {
	value := uint32(component) * uint32(alpha) / 0xffff
	return value >> 8
}
