#!/usr/bin/env python3

import os

# --- CONFIGURE THESE PATHS ---
directory_path = "/home/coszo/coszo-data-collection/output/mseed2dmc/"
output_file = "/home/coszo/coszo-data-collection/output/mseed_to_fix/old_mseed_files_in_multiple_directories.txt"
# ------------------------------

# Get all entries in the directory
entries = os.listdir(directory_path)

with open(output_file, "a") as f:
    for name in entries:
        f.write(name + "\n")

print(f"Saved {len(entries)} entries to {output_file}")