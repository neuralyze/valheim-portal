#!/usr/bin/env python3
"""Stage 1 of VR mod onboarding: static VR-impact scan of Valheim mod packages.

Reads every .dll inside every package zip and reports the seven known
VHVR-incompatibility classes with concrete symbol evidence. Read-only with
respect to the packages directory; output goes to stdout and optionally --json.

Exit codes: 0 nothing at or above --min-severity, 1 findings at or above it,
2 the tool could not run.
"""
from __future__ import annotations
import argparse, json, logging, re, struct, sys, time, zipfile
from pathlib import Path
import dnfile
from vr_scan_common import (CLASS_RECALL, CLASS_TITLES, EXIT_CLEAN, EXIT_ERROR, EXIT_FLAGGED,
                            SEVERITIES, SEVERITY_WEIGHT, fail, manifest_entries, normalise,
                            split_package, write_json)

# dnfile logs malformed-metadata warnings straight to stderr. Mod assemblies are full of them
# and none are fatal here, so keep them out of the report; the skip list records real failures.
logging.getLogger('dnfile').setLevel(logging.CRITICAL)
logging.getLogger('dnfile.stream').setLevel(logging.CRITICAL)

STAGE = '1-static-vr-scan'
VHVR_ROOT = Path('/tmp/librarian-vhvr/ValheimVRMod')
VHVR_SOURCE = VHVR_ROOT / 'VRCore/UI/VRGUI.cs'
VHVR_CONTROLS = VHVR_ROOT / 'VRCore/UI/VRControls.cs'

# ---------------------------------------------------------------- known state

# Parsed out of VRGUI.cs ADDITIONAL_GUI_CANVAS_NAMES (~:60-79) at author time.
# --vhvr-source re-parses the real file so this cannot silently rot.
VHVR_CANVAS_NAMES = (
    'Chat', 'Connecting', 'EnemyHud', 'TextViewer', 'JoinCodeOverlay', 'Store_Screen',
    'ClosedCaptions', 'Inventory_screen', 'Menu', 'MiniMap', 'TopLeftMessage', 'TextInput',
    'HudMessage', 'ConnectionPanel', 'Tutorial', 'BarberGui', 'Scaled 3D Viewport',
)
# ensureGuiCanvas also matches MENU_GUI_CANVAS / PASSWORD_CANVAS directly (VRGUI.cs ~:757).
VHVR_CANVAS_NAMES_IMPLICIT = ('LoadingGUI', 'PasswordCanvas')

# ZInput actions VHVR actually binds to a SteamVR action (VRControls.cs ~:876-887), and the
# ones it deliberately blacklists at startup (initIgnoredZInputs :986-996). --vhvr-controls
# re-parses both so this cannot silently rot.
VHVR_ZINPUT_MAPPED = ('JoyMenu', 'Inventory', 'Jump', 'Use', 'Sit', 'AutoPickup', 'ToggleMiniMap',
                      'BuildMenu', 'JoyPlace', 'Remove')
VHVR_ZINPUT_IGNORED = (
    'AltPlace', 'Attack', 'AutoRun', 'Backward', 'Block', 'BuildNext', 'BuildPrev', 'ChatDown',
    'ChatUp', 'Crouch', 'Forward', 'GPower', 'Hide', 'JoyAttack', 'JoyBlock', 'JoyButtonA',
    'JoyButtonB', 'JoyButtonX', 'JoyButtonY', 'JoyCrouch', 'JoyDPadDown', 'JoyDPadLeft',
    'JoyDPadRight', 'JoyDPadUp', 'JoyGPower', 'JoyHide', 'JoyJump', 'JoyLStickDown',
    'JoyLStickLeft', 'JoyLStickRight', 'JoyLStickUp', 'JoyLTrigger', 'JoyMap', 'JoyRTrigger',
    'JoyRemove', 'JoyRotate', 'JoyRun', 'JoySecondAttack', 'JoySit', 'JoyTabLeft', 'JoyTabRight',
    'JoyUse', 'Left', 'Right', 'Run', 'ScrollChatDown', 'ScrollChatUp', 'SecondAttack', 'ToggleWalk')

# Canvas names our own client-side VR fixes already adopt or reparent.
# Extend with --adopt-list (newline-delimited, or a JSON list / {"canvas_names": [...]}).
OUR_ADOPT_LIST = ()

# Packages we already ship a bespoke VR fix for. Findings are annotated, not silenced.
OUR_HANDLED_PACKAGES = {
    'backpacks': 'Avo.BackpacksVRFix (BackpacksWristPanelPatch)',
    'backpacksvrfix': 'this IS one of our VR fixes',
    'epicloot': 'Epic Loot VR Fix (shipped in the VR runtime)',
    'creaturelevelandlootcontrol': 'CLLC VR Fix (shipped in the VR runtime)',
}

# Jotunn's GUIManager parents custom GUI under the vanilla GuiRoot PixelFix, which VHVR
# already converts. Seeing these symbols means the canvas path is the correct one.
JOTUNN_GUI_SYMBOLS = (
    'GUIManager::CreateCustomGUI', 'GUIManager::get_CustomGUIFront', 'GUIManager::get_CustomGUIBack',
    'GUIManager::get_PixelFix', 'GUIManager::ApplyButtonStyle', 'GUIManager::CreateWoodpanel',
    'GUIManager::CreateInputField', 'GUIManager::CreateButton',
)

DEPENDENCY_STEMS = {
    '0harmony', 'harmonyxinterop', 'mscorlib', 'netstandard', 'newtonsoft.json',
    'valve.newtonsoft.json', 'protobuf-net', 'yamldotnet', 'monomod.utils', 'monomod.runtimedetour',
    'mono.cecil', 'mono.cecil.mdb', 'mono.cecil.pdb', 'mono.cecil.rocks', 'semverfactory',
    'bepinex', 'bepinex.harmony', 'bepinex.preloader', 'bepinex.preloader.core', 'bepinex.core',
    'steamvr', 'steamvr_actions', 'final_ik', 'amplify_occlusion', 'ndesk.options',
}
DEPENDENCY_PREFIXES = ('system.', 'unity.', 'unityengine', 'microsoft.', 'mono.posix')
SKIP_PATH_PARTS = ('bepinex/core/', 'valheim_data/managed/', 'valheim_data/plugins/',
                   'doorstop_libs/', '__macosx/')

# ------------------------------------------------------------------- IL walk

_OP1 = [0] * 0x100
for _c in (0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13, 0x1F, 0xDE, *range(0x2B, 0x38)):
    _OP1[_c] = 1
for _c in (0x20, 0x22, 0x27, 0x28, 0x29, 0x6F, 0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x79,
           0x8D, 0x8F, 0xA3, 0xA4, 0xA5, 0xC2, 0xC6, 0xD0, 0xDD, *range(0x38, 0x45),
           *range(0x7B, 0x82)):
    _OP1[_c] = 4
