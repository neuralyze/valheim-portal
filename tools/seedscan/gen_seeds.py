#!/usr/bin/env python3
"""Emit candidate Valheim seed strings, one per line.

The alphabet and length are taken from World.GenerateSeed in
assembly_valheim.dll (Valheim 1.0.12), measured from its IL:

    "abcdefghijklmnpqrstuvwxyzABCDEFGHIJKLMNPQRSTUVWXYZ023456789", 10 chars

Note the deliberate omissions: no 'o', no 'O', no '1'. Seeds produced here are
therefore indistinguishable from ones the game's own "randomise" button makes.

Usage:
  tools/seedscan/gen_seeds.py 1000 [--rng 1] [--include 8JiFcknsJd] > seeds.txt
"""

import argparse
import random
import sys

ALPHABET = "abcdefghijklmnpqrstuvwxyzABCDEFGHIJKLMNPQRSTUVWXYZ023456789"
LENGTH = 10


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("count", type=int)
    ap.add_argument("--rng", type=int, default=1, help="python RNG seed, for reproducibility")
    ap.add_argument("--include", action="append", default=[],
                    help="emit this seed first (use for controls)")
    args = ap.parse_args()

    rng = random.Random(args.rng)
    seen = set()
    out = []
    for s in args.include:
        if s not in seen:
            seen.add(s)
            out.append(s)
    while len(out) < args.count + len(args.include):
        s = "".join(rng.choice(ALPHABET) for _ in range(LENGTH))
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
