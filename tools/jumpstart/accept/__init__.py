"""WORLD ACCEPTANCE GATE.  `python3 -m accept` or accept/__main__.py.

Sections live in `accept/sections/`; instruments in `accept/instruments.py`;
the registry - and the two refusals that keep a blind check out - in
`accept/registry.py`.
"""
from .registry import (Case, Finding, Section, SectionResult,  # noqa: F401
                       SECTIONS, register)