for _c in (0x21, 0x23):
    _OP1[_c] = 8
_OP1[0x45] = -1  # switch

_OP2 = [0] * 0x100
for _c in (0x12, 0x19):
    _OP2[_c] = 1
for _c in (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E):
    _OP2[_c] = 2
for _c in (0x06, 0x07, 0x15, 0x16, 0x1C):
    _OP2[_c] = 4

_TOKEN_OPS = {0x28: 'call', 0x6F: 'callvirt', 0x73: 'newobj', 0xD0: 'ldtoken', 0x8D: 'newarr',
              0x74: 'castclass', 0x75: 'isinst', 0x7E: 'ldsfld', 0x7B: 'ldfld', 0x80: 'stsfld',
              0x7D: 'stfld', 0x27: 'jmp'}
_LDC_SMALL = {c: c - 0x16 for c in range(0x16, 0x1F)}
_LDC_SMALL[0x15] = -1

SIG_PRIMS = {0x02: 'System.Boolean', 0x03: 'System.Char', 0x04: 'System.SByte', 0x05: 'System.Byte',
             0x06: 'System.Int16', 0x07: 'System.UInt16', 0x08: 'System.Int32', 0x09: 'System.UInt32',
             0x0A: 'System.Int64', 0x0B: 'System.UInt64', 0x0C: 'System.Single', 0x0D: 'System.Double',
             0x0E: 'System.String', 0x1C: 'System.Object'}


def _compressed(blob, i):
    b = blob[i]
    if b & 0x80 == 0:
        return b, i + 1
    if b & 0xC0 == 0x80:
        return ((b & 0x3F) << 8) | blob[i + 1], i + 2
    return ((b & 0x1F) << 24) | (blob[i + 1] << 16) | (blob[i + 2] << 8) | blob[i + 3], i + 4


def _ser_string(blob, i):
    if i >= len(blob) or blob[i] == 0xFF:
        return None, i + 1
    length, i = _compressed(blob, i)
    return blob[i:i + length].decode('utf-8', 'replace'), i + length


class Assembly:
    """One managed assembly, with just enough metadata to resolve IL tokens."""

    def __init__(self, name, data):
        self.name = name
        self.pe = dnfile.dnPE(data=data)
        if self.pe.net is None or self.pe.net.mdtables is None:
            raise ValueError('not a managed assembly (no .NET metadata)')
        self.data = self.pe.__data__
        self.mt = self.pe.net.mdtables
        self.typerefs = set()
        self.memberrefs = set()
        self._method_owner = {}
        self._build_index()

    # -- metadata index -----------------------------------------------------
    def _build_index(self):
        mt = self.mt
        if mt.TypeDef:
            for trow in mt.TypeDef.rows:
                owner = self._full(trow.TypeNamespace, trow.TypeName)
                for idx in trow.MethodList or ():
                    self._method_owner[idx.row_index] = owner
        if mt.TypeRef:
            for row in mt.TypeRef.rows:
                self.typerefs.add(self._full(row.TypeNamespace, row.TypeName))
        if mt.MemberRef:
            for row in mt.MemberRef.rows:
                self.memberrefs.add(self._memberref_name(row))

    @staticmethod
    def _full(namespace, name):
        namespace, name = str(namespace or ''), str(name or '')
        return f'{namespace}.{name}' if namespace else name

    def _typedeforref(self, coded):
        tag, rid = coded & 3, coded >> 2
        table = {0: self.mt.TypeDef, 1: self.mt.TypeRef, 2: self.mt.TypeSpec}.get(tag)
        if not table or rid < 1 or rid > table.num_rows:
            return '?'
        if tag == 2:
            return '<typespec>'
        row = table.rows[rid - 1]
        return self._full(row.TypeNamespace, row.TypeName)

    def _owner_of(self, coded_index):
        row = getattr(coded_index, 'row', None)
        if row is None:
            return '?'
        if hasattr(row, 'TypeName'):
            return self._full(row.TypeNamespace, row.TypeName)
        if hasattr(row, 'Name'):
            return str(row.Name)
        return '?'

    def _memberref_name(self, row):
        return f'{self._owner_of(row.Class)}::{row.Name}'

    def _sig_type(self, blob, i):
        if i >= len(blob):
            return '?', i
        et = blob[i]
        i += 1
        if et in (0x11, 0x12):  # VALUETYPE / CLASS
            coded, i = _compressed(blob, i)
            return self._typedeforref(coded), i
        if et in (0x1D, 0x0F, 0x10):  # SZARRAY / PTR / BYREF
            inner, i = self._sig_type(blob, i)
            return inner + ('[]' if et == 0x1D else '*'), i
        if et == 0x15:  # GENERICINST
            base, i = self._sig_type(blob, i)
            count, i = _compressed(blob, i)
            args = []
            for _ in range(count):
                arg, i = self._sig_type(blob, i)
                args.append(arg)
            return f'{base}<{",".join(args)}>', i
        if et in (0x13, 0x1E):  # VAR / MVAR
            num, i = _compressed(blob, i)
            return f'!{num}', i
        return SIG_PRIMS.get(et, f'et{et:02x}'), i

    def _methodspec(self, row):
        base = row.Method.row
        name = self._memberref_name(base) if hasattr(base, 'Class') else str(base.Name)
        blob = row.Instantiation.value or b''
        args = []
        if len(blob) >= 2 and blob[0] == 0x0A:
            i = 1
            count, i = _compressed(blob, i)
            for _ in range(count):
                arg, i = self._sig_type(blob, i)
                args.append(arg)
        return f'{name}<{",".join(args)}>' if args else name

    def method_name(self, rid):
        row = self.mt.MethodDef.rows[rid - 1]
        return f'{self._method_owner.get(rid, "?")}::{row.Name}'

    def resolve(self, tok):
        table, rid = tok >> 24, tok & 0xFFFFFF
        try:
            if table == 0x0A and self.mt.MemberRef and rid <= self.mt.MemberRef.num_rows:
                return self._memberref_name(self.mt.MemberRef.rows[rid - 1])
            if table == 0x06 and self.mt.MethodDef and rid <= self.mt.MethodDef.num_rows:
                return self.method_name(rid)
            if table == 0x2B and self.mt.MethodSpec and rid <= self.mt.MethodSpec.num_rows:
                return self._methodspec(self.mt.MethodSpec.rows[rid - 1])
            if table == 0x01 and self.mt.TypeRef and rid <= self.mt.TypeRef.num_rows:
                row = self.mt.TypeRef.rows[rid - 1]
                return self._full(row.TypeNamespace, row.TypeName)
            if table == 0x02 and self.mt.TypeDef and rid <= self.mt.TypeDef.num_rows:
                row = self.mt.TypeDef.rows[rid - 1]
                return self._full(row.TypeNamespace, row.TypeName)
            if table == 0x04 and self.mt.Field and rid <= self.mt.Field.num_rows:
                return str(self.mt.Field.rows[rid - 1].Name)
            if table == 0x1B:
                return '<typespec>'
        except (IndexError, AttributeError, TypeError, ValueError):
            return '?'
        return '?'

    def user_string(self, tok):
        try:
            item = self.pe.net.user_strings.get(tok & 0xFFFFFF)
        except Exception:
            return None
        return item.value if item is not None else None

    def plugins(self):
        """[BepInPlugin(guid, name, version)] declarations, the join key for stage 5."""
        found = []
        if not self.mt.CustomAttribute:
            return found
        for row in self.mt.CustomAttribute.rows:
            try:
                ctor = row.Type.row
                owner = self._owner_of(ctor.Class) if hasattr(ctor, 'Class') else ''
            except Exception:
                continue
            if not owner.endswith('BepInPlugin'):
                continue
            blob = getattr(row.Value, 'value', None) or b''
            if len(blob) < 3 or blob[0] != 0x01:
                continue
            i = 2
            guid, i = _ser_string(blob, i)
            name, i = _ser_string(blob, i)
            version, i = _ser_string(blob, i)
            found.append({'guid': guid, 'name': name, 'version': version, 'assembly': self.name})
        return found

    # -- IL -----------------------------------------------------------------
    def body(self, rva):
        if not rva:
            return None
        try:
            off = self.pe.get_offset_from_rva(rva)
        except Exception:
            return None
        data = self.data
        if off is None or off + 1 > len(data):
            return None
        head = data[off]
        if head & 3 == 2:
            size, start = head >> 2, off + 1
        elif head & 3 == 3:
            if off + 12 > len(data):
                return None
            flags_size = struct.unpack_from('<H', data, off)[0]
            size = struct.unpack_from('<I', data, off + 4)[0]
            start = off + (flags_size >> 12) * 4
        else:
            return None
        end = start + size
        return data[start:end] if 0 < size and end <= len(data) else None

    def walk(self, code):
        """Yield (kind, value) for the instructions we care about, skipping the rest exactly."""
        i, n = 0, len(code)
        while i < n:
            op = code[i]
            i += 1
            if op == 0xFE:
                if i >= n:
                    return
                op2 = code[i]
                i += 1
                if op2 in (0x06, 0x07, 0x15) and i + 4 <= n:
                    yield 'call', self.resolve(struct.unpack_from('<I', code, i)[0])
                i += _OP2[op2]
                continue
            size = _OP1[op]
            if size == -1:
                if i + 4 > n:
                    return
                i += 4 + 4 * struct.unpack_from('<I', code, i)[0]
                continue
            if i + size > n:
                return
            if op == 0x72:  # ldstr
                text = self.user_string(struct.unpack_from('<I', code, i)[0])
                if text is not None:
                    yield 'ldstr', text
            elif op in _TOKEN_OPS:
                yield _TOKEN_OPS[op], self.resolve(struct.unpack_from('<I', code, i)[0])
            elif op == 0x20:
                yield 'ldc', struct.unpack_from('<i', code, i)[0]
            elif op == 0x1F:
                yield 'ldc', struct.unpack_from('<b', code, i)[0]
            elif op in _LDC_SMALL:
                yield 'ldc', _LDC_SMALL[op]
            i += size

    def methods(self):
        if not self.mt.MethodDef:
            return
        for rid, row in enumerate(self.mt.MethodDef.rows, start=1):
            code = self.body(row.Rva)
            if code:
                yield rid, self.method_name(rid), list(self.walk(code))


