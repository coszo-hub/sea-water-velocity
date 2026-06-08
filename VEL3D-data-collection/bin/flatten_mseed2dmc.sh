#!/usr/bin/env bash
# Flatten output/mseed2dmc/<YYYY>/*.mseed into output/mseed2dmc/.
# Each year subdirectory is moved file-by-file into the parent and then
# removed if empty. Use --dry-run first to preview, then re-run without it.
#
# Usage:
#   bin/flatten_mseed2dmc.sh --dry-run
#   bin/flatten_mseed2dmc.sh
#   bin/flatten_mseed2dmc.sh /path/to/mseed2dmc   # override target dir

set -euo pipefail

DRY_RUN=0
TARGET=""

for arg in "$@"; do
    case "$arg" in
        --dry-run|-n) DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) TARGET="$arg" ;;
    esac
done

if [[ -z "$TARGET" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    TARGET="$(cd "$SCRIPT_DIR/.." && pwd)/output/mseed2dmc"
fi

if [[ ! -d "$TARGET" ]]; then
    echo "error: target directory does not exist: $TARGET" >&2
    exit 1
fi

echo "Target: $TARGET"
[[ $DRY_RUN -eq 1 ]] && echo "Mode:   DRY RUN (no files moved)"

shopt -s nullglob
year_dirs=( "$TARGET"/[0-9][0-9][0-9][0-9] )

if (( ${#year_dirs[@]} == 0 )); then
    echo "No YYYY subdirectories found. Nothing to do."
    exit 0
fi

total_moved=0
total_conflicts=0

for ydir in "${year_dirs[@]}"; do
    [[ -d "$ydir" ]] || continue
    year="$(basename "$ydir")"

    file_count=$(find "$ydir" -mindepth 1 -maxdepth 1 -type f | wc -l | tr -d ' ')
    echo
    echo "[$year] $file_count file(s) to move"

    moved=0
    conflicts=0

    # Use find -print0 to handle any filenames safely.
    while IFS= read -r -d '' src; do
        fname="$(basename "$src")"
        dst="$TARGET/$fname"

        if [[ -e "$dst" ]]; then
            echo "  CONFLICT: $fname already exists in target, skipping"
            conflicts=$((conflicts + 1))
            continue
        fi

        if [[ $DRY_RUN -eq 1 ]]; then
            echo "  would mv: $year/$fname"
        else
            mv -n "$src" "$dst"
        fi
        moved=$((moved + 1))
    done < <(find "$ydir" -mindepth 1 -maxdepth 1 -type f -print0)

    # Print a single-line summary per year for real runs (less noise).
    if [[ $DRY_RUN -eq 0 ]]; then
        echo "  moved $moved file(s)${conflicts:+, $conflicts conflict(s)}"
    fi

    total_moved=$((total_moved + moved))
    total_conflicts=$((total_conflicts + conflicts))

    # Remove the year dir if it's now empty (and we're not dry-running).
    if [[ $DRY_RUN -eq 0 ]]; then
        if [[ -z "$(ls -A "$ydir" 2>/dev/null)" ]]; then
            rmdir "$ydir"
            echo "  removed empty $year/"
        else
            echo "  $year/ not empty after move (contains subdirs or skipped files); left in place"
        fi
    fi
done

echo
echo "Done. Total moved: $total_moved, conflicts: $total_conflicts"
[[ $DRY_RUN -eq 1 ]] && echo "This was a dry run. Re-run without --dry-run to actually move files."
