//go:build windows

package main

import (
	"errors"
	"fmt"
	"runtime"
	"syscall"
	"unsafe"

	"github.com/lxn/win"
	"golang.org/x/sys/windows"
)

var errFolderSelectionCanceled = errors.New("Steam Valheim folder selection was canceled")

const (
	folderPickerPickFolders     = uint32(0x00000020)
	folderPickerForceFilesystem = uint32(0x00000040)
	folderPickerPathMustExist   = uint32(0x00000800)
	folderPickerNoChangeDir     = uint32(0x00000008)
	folderPickerFileSystemPath  = uint32(0x80058000)
	folderPickerCanceled        = uint32(0x800704c7)
)

var (
	folderPickerCLSID = win.CLSID{Data1: 0xdc1c5a9c, Data2: 0xe88a, Data3: 0x4dde, Data4: [8]byte{0xa5, 0xa1, 0x60, 0xf8, 0x2a, 0x20, 0xae, 0xf7}}
	folderPickerIID   = win.IID{Data1: 0xd57c7288, Data2: 0xd4ad, Data3: 0x4768, Data4: [8]byte{0xbe, 0x02, 0x9d, 0x96, 0x95, 0x32, 0xd9, 0x60}}
	shellItemIID      = win.IID{Data1: 0x43826d1e, Data2: 0xe718, Data3: 0x42ee, Data4: [8]byte{0xbc, 0x55, 0xa1, 0xe2, 0x61, 0xc3, 0x7b, 0xfe}}
	computerFolderID  = win.CLSID{Data1: 0x0ac0837c, Data2: 0xbbf8, Data3: 0x452a, Data4: [8]byte{0x85, 0x0d, 0x79, 0xd0, 0x8e, 0x66, 0x7c, 0xa7}}
	shell32DLL        = windows.NewLazySystemDLL("shell32.dll")
	createShellItem   = shell32DLL.NewProc("SHCreateItemFromParsingName")
	getKnownFolder    = shell32DLL.NewProc("SHGetKnownFolderItem")
)

type fileOpenDialog struct {
	vtable *fileOpenDialogVTable
}

type fileOpenDialogVTable struct {
	queryInterface      uintptr
	addRef              uintptr
	release             uintptr
	show                uintptr
	setFileTypes        uintptr
	setFileTypeIndex    uintptr
	getFileTypeIndex    uintptr
	advise              uintptr
	unadvise            uintptr
	setOptions          uintptr
	getOptions          uintptr
	setDefaultFolder    uintptr
	setFolder           uintptr
	getFolder           uintptr
	getCurrentSelection uintptr
	setFileName         uintptr
	getFileName         uintptr
	setTitle            uintptr
	setOKButtonLabel    uintptr
	setFileNameLabel    uintptr
	getResult           uintptr
	addPlace            uintptr
	setDefaultExtension uintptr
	close               uintptr
	setClientGUID       uintptr
	clearClientData     uintptr
	setFilter           uintptr
	getResults          uintptr
	getSelectedItems    uintptr
}

type shellItem struct {
	vtable *shellItemVTable
}

type shellItemVTable struct {
	queryInterface uintptr
	addRef         uintptr
	release        uintptr
	bindToHandler  uintptr
	getParent      uintptr
	getDisplayName uintptr
	getAttributes  uintptr
	compare        uintptr
}

func pickWindowsFolder(owner win.HWND, initialDirectory string) (string, error) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()

	if result := win.OleInitialize(); win.FAILED(result) {
		return "", folderPickerError("initialize the Windows folder picker", result)
	}
	defer win.OleUninitialize()

	var raw unsafe.Pointer
	if result := win.CoCreateInstance(&folderPickerCLSID, nil, win.CLSCTX_INPROC_SERVER, &folderPickerIID, &raw); win.FAILED(result) {
		return "", folderPickerError("create the Windows folder picker", result)
	}
	dialog := (*fileOpenDialog)(raw)
	defer dialog.release()

	options, result := dialog.options()
	if win.FAILED(result) {
		return "", folderPickerError("read Windows folder picker options", result)
	}
	options |= folderPickerPickFolders | folderPickerForceFilesystem | folderPickerPathMustExist | folderPickerNoChangeDir
	if result := dialog.setOptions(options); win.FAILED(result) {
		return "", folderPickerError("configure the Windows folder picker", result)
	}
	if result := dialog.setTitle("Select your Steam Valheim folder"); win.FAILED(result) {
		return "", folderPickerError("set the Windows folder picker title", result)
	}
	if result := dialog.setOKButtonLabel("Select Valheim folder"); win.FAILED(result) {
		return "", folderPickerError("set the Windows folder picker button", result)
	}

	start := shellItemForStartingFolder(initialDirectory)
	if start != nil {
		_ = dialog.setFolder(start)
		start.release()
	}

	if result := dialog.show(owner); win.FAILED(result) {
		if uint32(result) == folderPickerCanceled {
			return "", errFolderSelectionCanceled
		}
		return "", folderPickerError("show the Windows folder picker", result)
	}
	item, result := dialog.result()
	if win.FAILED(result) {
		return "", folderPickerError("read the selected Windows folder", result)
	}
	defer item.release()
	path, result := item.fileSystemPath()
	if win.FAILED(result) {
		return "", folderPickerError("read the selected folder path", result)
	}
	return path, nil
}