# --------------------------------------------------------------- KeyCode map

def _keycode_names():
    names = {0: 'None', 8: 'Backspace', 9: 'Tab', 12: 'Clear', 13: 'Return', 19: 'Pause',
             27: 'Escape', 32: 'Space', 127: 'Delete',
             33: 'Exclaim', 34: 'DoubleQuote', 35: 'Hash', 36: 'Dollar', 37: 'Percent',
             38: 'Ampersand', 39: 'Quote', 40: 'LeftParen', 41: 'RightParen', 42: 'Asterisk',
             43: 'Plus', 44: 'Comma', 45: 'Minus', 46: 'Period', 47: 'Slash', 58: 'Colon',
             59: 'Semicolon', 60: 'Less', 61: 'Equals', 62: 'Greater', 63: 'Question', 64: 'At',
             91: 'LeftBracket', 92: 'Backslash', 93: 'RightBracket', 94: 'Caret',
             95: 'Underscore', 96: 'BackQuote'}
    for d in range(10):
        names[48 + d] = f'Alpha{d}'
        names[256 + d] = f'Keypad{d}'
    for o in range(26):
        names[97 + o] = chr(ord('A') + o)
    names.update({266: 'KeypadPeriod', 267: 'KeypadDivide', 268: 'KeypadMultiply',
                  269: 'KeypadMinus', 270: 'KeypadPlus', 271: 'KeypadEnter', 272: 'KeypadEquals',
                  273: 'UpArrow', 274: 'DownArrow', 275: 'RightArrow', 276: 'LeftArrow',
                  277: 'Insert', 278: 'Home', 279: 'End', 280: 'PageUp', 281: 'PageDown'})
    for f in range(1, 16):
        names[281 + f] = f'F{f}'
    names.update({300: 'Numlock', 301: 'CapsLock', 302: 'ScrollLock', 303: 'RightShift',
                  304: 'LeftShift', 305: 'RightControl', 306: 'LeftControl', 307: 'RightAlt',
                  308: 'LeftAlt', 309: 'RightCommand', 310: 'LeftCommand', 311: 'LeftWindows',
                  312: 'RightWindows', 313: 'AltGr', 315: 'Help', 316: 'Print', 317: 'SysReq',
                  318: 'Break', 319: 'Menu'})
    for m in range(7):
        names[323 + m] = f'Mouse{m}'
    for j in range(20):
        names[330 + j] = f'JoystickButton{j}'
    return names


KEYCODES = _keycode_names()


def keycode(value):
    return KEYCODES.get(value, f'KeyCode({value})')


# ------------------------------------------------------------------ symbols

CANVAS_SET_MODE = 'UnityEngine.Canvas::set_renderMode'
CANVAS_ADD = 'UnityEngine.Canvas>'          # AddComponent<Canvas> / GetOrAddComponent<Canvas>
PARENT_SYMBOLS = ('Transform::SetParent', 'Transform::set_parent')
LAYER_SYMBOLS = ('GameObject::set_layer', 'LayerMask::NameToLayer', 'LayerMask::GetMask')
# Registration: raw ZInput, or Jotunn's InputManager which forwards to ZInput.AddButton.
ZINPUT_ADD = ('ZInput::AddButton', 'InputManager::AddButton')
# Consumption: a mod reading an action VHVR never bound is just as dead as one it never registered.
ZINPUT_READ = ('ZInput::GetButtonDown', 'ZInput::GetButtonUp', 'ZInput::GetButton',
               'ZInput::GetButtonLastDown', 'ZInput::GetButtonPressedTimer')
