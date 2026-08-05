#!/usr/bin/env python3
"""
Rename accumulated rail route keys to the new route IDs.

Rail route IDs are now TrainView's exact `line` strings, so four keys
change name.  data/ holds months of history that cannot be regenerated,
so the rename is applied to the stored stats rather than orphaning them.

Run with the server stopped: today.json is held in memory and flushed on
a debounce, so a running process would write the old keys straight back.

Both files are backed up alongside the original before anything is
written, and the script is idempotent — re-running it is a no-op.

Usage:
    python3 scripts/migrate_rail_keys.py [--dry-run]
"""

import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pkg.helpers import DAILY_CDFS, TODAY, dump, load  # noqa: E402

RENAMES = {
    "Lansdale":   "Lansdale/Doylestown",
    "Media":      "Media/Wawa",
    "Manayunk":   "Manayunk/Norristown",
    "Paoli":      "Paoli/Thorndale",
}


def _merge_days(dst: dict, src: dict) -> dict:
    """Merge {day: [entries]} buckets, keeping both sides' entries."""
    for day, entries in src.items():
        if day in dst:
            combined = list(dst[day]) + list(entries)
            # Buckets are kept sorted by start-minute; entries are either
            # bare floats (daily_cdfs) or dicts with a "start" key (today).
            combined.sort(key=lambda e: e["start"] if isinstance(e, dict) else e)
            dst[day] = combined
        else:
            dst[day] = entries
    return dst


def migrate(path: Path, dry_run: bool) -> int:
    data = load(path)
    if not data:
        print(f"  {path.name}: empty or missing — nothing to do")
        return 0

    changed = 0
    for old, new in RENAMES.items():
        if old not in data:
            continue
        moved = data.pop(old)
        if new in data:
            print(f"  {path.name}: merging {old!r} into existing {new!r}")
            data[new] = _merge_days(data[new], moved)
        else:
            data[new] = moved
        n = sum(len(v) for v in moved.values())
        print(f"  {path.name}: {old!r} -> {new!r}  ({len(moved)} days, {n} entries)")
        changed += 1

    if not changed:
        print(f"  {path.name}: already migrated")
        return 0

    if dry_run:
        print(f"  {path.name}: dry run — not written")
        return changed

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}.pre_rail_rename_{stamp}{path.suffix}")
    shutil.copy2(path, backup)
    print(f"  {path.name}: backed up to {backup.name}")
    dump(path, data)
    return changed


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("Dry run — no files will be modified.\n")

    total = 0
    for path in (DAILY_CDFS, TODAY):
        total += migrate(path, dry_run)

    if total:
        print(f"\nMigrated {total} key(s).")
    else:
        print("\nNothing to migrate.")

    # Sanity check: the old names must be gone from both files.
    if not dry_run:
        for path in (DAILY_CDFS, TODAY):
            stale = sorted(set(RENAMES) & set(load(path)))
            if stale:
                print(f"  ! {path.name} still contains {stale}", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
