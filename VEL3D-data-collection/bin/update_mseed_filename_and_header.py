import os
from obspy import read

# --- USER SETTINGS ---
input_dir = "/home/coszo/coszo-data-collection/output/mseed_to_fix/needs_loc_code_update/UK1/"
output_dir = "/home/coszo/coszo-data-collection/output/fixed_mseed/UK1_01_to_UK1_10/"

new_channel = "UK1"     # e.g., "HHZ", "BHN", "EHZ"
new_location = "10"     # must be 2 characters; "" allowed
# ----------------------

os.makedirs(output_dir, exist_ok=True)

for fname in os.listdir(input_dir):
    if not fname.lower().endswith((".mseed", ".ms")):
        continue

    in_path = os.path.join(input_dir, fname)

    # Parse filename components
    parts = fname.split(".")
    if len(parts) < 5:
        print(f"Skipping unexpected filename format: {fname}")
        continue

    # Replace location (index 2) and channel (index 3)
    parts[2] = new_location
    parts[3] = new_channel

    new_fname = ".".join(parts)
    out_path = os.path.join(output_dir, new_fname)

    # Read and update MiniSEED header
    st = read(in_path)
    for tr in st:
        tr.stats.location = new_location
        tr.stats.channel = new_channel

    # Write updated file
    st.write(out_path, format="MSEED")

print("Done. Updated files saved to:", output_dir)