# Mods rarely pass the action name straight to AddButton; they wrap it in a config object
# (Jotunn's ButtonConfig.Name, Zen.ModLib's ControlInputs.Create, ...) and register that later.
# Harvesting the literal at the wrapper is how the real name is recovered.
ACTION_OWNER_HINTS = ('ButtonConfig', 'ControlInputs', 'ActionString', 'InputManager', 'ZInput',
                      'KeyBinding', 'Keybind', 'InputAction', 'ControlConfig')
ACTION_MEMBERS = ('set_Name', 'Create', '.ctor', 'AddButton', 'AddKeyBinding', 'Register')
ACTION_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_.]{2,48}$')
KEY_READ = {'UnityEngine.Input::GetKeyDown': 'Input.GetKeyDown',
            'UnityEngine.Input::GetKeyUp': 'Input.GetKeyUp',
            'UnityEngine.Input::GetKey': 'Input.GetKey'}
MOUSE_READ = {'UnityEngine.Input::GetMouseButtonDown': 'Input.GetMouseButtonDown',
              'UnityEngine.Input::GetMouseButtonUp': 'Input.GetMouseButtonUp',
              'UnityEngine.Input::GetMouseButton': 'Input.GetMouseButton',
              'UnityEngine.Input::get_mouseScrollDelta': 'Input.mouseScrollDelta',
              'UnityEngine.Input::get_mousePosition': 'Input.mousePosition'}
DRAG_INTERFACES = ('IDragHandler', 'IBeginDragHandler', 'IEndDragHandler', 'IScrollHandler',
                   'IInitializePotentialDragHandler')
PERF_SYMBOLS = {
    'QualitySettings::set_masterTextureLimit': ('high', 'QualitySettings.masterTextureLimit'),
    'QualitySettings::set_globalTextureMipmapLimit': ('high', 'QualitySettings.globalTextureMipmapLimit'),
    'QualitySettings::set_vSyncCount': ('high', 'QualitySettings.vSyncCount'),
    'QualitySettings::set_lodBias': ('high', 'QualitySettings.lodBias'),
    'QualitySettings::SetQualityLevel': ('high', 'QualitySettings.SetQualityLevel'),
    'QualitySettings::set_shadowDistance': ('medium', 'QualitySettings.shadowDistance'),
    'QualitySettings::set_antiAliasing': ('medium', 'QualitySettings.antiAliasing'),
    'QualitySettings::set_shadowResolution': ('medium', 'QualitySettings.shadowResolution'),
    'Application::set_targetFrameRate': ('high', 'Application.targetFrameRate'),
    'Screen::SetResolution': ('high', 'Screen.SetResolution'),
    'Screen::set_fullScreen': ('high', 'Screen.fullScreen'),
    'XRSettings::set_eyeTextureResolutionScale': ('high', 'XRSettings.eyeTextureResolutionScale'),
    'XRSettings::set_enabled': ('high', 'XRSettings.enabled'),
    'Camera::set_fieldOfView': ('high', 'Camera.fieldOfView'),
    'Camera::set_nearClipPlane': ('medium', 'Camera.nearClipPlane'),
    'Camera::set_farClipPlane': ('medium', 'Camera.farClipPlane'),
    'Camera::set_targetTexture': ('medium', 'Camera.targetTexture'),
    'Camera::set_cullingMask': ('medium', 'Camera.cullingMask'),
    'Camera::set_depth': ('medium', 'Camera.depth'),
    'Camera::set_orthographic': ('medium', 'Camera.orthographic'),
    'Time::set_fixedDeltaTime': ('medium', 'Time.fixedDeltaTime'),
}
CAMERA_CREATE = 'UnityEngine.Camera>'

PROMPT_PATTERNS = (
    (re.compile(r'\[<color=', re.I), 'Valheim key-prompt markup'),
    (re.compile(r'<sprite=', re.I), 'TMP gamepad glyph'),
    (re.compile(r'\$KEY_'), 'vanilla $KEY_ token'),
    (re.compile(r'\b(?:ctrl|shift|alt)\s*\+\s*\w', re.I), 'modifier combo'),
    (re.compile(r'\b(?:left|right|middle)[- ]click\b', re.I), 'mouse click prompt'),
    (re.compile(r'\b(?:mouse|scroll)\s*wheel\b', re.I), 'scroll wheel prompt'),
    (re.compile(r'\b(?:press|hold|tap)\s+(?:\[|<|the\s+)?[A-Za-z0-9]', re.I), 'imperative key prompt'),
)

HINTS = {
    'C1': 'add the canvas name to VHVR ADDITIONAL_GUI_CANVAS_NAMES / our adopt list, or reparent under GuiRoot/GUI',
    'C2': 'set the GameObject layer to "UI" so the VHVR GUI camera (cullingMask 1<<UI) renders it',
    'C3': 'map the ZInput action to a SteamVR action or a VR radial-menu entry before VHVR blacklists it',
    'C4': 'expose the action in the VR menu or bind it to a SteamVR action; a keyboard default is unreachable in VR',
    'C5': 'verify with the VHVR software cursor; VHVR patches mousePosition only, not buttons/wheel/drag',
    'C6': 'reword or localise the prompt so VR users are not told to press a physical key',
    'C7': 'pin the setting in config or patch it out; texture/vsync/framerate churn wrecks VR frame pacing',
}

# ----------------------------------------------------------------- findings


def finding(package, assembly, cid, severity, evidence, method, confidence, note=None):
    return {'package': package, 'assembly': assembly, 'class': cid, 'title': CLASS_TITLES[cid],
            'severity': severity, 'confidence': confidence, 'evidence': evidence,
            'method': method, 'hint': HINTS[cid], 'note': note}


# Calls that are part of building an argument, not a preceding statement. Walking back
# past these keeps section/key/default strings attached to the right bind.
ARG_CONSTRUCTORS = ('.ctor', 'op_Implicit', 'op_Explicit', 'ConfigDescription', 'AcceptableValue',
                    'ConfigurationManagerAttributes', 'Empty', 'Concat', 'Format', 'ToString',
                    'get_Localize', 'Localize')


def window_before(stream, index, back=64, floor=0):
    """Recover the literal arguments pushed for the call at `index`.

    Scans backwards and stops at the previous statement boundary - the first call that is
    not itself building an argument - so a bind never inherits the strings of the bind above it.
    `floor` is a hard lower bound, used to separate consecutive constructors of the same type.
    """
    strings, ints = [], []
    for position in range(index - 1, max(floor - 1, index - back - 1), -1):
        kind, value = stream[position]
        if kind == 'ldstr':
            strings.append(value)
        elif kind == 'ldc':
            ints.append(value)
        elif kind in ('call', 'callvirt', 'newobj'):
            if not (isinstance(value, str) and any(c in value for c in ARG_CONSTRUCTORS)):
                break
    strings.reverse()
    ints.reverse()
    return strings, ints


