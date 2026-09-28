#!/usr/bin/env python3
"""
NES Open Tournament Golf - Mesen Label Editor

Add/edit/remove/list entries in a Mesen ".mlb" label file from the command
line, so labels discovered during reverse-engineering (with golf-rom-peek,
disassembly notes, etc.) can be recorded without hand-editing the raw file.

Where labels are written:
  `add`/`edit`/`remove` write to the label file itself (positional
  `mlb_file`); the file is kept in git, so a change is reviewed with
  `git diff` there. `add` refuses a name another label already uses, and
  warns when the new label's range overlaps another label's (nested ranges,
  such as a string inside a text table, are fine).

  Pass --target sidecar to write to "<mlb_file stem>.sidecar.mlb" next to it
  instead (or the path --sidecar names), to keep entries apart until they are
  merged. Lookups everywhere see the sidecar shadowing the label file, and
  `list` tags each row's source.

Merging a sidecar into the label file:
  `merge` prints what folding the sidecar into the label file would do: the
  count of new labels, every label a sidecar entry replaces (name, comment and
  range changes), and the conflicts - a sidecar label whose range overlaps
  another label, or a name held at two addresses. Nothing is written without
  --write, which refuses while conflicts remain (fix them with `edit`/`remove`,
  or accept them with --allow-conflicts). --write copies both files to
  "<file>.bak", writes the merged label file and deletes the sidecar.

  The label file describes the vanilla ROM only: never label code or RAM that
  exists only after a patch.

Label types (see golf.core.mlb_labels for the full address semantics):
  prg  - NesPrgRom:    raw PRG ROM offset (same numbering as golf-rom-peek's
                        raw hex address form / --bank-resolved "$XXXX")
  ram  - NesInternalRam: CPU-space system RAM address ($0000-$1FFF)
  sram - NesSaveRam:    offset into cartridge battery-backed SRAM
  mem  - NesMemory:     CPU-space memory-mapped register address

Examples:
    golf-labels notes.mlb list --filter Scorecard
    golf-labels notes.mlb add prg '$AD5D' --bank 2 CourseSelectHandler
    golf-labels notes.mlb add ram 001A ScrollX --comment "current scroll X"
    golf-labels notes.mlb edit prg '$AD5D' --bank 2 --comment "confirmed via trace"
    golf-labels notes.mlb remove ram 001A
    golf-labels notes.mlb add prg '$AD5D' --bank 2 CourseSelectHandler --target sidecar
    golf-labels notes.mlb merge
    golf-labels notes.mlb merge --verbose --write
"""

import argparse
import os
import shutil
import sys

from golf.core.mlb_labels import (
    TYPE_ALIASES,
    Label,
    LabelStore,
    find_conflicts,
    plan_merge,
)
from golf.core.rom_utils import parse_cpu_or_prg_address, prg_to_bank_and_cpu

_TYPE_SHORT = {v: k for k, v in TYPE_ALIASES.items()}


def _parse_prg_address_or_range(
    address: str, bank: int | None
) -> tuple[int, int | None]:
    if "-" in address:
        lo, hi = address.split("-", 1)
        return parse_cpu_or_prg_address(lo, bank), parse_cpu_or_prg_address(hi, bank)
    return parse_cpu_or_prg_address(address, bank), None


def _parse_plain_address_or_range(address: str) -> tuple[int, int | None]:
    address = address.strip()
    if "-" in address:
        lo, hi = address.split("-", 1)
        return int(lo.strip().lstrip("$"), 16), int(hi.strip().lstrip("$"), 16)
    return int(address.lstrip("$"), 16), None


def _address_range(args) -> tuple[str, int, int | None]:
    type_ = TYPE_ALIASES[args.type]
    if type_ == "NesPrgRom":
        start, end = _parse_prg_address_or_range(args.address, args.bank)
    else:
        start, end = _parse_plain_address_or_range(args.address)
    return type_, start, end


def cmd_list(store: LabelStore, args) -> None:
    type_filter = TYPE_ALIASES[args.type] if args.type else None
    needle = args.filter.lower() if args.filter else None
    for label, source in store.iter_merged():
        if args.source != "all" and source != args.source:
            continue
        if type_filter and label.type != type_filter:
            continue
        if needle:
            haystack = label.name.lower() + " " + (label.comment or "").lower()
            if needle not in haystack:
                continue
        print(f"[{source}] {label.to_line()}")


