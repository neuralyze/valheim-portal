#!/usr/bin/env python3
"""Apply a tiered Valheim jumpstart preset to a running 1.0.12 server.

The presets in ``tools/jumpstart/presets/*.yaml`` describe a whole progression
state: boss keys, gear, materials, skills and station levels.  Only part of
that is reachable from outside the game, so this driver splits every preset
into three buckets and is loud about which is which:

  AUTOMATED   global keys and item grants, over RCON.
  LAUNCH      world modifiers, which are process arguments (``-modifier``) and
              therefore belong in ``valheim.env``; printed, never written.
  MANUAL      skills and station levels.  There is no working server-side skill
              backend on 1.0.12 (see ``ServerCharactersBackend``), and station
              level comes from extension pieces an admin has to place.

Nothing mutates the server unless ``--commit`` is passed.  ``--dry-run`` prints
the exact command sequence.

Usage::

    jumpstart.py list
    jumpstart.py show pre-bonemass
    jumpstart.py verify
    jumpstart.py apply pre-bonemass --players 76561197987967077 --dry-run
    jumpstart.py apply pre-bonemass --players 7656...,7656... --commit
    jumpstart.py itemset deepnorth-sandbox
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import yaml

HERE = Path(__file__).resolve().parent
PRESET_DIR = HERE / "presets"
INDEX_PATH = HERE / "data" / "prefab_index.json"

DEFAULT_VALHEIM_ROOT = os.environ.get("VALHEIM_ROOT", "/media/big4/projects/game/valheim")
DEFAULT_WORLD = os.environ.get("VALHEIM_WORLD", "Ulfsland")

MODIFIER_KEYS = ("Combat", "DeathPenalty", "Resources", "Raids", "Portals")
# assembly_valheim.dll :: WorldModifierOption -- the complete option vocabulary.
MODIFIER_VALUES = (
    "Default", "None", "Less", "MuchLess", "More", "MuchMore",
    "Casual", "VeryEasy", "Easy", "Hard", "VeryHard", "Hardcore", "Most",
)


# ==========================================================================
# preset data
# ==========================================================================
@dataclass(frozen=True)
class Grant:
    prefab: str
    count: int
    quality: int = 1
    bucket: str = "kit"


@dataclass
class Preset:
    name: str
    path: Path
    raw: dict

    @property
    def tier(self) -> int:
        return int(self.raw.get("tier", 0))

    @property
    def summary(self) -> str:
        return str(self.raw.get("summary", ""))

    @property
    def global_keys(self) -> list[str]:
        return list(self.raw.get("global_keys") or [])

    @property
    def world_modifiers(self) -> dict[str, str]:
        return dict(self.raw.get("world_modifiers") or {})

    @property
    def skills(self) -> dict[str, int]:
        return {str(k): int(v) for k, v in (self.raw.get("skills") or {}).items()}

    @property
    def stations(self) -> list[dict]:
        return list(self.raw.get("stations") or [])

    def grants(self) -> list[Grant]:
        out: list[Grant] = []
        for bucket in ("kit", "materials", "chain_items"):
            for row in self.raw.get(bucket) or []:
                out.append(
                    Grant(
                        prefab=str(row["prefab"]),
                        count=int(row.get("count", 1)),
                        quality=int(row.get("quality", 1)),
                        bucket=bucket,
                    )
                )
        return out

    def prefab_refs(self) -> list[tuple[str, str]]:
        """Every prefab this preset names, tagged ``item`` or ``piece``.

        The two kinds are verified against different evidence: items must show
        up in an item channel (ObjectDB / EpicLoot / recipe / localization),
        build pieces only exist in the game's asset bundles.
        """
        refs: list[tuple[str, str]] = [(g.prefab, "item") for g in self.grants()]
        for station in self.stations:
            refs.append((str(station["prefab"]), "piece"))
            refs.extend((str(e), "piece") for e in station.get("extensions") or [])
        return refs

    def modifier_args(self) -> str:
        parts = []
        for key in MODIFIER_KEYS:
            value = self.world_modifiers.get(key)
            if value:
                parts.append(f"-modifier {key} {value}")
        return " ".join(parts)


def load_preset(name: str) -> Preset:
    path = PRESET_DIR / f"{name}.yaml"
    if not path.exists():
        raise SystemExit(f"no such preset: {name!r} (looked for {path})")
    raw = yaml.safe_load(path.read_text("utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit(f"{path}: preset must be a YAML mapping")
    if raw.get("name") != name:
        raise SystemExit(f"{path}: 'name' is {raw.get('name')!r}, expected {name!r}")
    return Preset(name=name, path=path, raw=raw)


def all_preset_names() -> list[str]:
    presets = []
    for path in PRESET_DIR.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text("utf-8")) or {}
        presets.append((int(raw.get("tier", 0)), path.stem))
    return [name for _, name in sorted(presets)]


# ==========================================================================
# prefab verification
# ==========================================================================
@dataclass
class PrefabIndex:
    objectdb: set[str]
    epicloot: set[str]
    recipes: set[str]
    localization: set[str]
    bundle: set[str]
    meta: dict

    STRONG = ("objectdb", "epicloot", "recipe", "localization")

    @classmethod
    def load(cls, path: Path) -> "PrefabIndex":
        if not path.exists():
            raise SystemExit(
                f"missing prefab index {path}\n"
                "regenerate it on the Valheim host with:\n"
                "  tools/jumpstart/extract_prefab_names.py --out tools/jumpstart/data/prefab_index.json"
            )
        data = json.loads(path.read_text("utf-8"))
        norm = lambda s: s.replace("_", "").lower()  # noqa: E731
        return cls(
            objectdb=set(data.get("objectdb_items") or []),
            epicloot=set(data.get("epicloot_names") or []),
            recipes=set(data.get("recipe_targets") or []),
            localization={norm(k) for k in data.get("localization_keys") or []},
            bundle=set(data.get("bundle_tokens") or []),
            meta={k: v for k, v in data.items() if not isinstance(v, list)},
        )

    def evidence(self, prefab: str, kind: str = "item") -> list[str]:
        found = []
        if kind == "piece":
            # Build pieces are not items: they never appear in ObjectDB,
            # EpicLoot or the item recipe list.  The two independent signals
            # available are the prefab token itself and its localization
            # description key, both recovered from the shipped asset bundles.
            if prefab in self.bundle:
                found.append("bundle")
            base = prefab if prefab.startswith("piece_") else "piece_" + prefab
            if f"{base}_description" in self.bundle:
                found.append("piece-desc")
            return found
        if prefab in self.objectdb:
            found.append("objectdb")
        if prefab in self.epicloot:
            found.append("epicloot")
        if prefab in self.recipes:
            found.append("recipe")
        if ("item" + prefab).replace("_", "").lower() in self.localization:
            found.append("localization")
        if prefab in self.bundle:
            found.append("bundle")
        return found

    def classify(self, prefab: str, kind: str = "item") -> str:
        evidence = self.evidence(prefab, kind)
        if kind == "piece":
            return "verified" if "bundle" in evidence else "unknown"
        if any(channel in evidence for channel in self.STRONG):
            return "verified"
        if evidence:
            return "weak"
        return "unknown"


# ==========================================================================
# RCON
# ==========================================================================
class RconError(RuntimeError):
    pass


@dataclass
class RconCredentials:
    host: str
    port: int
    password: str
    source: Path

    def __str__(self) -> str:  # never leak the password into logs
        return f"{self.host}:{self.port} (password from {self.source}, {len(self.password)} chars)"


def read_rcon_credentials(config_path: Path, host: str, port: int | None) -> RconCredentials:
    """Parse Port/Password out of the ``[1. Rcon]`` section of the deployed config.

    The file has two distinct ``Webhook url`` keys in different sections, so the
    parse has to be section-aware rather than a flat grep.
    """
    if not config_path.exists():
        raise RconError(f"rcon config not found: {config_path}")
    section = None
    found: dict[str, str] = {}
    for line in config_path.read_text("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if not line or line.startswith("#") or "=" not in line:
            continue
        if section != "1. Rcon":
            continue
        key, _, value = line.partition("=")
        found[key.strip()] = value.strip()
    password = found.get("Password", "")
    if not password:
        raise RconError(f"{config_path}: [1. Rcon] Password is empty; the rcon plugin is inert")
    cfg_port = port or int(found.get("Port") or 2458)
    return RconCredentials(host=host, port=cfg_port, password=password, source=config_path)


class RconClient:
    """Minimal Source RCON client.

    Valheim Rcon 1.6.2 speaks the Source RCON packet format (4-byte little
    endian length, id, type, two NUL-terminated strings).  This implementation
    has NOT been exercised against the live Ulfsland server by the author of
    this file; a sibling task owns the server.  Every failure raises.
    """

    AUTH = 3
    AUTH_RESPONSE = 2
    EXEC = 2
    RESPONSE_VALUE = 0

    def __init__(self, creds: RconCredentials, timeout: float = 10.0):
        self.creds = creds
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._id = 0

    def __enter__(self) -> "RconClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        self._sock = socket.create_connection((self.creds.host, self.creds.port), self.timeout)
        self._sock.settimeout(self.timeout)
        self._send(self.AUTH, self.creds.password)
        packet_id, packet_type, body = self._recv()
        if packet_type == self.RESPONSE_VALUE:  # some servers send an empty value first
            packet_id, packet_type, body = self._recv()
        if packet_id == -1:
            raise RconError("rcon authentication rejected (bad password)")
        if packet_type != self.AUTH_RESPONSE:
            raise RconError(f"unexpected rcon auth response type {packet_type}: {body!r}")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def command(self, line: str) -> str:
        if self._sock is None:
            raise RconError("rcon socket is not connected")
        self._send(self.EXEC, line)
        chunks: list[str] = []
        while True:
            try:
                _, _, body = self._recv()
            except socket.timeout:
                break
            chunks.append(body)
            # A single response is the norm; drain quickly in case it fragments.
            self._sock.settimeout(0.4)
        self._sock.settimeout(self.timeout)
        return "".join(chunks)

    # -- framing -----------------------------------------------------------
    def _send(self, packet_type: int, body: str) -> None:
        assert self._sock is not None
        self._id += 1
        payload = struct.pack("<ii", self._id, packet_type) + body.encode("utf-8") + b"\x00\x00"
        self._sock.sendall(struct.pack("<i", len(payload)) + payload)

    def _recv(self) -> tuple[int, int, str]:
        assert self._sock is not None
        raw_len = self._read_exactly(4)
        (length,) = struct.unpack("<i", raw_len)
        if length < 10 or length > 4_194_304:
            raise RconError(f"implausible rcon packet length {length}")
        payload = self._read_exactly(length)
        packet_id, packet_type = struct.unpack("<ii", payload[:8])
        body = payload[8:].split(b"\x00", 1)[0].decode("utf-8", "replace")
        return packet_id, packet_type, body

    def _read_exactly(self, count: int) -> bytes:
        assert self._sock is not None
        buf = b""
        while len(buf) < count:
            chunk = self._sock.recv(count - len(buf))
            if not chunk:
                raise RconError("rcon connection closed mid-packet")
            buf += chunk
        return buf


KEY_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,63}")
NOISE_TOKENS = {"globalKeys", "GlobalKeys", "Global", "Keys", "none", "None", "empty", "Empty", "keys"}


def parse_global_keys(response: str) -> set[str]:
    """Pull key names out of a ``globalKeys`` response.

    The exact framing of the reply is not pinned down here, so the parse is
    deliberately tolerant: every identifier-shaped token minus obvious prose.
    Callers treat a *missing* expected key as a hard failure, so a parser that
    over-collects is safe while one that under-collects is not.
    """
    return {t for t in KEY_TOKEN_RE.findall(response or "") if t not in NOISE_TOKENS}


# ==========================================================================
# skill backends
# ==========================================================================
@dataclass
class SkillPlan:
    backend: str
    automated: bool
    reason: str
    instructions: list[str] = field(default_factory=list)


class SkillBackend:
    """A way to set a player's skills.

    Implementations are looked up by name.  ``available()`` returns
    ``(usable, reason)``; an unusable backend must explain itself precisely
    enough that the operator can decide whether to fix it.
    """

    name = "abstract"

    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def plan(self, preset: Preset, players: Sequence[str]) -> SkillPlan:
        raise NotImplementedError

    def apply(self, preset: Preset, players: Sequence[str]) -> None:
        raise NotImplementedError


class ServerCharactersBackend(SkillBackend):
    """Smoothbrain-ServerCharacters -- present but NOT loadable on 1.0.12.

    ServerCharacters calls ``PlayerProfile.GetCharacterFolderPath(FileHelpers.
    FileSource)`` from ``Initialize()``, which Harmony runs at
    ``FejdStartup.Awake``.  Valheim 1.0 moved that member to ``SaveSystem``, so
    the plugin throws before it registers anything and its loopback
    ``WebInterfaceAPI`` on 127.0.0.1:5982 (``RaiseSkill``/``ResetSkill``/
    ``GiveItem``/``GetPlayerList``/``SendIngameMessage``) never starts.

    Retargeting the single MemberRef repairs all three call sites, but that is
    an operator decision, not this driver's.  When it is made, this class only
    needs ``available()`` to probe the port and ``apply()`` to POST RaiseSkill.
    """

    name = "servercharacters"
    endpoint = ("127.0.0.1", 5982)

    def available(self) -> tuple[bool, str]:
        reason = (
            "Smoothbrain-ServerCharacters 1.4.16 does not load on Valheim 1.0.12: it resolves "
            "PlayerProfile.GetCharacterFolderPath(FileHelpers.FileSource), which 1.0 moved to "
            "SaveSystem, during FejdStartup.Awake. Its WebInterfaceAPI on "
            f"{self.endpoint[0]}:{self.endpoint[1]} therefore never binds."
        )
        with socket.socket() as sock:
            sock.settimeout(0.5)
            try:
                sock.connect(self.endpoint)
            except OSError:
                return False, reason
        return False, (
            f"something is listening on {self.endpoint[0]}:{self.endpoint[1]}, but this backend "
            "is still disabled: the ServerCharacters MemberRef retarget has not been signed off, "
            "so the API contract is unverified. Enable it deliberately, do not assume it."
        )

    def plan(self, preset: Preset, players: Sequence[str]) -> SkillPlan:
        usable, reason = self.available()
        return SkillPlan(backend=self.name, automated=usable, reason=reason)

    def apply(self, preset: Preset, players: Sequence[str]) -> None:
        usable, reason = self.available()
        raise RuntimeError(f"skill backend {self.name!r} is unavailable: {reason}")


class ManualSkillBackend(SkillBackend):
    """Emit the exact hand steps, including AdminQoL itemset YAML."""

    name = "manual"

    def available(self) -> tuple[bool, str]:
        return True, "produces instructions only; sets nothing"

    def plan(self, preset: Preset, players: Sequence[str]) -> SkillPlan:
        yaml_path = "BepInEx/config/AdminQoL/AdminQoL.ItemSets.yml"
        steps = [
            "Skills cannot be set remotely on this server. Do it per character, from the "
            "ulfsland-admin client edition (never from ulfsland-vr / ulfsland-flat):",
            f"1. Append the generated item set to the admin client's {yaml_path}. Get it with:",
            f"     tools/jumpstart/jumpstart.py itemset {preset.name}",
            "2. In game, run  adminqol_itemsets_reload  then  adminqol_itemsets_list  and confirm "
            f"'jumpstart-{preset.name}' is listed.",
            f"3. Run  itemset jumpstart-{preset.name}  once per character. AdminQoL's itemset "
            "applies to the INVOKING admin's local character only, so each player must run it "
            "themselves while their own character is loaded, or an admin must load each character.",
            "4. Re-check levels afterwards: dev.crystal.deathpenalty.cfg sets SkillLossPercent = 5 "
            "with ResetLevelProgress = true, so every death shaves 5% off each skill and wipes "
            "in-level progress. Expect to re-apply.",
            "Players needing this step: " + (", ".join(players) if players else "(none given)"),
        ]
        return SkillPlan(
            backend=self.name,
            automated=False,
            reason="no working central skill backend on 1.0.12; see ServerCharactersBackend",
            instructions=steps,
        )

    def apply(self, preset: Preset, players: Sequence[str]) -> None:
        raise RuntimeError("the manual skill backend never applies anything by design")


SKILL_BACKENDS: dict[str, Callable[[], SkillBackend]] = {
    ServerCharactersBackend.name: ServerCharactersBackend,
    ManualSkillBackend.name: ManualSkillBackend,
}


def pick_skill_backend(choice: str) -> SkillBackend:
    if choice != "auto":
        if choice not in SKILL_BACKENDS:
            raise SystemExit(f"unknown skill backend {choice!r}; have {sorted(SKILL_BACKENDS)}")
        return SKILL_BACKENDS[choice]()
    for name in (ServerCharactersBackend.name, ManualSkillBackend.name):
        backend = SKILL_BACKENDS[name]()
        usable, _ = backend.available()
        if usable:
            return backend
    return ManualSkillBackend()


# ==========================================================================
# grant ledger (idempotency for `give`, which is additive)
# ==========================================================================
class Ledger:
    """Records what has already been granted, per world/preset/player/prefab.

    ``give`` stacks, so replaying a preset without a ledger would double every
    item.  The ledger turns a re-run into a delta: only the shortfall is
    granted, and a fully-applied preset is a no-op.
    """

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {"version": 1, "granted": {}, "keys": {}}
        if path.exists():
            loaded = json.loads(path.read_text("utf-8"))
            if loaded.get("version") != 1:
                raise SystemExit(f"{path}: unsupported ledger version {loaded.get('version')}")
            self.data = loaded

    @staticmethod
    def _slot(world: str, preset: str, player: str) -> str:
        return f"{world}/{preset}/{player}"

    def granted(self, world: str, preset: str, player: str, prefab: str, quality: int) -> int:
        slot = self.data["granted"].get(self._slot(world, preset, player), {})
        return int(slot.get(f"{prefab}@{quality}", 0))

    def record(self, world: str, preset: str, player: str, prefab: str, quality: int, count: int) -> None:
        slot = self.data["granted"].setdefault(self._slot(world, preset, player), {})
        key = f"{prefab}@{quality}"
        slot[key] = int(slot.get(key, 0)) + count

    def record_key(self, world: str, key: str) -> None:
        self.data["keys"].setdefault(world, [])
        if key not in self.data["keys"][world]:
            self.data["keys"][world].append(key)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n")


def default_ledger_path(world: str) -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "valheim-jumpstart" / f"{world}.json"


# ==========================================================================
# apply
# ==========================================================================
@dataclass
class Step:
    phase: str          # keys | items
    command: str
    detail: str = ""


@dataclass
class Report:
    preset: str
    world: str
    players: list[str]
    dry_run: bool
    planned: list[Step] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    launch: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.failures


def plan_keys(preset: Preset, existing: set[str] | None) -> tuple[list[Step], list[str]]:
    steps: list[Step] = []
    skipped: list[str] = []
    for key in preset.global_keys:
        if existing is not None and key in existing:
            skipped.append(f"globalKey {key}: already set")
            continue
        steps.append(
            Step(
                phase="keys",
                command=f"addGlobalKey {key}",
                detail="then re-read globalKeys and confirm the key is present before continuing",
            )
        )
    return steps, skipped


def plan_items(
    preset: Preset,
    players: Sequence[str],
    ledger: Ledger,
    world: str,
) -> tuple[list[Step], list[str]]:
    steps: list[Step] = []
    skipped: list[str] = []
    for player in players:
        for grant in preset.grants():
            already = ledger.granted(world, preset.name, player, grant.prefab, grant.quality)
            remaining = grant.count - already
            if remaining <= 0:
                skipped.append(
                    f"give {player} {grant.prefab} q{grant.quality}: ledger already has "
                    f"{already}/{grant.count}"
                )
                continue
            steps.append(
                Step(
                    phase="items",
                    command=f"give {player} {grant.prefab} -count {remaining} -quality {grant.quality}",
                    detail=f"{grant.bucket}; ledger {already}/{grant.count}",
                )
            )
    return steps, skipped


def run_apply(args: argparse.Namespace) -> int:
    preset = load_preset(args.preset)
    players = [p.strip() for p in ",".join(args.players or []).split(",") if p.strip()]
    if not players and not args.keys_only:
        raise SystemExit("--players is required unless --keys-only is given")
    for player in players:
        if not re.fullmatch(r"7656[0-9]{13}", player):
            raise SystemExit(f"{player!r} does not look like a SteamID64 (expected 7656 + 13 digits)")

    index = PrefabIndex.load(Path(args.index))
    unknown = [p for p, kind in preset.prefab_refs() if index.classify(p, kind) == "unknown"]
    if unknown:
        raise SystemExit(
            "refusing to apply: these prefabs have no evidence in the index: " + ", ".join(sorted(set(unknown)))
        )

    world = args.world
    ledger_path = Path(args.ledger) if args.ledger else default_ledger_path(world)
    ledger = Ledger(ledger_path)

    report = Report(preset=preset.name, world=world, players=players, dry_run=not args.commit)
    report.launch = [
        f"SERVER_ARGS='-preset Normal {preset.modifier_args()}'",
        "world modifiers are launch arguments; edit valheim.env and restart. This driver never writes it.",
    ]

    creds = None
    existing_keys: set[str] | None = None
    client: RconClient | None = None
    config_path = Path(args.rcon_config) if args.rcon_config else (
        Path(args.valheim_root) / world / "config_merged" / "bepinex" / "org.tristan.rcon.cfg"
    )
    try:
        creds = read_rcon_credentials(config_path, args.rcon_host, args.rcon_port)
    except RconError as exc:
        if args.commit:
            report.failures.append(f"rcon credentials: {exc}")
        else:
            report.skipped.append(f"rcon credentials unavailable in dry-run: {exc}")

    if args.commit and creds is not None:
        client = RconClient(creds, timeout=args.timeout)
        try:
            client.connect()
            existing_keys = parse_global_keys(client.command("globalKeys"))
        except (RconError, OSError) as exc:
            report.failures.append(f"rcon connect/globalKeys: {exc}")
            client = None

    key_steps, key_skips = plan_keys(preset, existing_keys)
    item_steps, item_skips = ([], []) if args.keys_only else plan_items(preset, players, ledger, world)
    if args.items_only:
        key_steps, key_skips = [], ["global keys skipped: --items-only"]
    report.planned = key_steps + item_steps
    report.skipped.extend(key_skips + item_skips)

    backend = pick_skill_backend(args.skill_backend)
    skill_plan = backend.plan(preset, players)
    if skill_plan.automated:
        report.manual.append(f"skills: backend {skill_plan.backend} reports itself usable -- verify before trusting it")
    else:
        report.manual.append(f"skills: NOT APPLIED ({skill_plan.reason})")
        report.manual.extend("  " + line for line in skill_plan.instructions)
    report.manual.extend(station_instructions(preset))

    if args.commit and client is not None and not report.failures:
        _execute(client, preset, report, ledger, world, args.keep_going)
        ledger.save()
        report.executed.append(f"ledger written to {ledger_path}")

    print_report(report, preset, ledger_path)
    return 0 if report.ok() else 1


def _execute(
    client: RconClient,
    preset: Preset,
    report: Report,
    ledger: Ledger,
    world: str,
    keep_going: bool,
) -> None:
    for step in report.planned:
        try:
            response = client.command(step.command)
        except (RconError, OSError) as exc:
            report.failures.append(f"{step.command}: transport error {exc}")
            if not keep_going:
                return
            continue

        if step.phase == "keys":
            key = step.command.split(maxsplit=1)[1]
            try:
                now = parse_global_keys(client.command("globalKeys"))
            except (RconError, OSError) as exc:
                report.failures.append(f"globalKeys readback after {key}: {exc}")
                return
            if key not in now:
                report.failures.append(
                    f"addGlobalKey {key}: readback does not contain the key. Response was "
                    f"{response!r}; key set is {sorted(now)}. Stopping: CLLC and ZenWorldSettings "
                    "react to keys and a half-applied chain must not be left ambiguous."
                )
                return
            ledger.record_key(world, key)
            report.executed.append(f"{step.command} -> confirmed ({len(now)} keys now set)")
            continue

        # items
        fields = step.command.split()
        player, prefab = fields[1], fields[2]
        count = int(fields[fields.index("-count") + 1])
        quality = int(fields[fields.index("-quality") + 1])
        if _looks_like_failure(response):
            report.failures.append(f"{step.command}: server said {response!r}")
            if not keep_going:
                return
            continue
        ledger.record(world, preset.name, player, prefab, quality, count)
        report.executed.append(f"{step.command} -> {response.strip() or 'ok'}")


FAILURE_MARKERS = ("could not find", "not found", "unknown", "invalid", "error", "no such", "failed", "usage:")


def _looks_like_failure(response: str) -> bool:
    lowered = (response or "").lower()
    return any(marker in lowered for marker in FAILURE_MARKERS)


def station_instructions(preset: Preset) -> list[str]:
    lines = ["stations: NOT APPLIED (station level comes from extension pieces; build them in-game)"]
    for station in preset.stations:
        exts = station.get("extensions") or []
        if station.get("max_level", 1) <= 1 and not exts:
            continue
        lines.append(
            f"  {station['prefab']}: level {station['level']}/{station.get('max_level')} "
            f"= base + {len(exts)} extension(s): {', '.join(exts) if exts else 'none'}"
        )
    singles = [s["prefab"] for s in preset.stations if s.get("max_level", 1) <= 1 and not (s.get("extensions") or [])]
    if singles:
        lines.append("  single-level pieces to place: " + ", ".join(singles))
    lines.append("  Build these from the ulfsland-admin client edition only.")
    return lines


def print_report(report: Report, preset: Preset, ledger_path: Path) -> None:
    mode = "DRY RUN" if report.dry_run else "COMMIT"
    print(f"== jumpstart {report.preset} [{mode}] world={report.world}")
    print(f"   {preset.summary}")
    print(f"   players: {', '.join(report.players) or '(none)'}")
    print(f"   ledger:  {ledger_path}")
    print()
    print(f"-- AUTOMATED ({len(report.planned)} rcon commands)")
    for step in report.planned:
        print(f"   {step.command}")
        if step.detail:
            print(f"       # {step.detail}")
    if not report.planned:
        print("   (nothing to do)")
    print()
    if report.skipped:
        print(f"-- SKIPPED ({len(report.skipped)}, already satisfied or out of scope)")
        for line in report.skipped:
            print(f"   {line}")
        print()
    print("-- LAUNCH (operator edits valheim.env, then restarts)")
    for line in report.launch:
        print(f"   {line}")
    print()
    print("-- MANUAL")
    for line in report.manual:
        print(f"   {line}")
    print()
    if report.executed:
        print(f"-- EXECUTED ({len(report.executed)})")
        for line in report.executed:
            print(f"   {line}")
        print()
    if report.failures:
        print(f"-- FAILURES ({len(report.failures)})")
        for line in report.failures:
            print(f"   !! {line}")
        print()
        print("   Nothing was silently skipped. Fix the above and re-run; the ledger makes the")
        print("   retry a delta, not a duplicate.")


# ==========================================================================
# other subcommands
# ==========================================================================
def run_list(args: argparse.Namespace) -> int:
    for name in all_preset_names():
        preset = load_preset(name)
        print(f"{preset.tier}  {name:20} {preset.summary}")
    return 0


def run_show(args: argparse.Namespace) -> int:
    preset = load_preset(args.preset)
    grants = preset.grants()
    print(f"{preset.name}  tier {preset.tier}  {preset.raw.get('biome')} / {preset.raw.get('boss')}")
    print(f"  {preset.summary}")
    print(f"  global_keys     ({len(preset.global_keys)}): {', '.join(preset.global_keys) or '(none)'}")
    print(f"  world_modifiers        : {preset.modifier_args()}")
    for bucket in ("kit", "materials", "chain_items"):
        rows = [g for g in grants if g.bucket == bucket]
        print(f"  {bucket:15} ({len(rows):3}): " + ", ".join(f"{g.prefab}x{g.count}" for g in rows[:8]) + (" ..." if len(rows) > 8 else ""))
    print(f"  skills          ({len(preset.skills)}): max {max(preset.skills.values(), default=0)}")
    print(f"  stations        ({len(preset.stations)}):")
    for station in preset.stations:
        if station.get("max_level", 1) > 1:
            print(f"      {station['prefab']} level {station['level']}/{station['max_level']}")
    notes = preset.raw.get("notes") or {}
    for heading in ("withholds", "gaps"):
        for line in notes.get(heading) or []:
            print(f"  {heading[:4]}: {line}")
    return 0


def run_verify(args: argparse.Namespace) -> int:
    index = PrefabIndex.load(Path(args.index))
    names = args.preset or all_preset_names()
    seen: dict[tuple[str, str], str] = {}
    weak: list[tuple[str, str, list[str]]] = []
    unknown: list[tuple[str, str]] = []
    uncorroborated: list[str] = []
    total = 0
    for name in names:
        preset = load_preset(name)
        for prefab, kind in preset.prefab_refs():
            total += 1
            if (prefab, kind) in seen:
                continue
            verdict = index.classify(prefab, kind)
            seen[(prefab, kind)] = verdict
            evidence = index.evidence(prefab, kind)
            if verdict == "weak":
                weak.append((name, prefab, evidence))
            elif verdict == "unknown":
                unknown.append((name, prefab))
            elif kind == "piece" and "piece-desc" not in evidence:
                uncorroborated.append(prefab)
    print(f"index generated {index.meta.get('generated_utc')} from {index.meta.get('valheim_root')}")
    print(f"{len(names)} preset(s), {total} prefab references, {len(seen)} distinct")
    counts = {v: sum(1 for x in seen.values() if x == v) for v in ("verified", "weak", "unknown")}
    kinds = {k: sum(1 for (_, kind) in seen if kind == k) for k in ("item", "piece")}
    print(f"items={kinds['item']} pieces={kinds['piece']}")
    print(f"verified={counts['verified']} weak={counts['weak']} unknown={counts['unknown']}")
    for name, prefab, evidence in weak:
        print(f"  WEAK    {name}: {prefab} (only {', '.join(evidence)})")
    for name, prefab in unknown:
        print(f"  UNKNOWN {name}: {prefab} -- no evidence in any channel")
    if uncorroborated:
        print(
            f"  note: {len(uncorroborated)} piece(s) verified by prefab token only, with no "
            f"localization description key to corroborate: {', '.join(sorted(set(uncorroborated)))}"
        )
    if args.detail:
        for prefab, kind in sorted(seen):
            print(f"    {kind:5} {prefab:34} {', '.join(index.evidence(prefab, kind))}")
    return 1 if unknown else 0


def run_itemset(args: argparse.Namespace) -> int:
    """Emit an AdminQoL item set: the closest thing to an automated skill grant."""
    preset = load_preset(args.preset)
    items = []
    for grant in preset.grants():
        row: dict = {"prefab": grant.prefab, "quality": grant.quality, "stack": grant.count}
        items.append(row)
    doc = {
        f"jumpstart-{preset.name}": {
            "items": items,
            "skills": [{"skill": name, "level": level} for name, level in sorted(preset.skills.items())],
            "knownStations": sorted({str(s["prefab"]) for s in preset.stations}),
        }
    }
    banner = (
        f"# AdminQoL item set generated by tools/jumpstart/jumpstart.py from\n"
        f"# {preset.path.name}. Append to the ulfsland-admin client's\n"
        f"# BepInEx/config/AdminQoL/AdminQoL.ItemSets.yml, then in game:\n"
        f"#   adminqol_itemsets_reload\n"
        f"#   adminqol_itemsets_list\n"
        f"#   itemset jumpstart-{preset.name}\n"
        f"# AdminQoL applies an item set to the INVOKING admin's local character only.\n"
        f"# 'knownStations' only teaches the recipes; it does not place the pieces, and it\n"
        f"# cannot raise a station's LEVEL -- extensions must be built.\n"
        f"# EpicLoot magic/legendary items are not expressible here and cannot be granted\n"
        f"# remotely at all; see README.\n"
    )
    text = banner + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, width=100)
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


# ==========================================================================
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list presets in tier order").set_defaults(func=run_list)

    show = sub.add_parser("show", help="summarise one preset")
    show.add_argument("preset")
    show.set_defaults(func=run_show)

    verify = sub.add_parser("verify", help="name-check every prefab against the index")
    verify.add_argument("preset", nargs="*")
    verify.add_argument("--index", default=str(INDEX_PATH))
    verify.add_argument("--detail", action="store_true", help="print per-prefab evidence")
    verify.set_defaults(func=run_verify)

    itemset = sub.add_parser("itemset", help="emit AdminQoL item-set YAML for the manual path")
    itemset.add_argument("preset")
    itemset.add_argument("--out")
    itemset.set_defaults(func=run_itemset)

    apply_ = sub.add_parser("apply", help="apply a preset (keys + items) over rcon")
    apply_.add_argument("preset")
    apply_.add_argument("--players", action="append", help="SteamID64, repeatable or comma-separated")
    apply_.add_argument("--commit", action="store_true", help="actually send the commands")
    apply_.add_argument("--dry-run", action="store_true", help="explicit no-op (the default)")
    apply_.add_argument("--keys-only", action="store_true")
    apply_.add_argument("--items-only", action="store_true")
    apply_.add_argument("--keep-going", action="store_true", help="continue past item failures (still exits non-zero)")
    apply_.add_argument("--world", default=DEFAULT_WORLD)
    apply_.add_argument("--valheim-root", default=DEFAULT_VALHEIM_ROOT)
    apply_.add_argument("--rcon-host", default="127.0.0.1")
    apply_.add_argument("--rcon-port", type=int, default=None, help="default: Port from the deployed rcon config")
    apply_.add_argument("--rcon-config", default=None, help="default: <valheim-root>/<world>/config_merged/bepinex/org.tristan.rcon.cfg")
    apply_.add_argument("--ledger", default=None)
    apply_.add_argument("--index", default=str(INDEX_PATH))
    apply_.add_argument("--skill-backend", default="auto", choices=["auto", *sorted(SKILL_BACKENDS)])
    apply_.add_argument("--timeout", type=float, default=10.0)
    apply_.set_defaults(func=run_apply)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "commit", False) and getattr(args, "dry_run", False):
        raise SystemExit("--commit and --dry-run are mutually exclusive")
    if getattr(args, "keys_only", False) and getattr(args, "items_only", False):
        raise SystemExit("--keys-only and --items-only are mutually exclusive")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