def collect_facts(asm):
    facts = {'canvas_names': set(), 'zinput': {}, 'zinput_read': {}, 'action_names': {},
             'shortcuts': [], 'key_reads': {}, 'mouse': {}, 'perf': {}, 'prompts': [],
             'canvas_methods': [], 'extra_camera': [], 'has_layer': False, 'jotunn_gui': False}
    shortcut_binders, call_sites, ctor_sites = set(), {}, {}

    for _rid, method, stream in asm.methods():
        canvas_here, last_ctor = False, 0
        for pos, (kind, value) in enumerate(stream):
            if kind == 'ldstr':
                for pattern, label in PROMPT_PATTERNS:
                    if pattern.search(value):
                        facts['prompts'].append((value, method, label))
                        break
                continue
            if kind == 'ldc' or not isinstance(value, str) or not value:
                continue
            text = value
            if kind in ('call', 'callvirt', 'newobj'):
                call_sites.setdefault(text, []).append((method, pos, stream))
            if CANVAS_SET_MODE in text:
                canvas_here = True
                strings, _ = window_before(stream, pos)
                facts['canvas_names'].update(s for s in strings if 0 < len(s) <= 48)
            if CANVAS_ADD in text and 'AddComponent' in text:
                canvas_here = True
            if any(sym in text for sym in JOTUNN_GUI_SYMBOLS):
                facts['jotunn_gui'] = True
            if any(sym in text for sym in LAYER_SYMBOLS):
                facts['has_layer'] = True
            if any(sym in text for sym in ZINPUT_ADD):
                strings, ints = window_before(stream, pos)
                button = strings[0] if strings and ACTION_NAME.match(strings[0]) else '<computed>'
                key = next((i for i in ints if i in KEYCODES and i >= 8), None)
                via = 'Jotunn InputManager' if 'InputManager::' in text else 'ZInput'
                facts['zinput'].setdefault(button, []).append((method, key, via))
            owner, _, member = text.partition('::')
            if member in ACTION_MEMBERS and any(hint in owner for hint in ACTION_OWNER_HINTS):
                strings, _ = window_before(stream, pos)
                for candidate in strings:
                    if ACTION_NAME.match(candidate):
                        facts['action_names'].setdefault(candidate, []).append((method, text))
            if any(sym in text for sym in ZINPUT_READ):
                strings, _ = window_before(stream, pos, back=4)
                if strings:
                    facts['zinput_read'].setdefault(strings[0], []).append(method)
            for symbol, label in KEY_READ.items():
                if text.endswith(symbol) or text == symbol:
                    _, ints = window_before(stream, pos, back=4)
                    key = next((i for i in ints if i in KEYCODES), None)
                    facts['key_reads'].setdefault(label, []).append((method, key))
            for symbol, label in MOUSE_READ.items():
                if text.endswith(symbol) or text == symbol:
                    facts['mouse'].setdefault(label, []).append(method)
            if 'UnityEngine.Input::GetAxis' in text:
                strings, _ = window_before(stream, pos, back=4)
                axis = next((s for s in strings if 'Mouse' in s), None)
                if axis:
                    facts['mouse'].setdefault(f'Input.GetAxis("{axis}")', []).append(method)
            for symbol, (severity, label) in PERF_SYMBOLS.items():
                if symbol in text:
                    facts['perf'].setdefault((label, severity), []).append(method)
            if CAMERA_CREATE in text and 'AddComponent' in text:
                facts['extra_camera'].append(method)
            if 'KeyboardShortcut' in text and '.ctor' in text:
                _, ints = window_before(stream, pos, floor=last_ctor)
                last_ctor = pos + 1
                keys = [i for i in ints if i in KEYCODES and i >= 8]
                if keys:
                    ctor_sites.setdefault(method, []).append((pos, keys))
            if 'KeyboardShortcut' in text and ('Bind' in text or '.ctor' in text):
                shortcut_binders.add(method)
        if canvas_here:
            parented = any(k in ('call', 'callvirt') and isinstance(v, str)
                           and any(p in v for p in PARENT_SYMBOLS) for k, v in stream)
            facts['canvas_methods'].append((method, parented))

    seen = set()
    for text, sites in call_sites.items():
        wrapper = text.split('<')[0]
        is_bind = 'KeyboardShortcut' in text and ('::Bind' in text or 'KeyboardShortcut::.ctor' in text)
        is_wrapper = wrapper in shortcut_binders or text in shortcut_binders
        if not (is_bind or is_wrapper):
            continue
        for method, pos, stream in sites:
            strings, ints = window_before(stream, pos)
            # Config sections and keys are short labels; error text and format strings are not.
            labels = [s for s in strings if 0 < len(s) <= 64 and '\n' not in s
                      and not s.endswith('.') and '{' not in s]
            # The default comes from the nearest preceding KeyboardShortcut ctor in this method,
            # which survives loop-generated binds where the section/key strings are built at runtime.
            keys = [i for i in ints if i in KEYCODES and i >= 8]
            nearby = [(p, k) for p, k in ctor_sites.get(method, ()) if p <= pos and pos - p <= 96]
            if nearby:
                keys = max(nearby, key=lambda item: item[0])[1]
            section = labels[0] if len(labels) > 1 else None
            config_key = labels[1] if len(labels) > 1 else (labels[0] if labels else None)
            record = (section, config_key, tuple(keys), method)
            if record in seen:
                continue
            seen.add(record)
            facts['shortcuts'].append({
                'section': section, 'key': config_key,
                'default': ' + '.join(keycode(k) for k in keys) if keys else None, 'method': method,
                'source': 'Bind<KeyboardShortcut>' if is_bind else f'wrapper {wrapper}'})

    facts['drag'] = sorted(i for i in DRAG_INTERFACES if any(i in ref for ref in asm.typerefs))
    return facts