def _check_conflicts(store: LabelStore, label: Label) -> None:
    """Refuse a name already in use; warn about an overlapping range."""
    merged = [other for other, _ in store.iter_merged()]
    overlapping, same_name = find_conflicts(merged, label)
    if same_name:
        held = ", ".join(f"{o.type}:{o.address_str}" for o in same_name)
        print(
            f"Error: the name {label.name} is already used at {held}. "
            "Choose another name, or rename that label first.",
            file=sys.stderr,
        )
        sys.exit(1)
    for other in overlapping:
        print(
            f"Warning: {label.type}:{label.address_str} overlaps "
            f"{other.address_str} ({other.name}). Fine if one is nested inside "
            "the other on purpose; otherwise fix one of the ranges.",
            file=sys.stderr,
        )


def cmd_add(store: LabelStore, args) -> None:
    type_, start, end = _address_range(args)
    target = store.index_for(args.target)

    existing = target.find_exact(type_, start)
    if existing is not None and not args.force:
        print(
            f"Error: a label already exists at {type_}:{existing.address_str} "
            f"({existing.name}) in {args.target}. Use --force to overwrite, or `edit`.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.target == "sidecar":
        shadowed = store.base.find_exact(type_, start)
        if shadowed is not None and existing is None:
            print(
                f"Note: base already has a label at {type_}:{shadowed.address_str} "
                f"({shadowed.name}) - this sidecar entry will shadow it until merged.",
                file=sys.stderr,
            )

    label = Label(type_, start, end, args.name, args.comment)
    _check_conflicts(store, label)
    target.add(label)
    store.save(args.target)
    print(f"Added to {args.target} ({store.path_for(args.target)}): {label.to_line()}")


def cmd_edit(store: LabelStore, args) -> None:
    type_, start, _end = _address_range(args)
    target = store.index_for(args.target)
    label = target.find_exact(type_, start)
    if label is None:
        other = "base" if args.target == "sidecar" else "sidecar"
        hint = ""
        if store.index_for(other).find_exact(type_, start) is not None:
            hint = f" (found in {other} instead - pass --target {other} if that's the one to edit)"
        print(
            f"Error: no label found at {type_}:{start:04X} in {args.target}{hint}",
            file=sys.stderr,
        )
        sys.exit(1)
    if not args.name and args.comment is None:
        print(
            "Error: nothing to change (pass --name and/or --comment)", file=sys.stderr
        )
        sys.exit(1)
    if args.name and args.name != label.name:
        _check_conflicts(
            store, Label(label.type, label.start, label.end, args.name, label.comment)
        )
        label.name = args.name
    if args.comment is not None:
        label.comment = args.comment or None
    store.save(args.target)
    print(f"Updated in {args.target}: {label.to_line()}")


def cmd_remove(store: LabelStore, args) -> None:
    type_, start, _end = _address_range(args)
    target = store.index_for(args.target)
    removed = target.remove(type_, start)
    if removed is None:
        other = "base" if args.target == "sidecar" else "sidecar"
        hint = ""
        if store.index_for(other).find_exact(type_, start) is not None:
            hint = f" (found in {other} instead - pass --target {other} if that's the one to remove)"
        print(
            f"Error: no label found at {type_}:{start:04X} in {args.target}{hint}",
            file=sys.stderr,
        )
        sys.exit(1)
    store.save(args.target)
    print(f"Removed from {args.target}: {removed.to_line()}")


def _where(label: Label) -> str:
    text = f"{_TYPE_SHORT.get(label.type, label.type)} {label.address_str}"
    if label.type == "NesPrgRom":
        bank, cpu = prg_to_bank_and_cpu(label.start)
        text += f" (bank {bank} ${cpu:04X})"
    return text


def _print_replacement(base: Label, side: Label) -> None:
    print(f"  {_where(side)}")
    if base.name != side.name:
        print(f"    name:    {base.name or '(empty)'} -> {side.name}")
    if base.address_str != side.address_str:
        print(f"    range:   {base.address_str} -> {side.address_str}")
    if (base.comment or "") != (side.comment or ""):
        print(f"    comment: {base.comment or '(none)'}")
        print(f"          -> {side.comment or '(none)'}")


def cmd_merge(store: LabelStore, args) -> None:
    if not store.sidecar.labels:
        print("The sidecar is empty: nothing to merge.")
        return
    plan = plan_merge(store)

    print(f"Merging {store.sidecar_path} into {store.base_path}")
    print(
        f"  {len(plan.added)} new, {len(plan.replaced)} replacing a base label, "
        f"{len(plan.unchanged)} identical to base; "
        f"{len(store.base.labels)} -> {len(plan.merged)} labels"
    )

    if args.verbose and plan.added:
        print(f"\nNew labels ({len(plan.added)}):")
        for label in plan.added:
            print(f"  {_where(label)}  {label.name}")

    renames = [(b, s) for b, s in plan.replaced if b.name != s.name]
    others = [(b, s) for b, s in plan.replaced if b.name == s.name]
    if renames:
        print(f"\nRenamed base labels ({len(renames)}):")
        for base, side in renames:
            _print_replacement(base, side)
    if others:
        print(f"\nComment or range changes to base labels ({len(others)}):")
        for base, side in others:
            _print_replacement(base, side)

    if plan.overlaps:
        print(f"\nConflict - overlapping ranges ({len(plan.overlaps)}):")
        for side, other in plan.overlaps:
            print(
                f"  {_where(side)} {side.name}  overlaps  "
                f"{other.address_str} {other.name}"
            )
    if plan.duplicate_names:
        print(
            f"\nConflict - one name at several addresses ({len(plan.duplicate_names)}):"
        )
        for name, holders in plan.duplicate_names.items():
            print(f"  {name}: " + ", ".join(_where(h) for h in holders))

    if not args.write:
        print("\nDry run: pass --write to apply.")
        return
    if plan.has_conflicts and not args.allow_conflicts:
        print(
            "\nError: resolve the conflicts above with `edit`/`remove`, "
            "or pass --allow-conflicts to merge them as they are.",
            file=sys.stderr,
        )
        sys.exit(1)

    for path in (store.base_path, store.sidecar_path):
        assert path is not None
        shutil.copy2(path, f"{path}.bak")
    store.base.labels = plan.merged
    store.save("base")
    assert store.sidecar_path is not None
    os.remove(store.sidecar_path)
    print(
        f"\nWrote {len(plan.merged)} labels to {store.base_path} and removed the "
        "sidecar; the previous versions of both are saved as .bak files."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Add/edit/remove/list labels in a Mesen .mlb label file"
    )
    parser.add_argument("mlb_file", help="Path to the base .mlb label file")
    parser.add_argument(
        "--sidecar",
        help="Path to the writable sidecar overlay (default: '<mlb_file stem>.sidecar.mlb')",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list", help="List labels (merged base + sidecar view)"
    )
    list_parser.add_argument(
        "--type", choices=list(TYPE_ALIASES), help="Restrict to one type"
    )
    list_parser.add_argument(
        "--filter", help="Case-insensitive substring to match name/comment"
    )
    list_parser.add_argument(
        "--source",
        choices=["all", "base", "sidecar"],
        default="all",
        help="Restrict to one source",
    )

    add_parser = subparsers.add_parser("add", help="Add a new label")
    add_parser.add_argument("type", choices=list(TYPE_ALIASES))
    add_parser.add_argument(
        "address",
        help="'$XXXX' (prg, needs --bank for switchable) or raw hex; 'START-END' for a range",
    )
    add_parser.add_argument("name")
    add_parser.add_argument("--comment")
    add_parser.add_argument(
        "--bank", type=int, help="Switchable bank number (0-14), for type=prg"
    )
    add_parser.add_argument(
        "--target",
        choices=["base", "sidecar"],
        default="base",
        help="Which file to write to",
    )
    add_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing label at this address",
    )

    edit_parser = subparsers.add_parser(
        "edit", help="Rename/re-comment an existing label"
    )
    edit_parser.add_argument("type", choices=list(TYPE_ALIASES))
    edit_parser.add_argument("address")
    edit_parser.add_argument("--name")
    edit_parser.add_argument("--comment")
    edit_parser.add_argument(
        "--bank", type=int, help="Switchable bank number (0-14), for type=prg"
    )
    edit_parser.add_argument(
        "--target",
        choices=["base", "sidecar"],
        default="base",
        help="Which file to edit",
    )

    remove_parser = subparsers.add_parser("remove", help="Remove a label")
    remove_parser.add_argument("type", choices=list(TYPE_ALIASES))
    remove_parser.add_argument("address")
    remove_parser.add_argument(
        "--bank", type=int, help="Switchable bank number (0-14), for type=prg"
    )
    remove_parser.add_argument(
        "--target",
        choices=["base", "sidecar"],
        default="base",
        help="Which file to remove from",
    )

    merge_parser = subparsers.add_parser(
        "merge", help="Fold the sidecar into the base file (dry run unless --write)"
    )
    merge_parser.add_argument(
        "--write",
        action="store_true",
        help="Write the merged base file and delete the sidecar",
    )
    merge_parser.add_argument(
        "--allow-conflicts",
        action="store_true",
        help="With --write, merge even with overlapping ranges or duplicate names",
    )
    merge_parser.add_argument(
        "--verbose", action="store_true", help="Also list every new label"
    )

    args = parser.parse_args()
    store = LabelStore.load(args.mlb_file, args.sidecar)

    commands = {
        "list": cmd_list,
        "add": cmd_add,
        "edit": cmd_edit,
        "remove": cmd_remove,
        "merge": cmd_merge,
    }
    try:
        commands[args.command](store, args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