func shellItemForStartingFolder(initialDirectory string) *shellItem {
	if initialDirectory != "" {
		if item, result := shellItemFromPath(initialDirectory); win.SUCCEEDED(result) {
			return item
		}
	}
	var item *shellItem
	result, _, _ := getKnownFolder.Call(
		uintptr(unsafe.Pointer(&computerFolderID)),
		0,
		0,
		uintptr(unsafe.Pointer(&shellItemIID)),
		uintptr(unsafe.Pointer(&item)),
	)
	if win.FAILED(win.HRESULT(result)) {
		return nil
	}
	return item
}

func shellItemFromPath(path string) (*shellItem, win.HRESULT) {
	pathPointer, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return nil, win.HRESULT(-2147024809)
	}
	var item *shellItem
	result, _, _ := createShellItem.Call(
		uintptr(unsafe.Pointer(pathPointer)),
		0,
		uintptr(unsafe.Pointer(&shellItemIID)),
		uintptr(unsafe.Pointer(&item)),
	)
	return item, win.HRESULT(result)
}

func (dialog *fileOpenDialog) release() {
	syscall.SyscallN(dialog.vtable.release, uintptr(unsafe.Pointer(dialog)))
}

func (dialog *fileOpenDialog) show(owner win.HWND) win.HRESULT {
	result, _, _ := syscall.SyscallN(dialog.vtable.show, uintptr(unsafe.Pointer(dialog)), uintptr(owner))
	return win.HRESULT(result)
}

func (dialog *fileOpenDialog) options() (uint32, win.HRESULT) {
	var options uint32
	result, _, _ := syscall.SyscallN(dialog.vtable.getOptions, uintptr(unsafe.Pointer(dialog)), uintptr(unsafe.Pointer(&options)))
	return options, win.HRESULT(result)
}

func (dialog *fileOpenDialog) setOptions(options uint32) win.HRESULT {
	result, _, _ := syscall.SyscallN(dialog.vtable.setOptions, uintptr(unsafe.Pointer(dialog)), uintptr(options))
	return win.HRESULT(result)
}

func (dialog *fileOpenDialog) setFolder(item *shellItem) win.HRESULT {
	result, _, _ := syscall.SyscallN(dialog.vtable.setFolder, uintptr(unsafe.Pointer(dialog)), uintptr(unsafe.Pointer(item)))
	return win.HRESULT(result)
}

func (dialog *fileOpenDialog) setTitle(title string) win.HRESULT {
	value, err := windows.UTF16PtrFromString(title)
	if err != nil {
		return win.HRESULT(-2147024809)
	}
	result, _, _ := syscall.SyscallN(dialog.vtable.setTitle, uintptr(unsafe.Pointer(dialog)), uintptr(unsafe.Pointer(value)))
	return win.HRESULT(result)
}

func (dialog *fileOpenDialog) setOKButtonLabel(label string) win.HRESULT {
	value, err := windows.UTF16PtrFromString(label)
	if err != nil {
		return win.HRESULT(-2147024809)
	}
	result, _, _ := syscall.SyscallN(dialog.vtable.setOKButtonLabel, uintptr(unsafe.Pointer(dialog)), uintptr(unsafe.Pointer(value)))
	return win.HRESULT(result)
}

func (dialog *fileOpenDialog) result() (*shellItem, win.HRESULT) {
	var item *shellItem
	result, _, _ := syscall.SyscallN(dialog.vtable.getResult, uintptr(unsafe.Pointer(dialog)), uintptr(unsafe.Pointer(&item)))
	return item, win.HRESULT(result)
}

func (item *shellItem) release() {
	syscall.SyscallN(item.vtable.release, uintptr(unsafe.Pointer(item)))
}

func (item *shellItem) fileSystemPath() (string, win.HRESULT) {
	var value *uint16
	result, _, _ := syscall.SyscallN(item.vtable.getDisplayName, uintptr(unsafe.Pointer(item)), uintptr(folderPickerFileSystemPath), uintptr(unsafe.Pointer(&value)))
	if win.FAILED(win.HRESULT(result)) {
		return "", win.HRESULT(result)
	}
	defer win.CoTaskMemFree(uintptr(unsafe.Pointer(value)))
	return windows.UTF16PtrToString(value), win.HRESULT(result)
}

func folderPickerError(action string, result win.HRESULT) error {
	return fmt.Errorf("%s (Windows error 0x%08X)", action, uint32(result))
}