def canvas_findings(package, name, facts, adopt_names):
    out = []
    known = sorted(n for n in facts['canvas_names'] if n in adopt_names)
    unknown = sorted(n for n in facts['canvas_names'] if n not in adopt_names)
    for method, parented in facts['canvas_methods']:
        if facts['jotunn_gui']:
            out.append(finding(package, name, 'C1', 'info',
                               f'{CANVAS_SET_MODE} plus Jotunn GUIManager custom-GUI symbols', method, 'medium',
                               'ALREADY HANDLED: Jotunn parents custom GUI under the GuiRoot PixelFix, which VHVR converts'))
        elif known and not unknown:
            out.append(finding(package, name, 'C1', 'info',
                               f'{CANVAS_SET_MODE} on known canvas {known}', method, 'medium',
                               'ALREADY HANDLED: canvas name is in VHVR ADDITIONAL_GUI_CANVAS_NAMES / our adopt list'))
        elif parented:
            out.append(finding(package, name, 'C1', 'medium',
                               f'{CANVAS_SET_MODE} with Transform.SetParent in the same method', method, 'medium',
                               'nesting target is a runtime value, so this is not statically decidable: it may be a '
                               f'correct nested canvas or a reparented root. Candidate names={unknown or ["<none>"]}'))
        else:
            out.append(finding(package, name, 'C1', 'high',
                               f'{CANVAS_SET_MODE} with no reparenting call in the same method', method, 'high',
                               f'likely a ROOT canvas; candidate names={unknown or ["<none recovered>"]}. VHVR scans '
                               'for canvases once at startup only (VRGUI.ensureGuiCanvas ~:747-779)'))
    if not facts['canvas_methods']:
        return out
    worst = max(SEVERITY_WEIGHT[f['severity']] for f in out)
    if facts['has_layer']:
        return out
    if worst >= SEVERITY_WEIGHT['medium']:
        out.append(finding(package, name, 'C2', 'high',
                           'no GameObject::set_layer / LayerMask::NameToLayer anywhere in the assembly',
                           '<assembly>', 'medium',
                           'VHVR onGuiCanvasFound (VRGUI.cs:952-955) sets worldCamera and renderMode but never the '
                           'layer, and the GUI camera cullingMask is 1<<"UI" (VRGUI.cs:992)'))
    else:
        out.append(finding(package, name, 'C2', 'medium',
                           'no GameObject::set_layer / LayerMask::NameToLayer anywhere in the assembly',
                           '<assembly>', 'low',
                           'the canvas path looks already handled, but nothing sets the UI layer explicitly'))
    return out


def zinput_findings(package, name, facts, cap, zinput_state):
    mapped, ignored = zinput_state['zinput_mapped'], zinput_state['zinput_ignored']
    out = []
    for button, sites in sorted(facts['zinput'].items())[:cap]:
        method, key, via = sites[0]
        default = f' default={keycode(key)}' if key is not None else ''
        if button in mapped:
            out.append(finding(package, name, 'C3', 'info', f'{via}.AddButton("{button}"){default}', method,
                               'medium', 'ALREADY HANDLED: VHVR binds this ZInput name to a SteamVR action '
                                         '(VRControls.cs ~:876-887)'))
            continue
        out.append(finding(package, name, 'C3', 'high', f'{via}.AddButton("{button}"){default}', method,
                           'high' if button != '<computed>' else 'low',
                           'VHVR logs "Unmapped ZInput Key:" once then adds the name to ignoredZInputs permanently '
                           '(VRControls.cs:521, initIgnoredZInputs :986-996), so the action is dead for the session'
                           + ('' if button != '<computed>' else
                              '. The name is built at runtime here; see the action-name literals reported below '
                              'for this assembly, and confirm against "Unmapped ZInput Key:" in the client log')))
    registered = set(facts['zinput'])
    for action, sites in sorted(facts['action_names'].items())[:cap]:
        if action in registered or action in mapped:
            continue
        method, symbol = sites[0]
        out.append(finding(package, name, 'C3', 'high', f'action name "{action}" passed to {symbol}', method,
                           'medium',
                           'recovered from the registration wrapper rather than from AddButton itself, so confirm '
                           'it reaches ZInput; if it does, VHVR will blacklist it on first use'))
    for button, methods in sorted(facts['zinput_read'].items())[:cap]:
        if button in mapped or button in registered:
            continue
        blacklisted = button in ignored
        out.append(finding(package, name, 'C3', 'high' if blacklisted else 'medium',
                           f'ZInput.GetButton*("{button}") x{len(methods)}', methods[0], 'high',
                           'VHVR blacklists this name in initIgnoredZInputs (:986-996), so the read never returns true'
                           if blacklisted else
                           'the mod consumes a ZInput action VHVR does not bind; it may be a vanilla name VHVR '
                           'simply never mapped, so confirm in-headset before spending a fix on it'))
    return out


def hotkey_findings(package, name, facts, cap):
    out = []
    for shortcut in facts['shortcuts'][:cap]:
        label = f'{shortcut["section"] or "<runtime>"}/{shortcut["key"] or "<runtime>"}'
        out.append(finding(package, name, 'C4', 'high',
                           f'KeyboardShortcut config "{label}" default={shortcut["default"] or "<not recovered>"} '
                           f'via {shortcut["source"]}', shortcut['method'],
                           'high' if shortcut['default'] else 'medium',
                           'this is the actionable string: wire it into a VR menu entry or a SteamVR action'))
    for label, sites in sorted(facts['key_reads'].items()):
        keys = sorted({keycode(k) for _m, k in sites if k is not None})
        out.append(finding(package, name, 'C4', 'medium' if facts['shortcuts'] else 'high',
                           f'{label} x{len(sites)}' + (f' keys={keys[:10]}' if keys else ''), sites[0][0], 'high',
                           'raw keyboard polling; VHVR patches Input.GetKeyDownInt/GetKeyInt only for the text-input '
                           'return key (TextInputPatches.cs:104,:111), so nothing else reaches VR'))
    return out


def mouse_findings(package, name, facts, cap):
    out = []
    for label, sites in sorted(facts['mouse'].items())[:cap]:
        handled = label == 'Input.mousePosition'
        out.append(finding(package, name, 'C5', 'info' if handled else 'medium',
                           f'{label} x{len(sites)}', sites[0], 'high',
                           'ALREADY HANDLED: VHVR patches Input.get_mousePosition (UIPatches.cs:114-123)' if handled
                           else 'VHVR does not patch this member; behaviour in VR is undefined'))
    if facts['drag']:
        out.append(finding(package, name, 'C5', 'medium', f'implements {", ".join(facts["drag"])}',
                           '<assembly>', 'medium',
                           'drag/scroll handlers need pointer deltas the VHVR software cursor does not emit'))
    return out


def prompt_findings(package, name, facts, cap):
    out, seen = [], set()
    for text, method, label in facts['prompts']:
        snippet = text.strip().replace('\n', ' ')[:80]
        if snippet in seen:
            continue
        seen.add(snippet)
        if len(out) < cap:
            out.append(finding(package, name, 'C6', 'low', f'{label}: "{snippet}"', method, 'low',
                               'string heuristic; confirm the string is actually shown to the player'))
    if len(seen) > cap:
        out.append(finding(package, name, 'C6', 'low', f'... and {len(seen) - cap} more key-prompt strings',
                           '<assembly>', 'low', 'per-assembly output cap reached; raise --cap to see them all'))
    return out


def perf_findings(package, name, facts, cap):
    out = []
    for (label, severity), sites in sorted(facts['perf'].items())[:cap]:
        out.append(finding(package, name, 'C7', severity, f'{label} x{len(sites)}', sites[0], 'high',
                           'mutating this at runtime changes VR frame pacing or stereo rendering'))
    if facts['extra_camera']:
        out.append(finding(package, name, 'C7', 'medium', f'AddComponent<Camera> x{len(facts["extra_camera"])}',
                           facts['extra_camera'][0], 'high',
                           'an extra camera doubles per-eye draw cost unless it is disabled in VR'))
    return out


def analyse_assembly(package, entry, asm, known, cap):
    facts = collect_facts(asm)
    name = asm.name
    out = (canvas_findings(package, name, facts, known['canvas'])
           + zinput_findings(package, name, facts, cap, known)
           + hotkey_findings(package, name, facts, cap)
           + mouse_findings(package, name, facts, cap)
           + prompt_findings(package, name, facts, cap)
           + perf_findings(package, name, facts, cap))
    for item in out:
        item['entry'] = entry
    return out


# ------------------------------------------------------------------ scanning

def parse_vhvr_names(path):
    """Re-parse ADDITIONAL_GUI_CANVAS_NAMES out of VRGUI.cs."""
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    match = re.search(r'ADDITIONAL_GUI_CANVAS_NAMES\s*=\s*new\s+string\[\]\s*\{(.*?)\}', text, re.S)
    return tuple(re.findall(r'"([^"]*)"', match.group(1))) if match else None


def parse_vhvr_zinputs(path):
    """Re-parse the bound and blacklisted ZInput names out of VRControls.cs."""
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None, None
    mapped = set(re.findall(r'zInputToBooleanAction\.Add\("([^"]+)"', text))
    # A few entries are added through a constant; resolve trivial string-literal properties.
    for symbol in re.findall(r'zInputToBooleanAction\.Add\(([A-Za-z_]\w*)\s*,', text):
        literal = re.search(rf'{symbol}\s*{{\s*get\s*{{\s*return\s*"([^"]+)"', text)
        mapped.add(literal.group(1) if literal else symbol)
    block = re.search(r'initIgnoredZInputs\s*\(\s*\)\s*\{(.*?)\n        \}', text, re.S)
    ignored = set(re.findall(r'ignoredZInputs\.Add\("([^"]+)"', block.group(1) if block else ''))
    return (mapped or None), (ignored or None)


def load_adopt_list(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    if text.lstrip()[:1] in '[{':
        data = json.loads(text)
        return tuple(data if isinstance(data, list) else data.get('canvas_names', []))
    return tuple(line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#'))


def interesting_dll(entry):
    lowered = entry.lower()
    if not lowered.endswith('.dll') or any(part in lowered for part in SKIP_PATH_PARTS):
        return False
    stem = lowered.rsplit('/', 1)[-1][:-4]
    return stem not in DEPENDENCY_STEMS and not stem.startswith(DEPENDENCY_PREFIXES)


def manifest_match(entries, stem):
    """A cache directory holds every downloaded version; keep only the pinned one."""
    name, version = split_package(stem)
    entry = entries.get(name.lower())
    if entry is None:
        return False
    return entry['version'] in (None, '', version)


def scan_package(path, known, entries, cap):
    package, version = split_package(path.stem)
    result = {'package': path.stem, 'name': package, 'version': version,
              'scope': (entries.get(package.lower()) or {}).get('scope') if entries is not None else None,
              'assemblies': [], 'plugins': [], 'skipped': [], 'findings': [],
              'handled_by': OUR_HANDLED_PACKAGES.get(normalise(package))}
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        result['skipped'].append({'entry': path.name, 'reason': f'unreadable zip: {error}'})
        return result
    with archive:
        for info in archive.infolist():
            if info.is_dir() or not interesting_dll(info.filename):
                continue
            # Mods with embedded asset bundles run to ~100 MB; skipping them would be a silent
            # false negative, so the cap only guards against a pathological archive.
            if info.file_size > 512 * 1024 * 1024:
                result['skipped'].append({'entry': info.filename,
                                          'reason': f'oversized ({info.file_size // (1024 * 1024)} MB)'})
                continue
            short = info.filename.rsplit('/', 1)[-1]
            try:
                asm = Assembly(short, archive.read(info))
                result['plugins'] += asm.plugins()
                found = analyse_assembly(path.stem, info.filename, asm, known, cap)
            except Exception as error:  # unmanaged dll, packer, truncated stream
                result['skipped'].append({'entry': info.filename,
                                          'reason': f'{type(error).__name__}: {error}'[:160]})
                continue
            result['assemblies'].append(info.filename)
            result['findings'] += found
    return result


def rank(results, min_weight):
    ranked = []
    for result in results:
        kept = [f for f in result['findings'] if SEVERITY_WEIGHT[f['severity']] >= min_weight]
        if not kept:
            continue
        counts = {}
        for item in kept:
            counts[item['class']] = counts.get(item['class'], 0) + 1
        ranked.append({'package': result['package'], 'name': result['name'],
                       'scope': result['scope'], 'plugins': result['plugins'],
                       'score': sum(SEVERITY_WEIGHT[f['severity']] for f in kept),
                       'severity': max(kept, key=lambda f: SEVERITY_WEIGHT[f['severity']])['severity'],
                       'classes': counts, 'findings': kept, 'handled_by': result['handled_by']})
    ranked.sort(key=lambda r: (-r['score'], r['package']))
    return ranked


def report(ranked, results, adopt_names, elapsed, detail, stream=sys.stdout):
    write = lambda text='': print(text, file=stream)
    total = sum(len(r['findings']) for r in ranked)
    by_severity = dict.fromkeys(SEVERITIES, 0)
    by_class = {}
    for result in ranked:
        for item in result['findings']:
            by_severity[item['severity']] += 1
            by_class[item['class']] = by_class.get(item['class'], 0) + 1
    handled = [f for r in results for f in r['findings'] if f['severity'] == 'info']
    write(f'stage={STAGE} packages={len(results)} '
          f'assemblies={sum(len(r["assemblies"]) for r in results)} '
          f'skipped_dlls={sum(len(r["skipped"]) for r in results)} '
          f'flagged_packages={len(ranked)} findings={total} elapsed={elapsed:.1f}s '
          f'adopt_names={len(adopt_names)}')
    write('severity ' + ' '.join(f'{s}={by_severity[s]}' for s in reversed(SEVERITIES)))
    write('classes  ' + ' '.join(f'{c}={by_class[c]}' for c in sorted(by_class)))
    write(f'already_handled={len(handled)} findings downgraded to info because VHVR or one of our '
          f'fixes covers them (raise --min-severity info to list them)')
    for item in handled if not by_severity['info'] else ():
        write(f'  {item["class"]}/info  {item["package"]:<40} {item["evidence"]}')

    write('\n== ranked packages ==')
    for index, result in enumerate(ranked, start=1):
        classes = ' '.join(f'{c}x{n}' for c, n in sorted(result['classes'].items()))
        handled = f'  handled_by={result["handled_by"]}' if result['handled_by'] else ''
        write(f'{index:>3} {result["score"]:>4} {result["severity"]:<6} {result["package"]:<46} '
              f'{result["scope"] or "not-in-manifest":<15} {classes}{handled}')
    if not detail:
        return
    write('\n== findings ==')
    for result in ranked:
        write(f'\n[{result["package"]}] scope={result["scope"] or "not-in-manifest"} score={result["score"]}')
        for item in sorted(result['findings'], key=lambda f: (-SEVERITY_WEIGHT[f['severity']], f['class'])):
            write(f'  {item["class"]}/{item["severity"]:<6} conf={item["confidence"]:<6} {item["assembly"]}')
            write(f'      evidence: {item["evidence"]}')
            write(f'      method:   {item["method"]}')
            if item['note']:
                write(f'      note:     {item["note"]}')
            write(f'      hint:     {item["hint"]}')


def actionable(ranked):
    """The three lists a human needs next, in order of how directly they feed a VR fix."""
    registrations, dead_reads, shortcuts = [], [], []
    for result in ranked:
        for item in result['findings']:
            row = {'package': result['package'], 'evidence': item['evidence'],
                   'method': item['method'], 'confidence': item['confidence'],
                   'severity': item['severity']}
            if item['class'] == 'C3':
                (dead_reads if item['evidence'].startswith('ZInput.GetButton*')
                 else registrations).append(row)
            elif item['class'] == 'C4' and item['evidence'].startswith('KeyboardShortcut config'):
                shortcuts.append(row)
    return registrations, dead_reads, shortcuts


def main():
    parser = argparse.ArgumentParser(
        description='Stage 1 of VR mod onboarding: static VR-impact scan of Valheim mod packages.',
        epilog='exit 0 = clean at --min-severity, 1 = findings at or above it, 2 = tool error')
    parser.add_argument('--packages', type=Path, required=True, help='directory of package .zip files')
    parser.add_argument('--manifest', type=Path, help='profile-manifest.json; restricts the scan to the profile')
    parser.add_argument('--json', type=Path, help='write the machine-readable stage-1 result here')
    parser.add_argument('--min-severity', choices=SEVERITIES, default='low',
                        help='filter findings and set the gate threshold (default: low)')
    parser.add_argument('--package', action='append', default=[],
                        help='only packages whose filename contains this (repeatable)')
    parser.add_argument('--vhvr-source', type=Path, default=VHVR_SOURCE,
                        help='VRGUI.cs to re-parse ADDITIONAL_GUI_CANVAS_NAMES from')
    parser.add_argument('--vhvr-controls', type=Path, default=VHVR_CONTROLS,
                        help='VRControls.cs to re-parse the bound/blacklisted ZInput names from')
    parser.add_argument('--adopt-list', type=Path, help='extra already-adopted canvas names (text or JSON list)')
    parser.add_argument('--cap', type=int, default=8, help='max findings per class per assembly (default: 8)')
    parser.add_argument('--quiet', action='store_true', help='summary and ranking only, no per-finding detail')
    args = parser.parse_args()

    if not args.packages.is_dir():
        fail(f'not a directory: {args.packages}')

    parsed = parse_vhvr_names(args.vhvr_source)
    adopt_names = set(parsed or VHVR_CANVAS_NAMES) | set(VHVR_CANVAS_NAMES_IMPLICIT) | set(OUR_ADOPT_LIST)
    if args.adopt_list:
        adopt_names |= set(load_adopt_list(args.adopt_list))
    mapped, ignored = parse_vhvr_zinputs(args.vhvr_controls)
    known = {'canvas': adopt_names,
             'zinput_mapped': set(mapped or VHVR_ZINPUT_MAPPED),
             'zinput_ignored': set(ignored or VHVR_ZINPUT_IGNORED)}
    for source, value in ((args.vhvr_source, parsed), (args.vhvr_controls, mapped)):
        if value is None:
            print(f'warning: could not parse {source}; using the baked-in list', file=sys.stderr)

    entries = manifest_entries(args.manifest) if args.manifest else None
    archives = sorted(p for p in args.packages.glob('*.zip') if p.is_file())
    if entries is not None:
        archives = [p for p in archives if manifest_match(entries, p.stem)]
    if args.package:
        needles = [n.lower() for n in args.package]
        archives = [p for p in archives if any(n in p.stem.lower() for n in needles)]
    if not archives:
        fail('no packages matched')

    started = time.monotonic()
    results = [scan_package(path, known, entries, args.cap) for path in archives]
    elapsed = time.monotonic() - started

    ranked = rank(results, SEVERITY_WEIGHT[args.min_severity])
    report(ranked, results, adopt_names, elapsed, not args.quiet)

    registrations, dead_reads, shortcuts = actionable(ranked)
    print(f'\n== recovered ZInput button registrations ({len(registrations)}) ==')
    for item in registrations:
        print(f'  {item["package"]:<44} {item["evidence"]}')
    print(f'\n== ZInput actions read but never bound in VR ({len(dead_reads)}) ==')
    for item in dead_reads:
        print(f'  {item["severity"]:<6} {item["package"]:<44} {item["evidence"]}')
    print(f'\n== recovered KeyboardShortcut config defaults ({len(shortcuts)}) ==')
    for item in shortcuts:
        print(f'  {item["package"]:<44} {item["evidence"]}')

    skipped = [{**entry, 'package': result['package']} for result in results for entry in result['skipped']]
    if skipped:
        print(f'\n== skipped assemblies ({len(skipped)}) ==')
        for item in skipped:
            print(f'  {item["package"]:<44} {item["entry"]}: {item["reason"]}')

    if args.json:
        write_json(args.json, {
            'schema': 1, 'stage': STAGE,
            'packages_dir': str(args.packages),
            'manifest': str(args.manifest) if args.manifest else None,
            'min_severity': args.min_severity,
            'elapsed_seconds': round(elapsed, 3),
            'vhvr_state': {'canvas_names': sorted(known['canvas']),
                           'zinput_mapped': sorted(known['zinput_mapped']),
                           'zinput_ignored': sorted(known['zinput_ignored']),
                           'source': str(args.vhvr_source), 'controls': str(args.vhvr_controls)},
            'class_titles': CLASS_TITLES, 'class_recall': CLASS_RECALL,
            'counts': {'packages': len(results),
                       'assemblies': sum(len(r['assemblies']) for r in results),
                       'flagged_packages': len(ranked),
                       'findings': sum(len(r['findings']) for r in ranked)},
            'ranked': ranked,
            'plugins': [p for r in results for p in r['plugins']],
            'zinput_registrations': registrations, 'zinput_dead_reads': dead_reads,
            'keyboard_shortcuts': shortcuts, 'skipped': skipped,
        })
        print(f'\njson={args.json}')
    return EXIT_FLAGGED if ranked else EXIT_CLEAN


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, json.JSONDecodeError) as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(EXIT_ERROR)
