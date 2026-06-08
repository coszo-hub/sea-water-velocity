#!/home/coszo/miniconda3/envs/ooi_env/bin/python

# read variables from the parameter file
def read_param(filename):
    """
    Read a parameter file of lines like:
        key = value1; value2; ... # optional inline comment

    Notes:
      - Values are split ONLY on semicolons (';').
      - Commas (',') are NOT treated as separators and remain part of the value.
        For example:
            c_description = pressure_temp, Pressure Sensor Internal Temperature
        will be read as a single list element.

    Returns:
        dict[str, list]: each key maps to a list of string values.
    """
    params = {}

    with open(filename, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()

            # Skip empty lines and full-line comments
            if not line or line.startswith("#"):
                continue

            # Strip inline comments
            line_no_comment = line.partition("#")[0].strip()
            if not line_no_comment:
                continue

            # Split key and value
            if "=" not in line_no_comment:
                continue

            left, right = line_no_comment.split("=", 1)
            key = left.strip()
            value_field = right.strip()

            # Split ONLY on semicolons; commas are preserved
            values = [v.strip() for v in value_field.split(";") if v.strip()]

            if key not in params:
                params[key] = []

            params[key].extend(values)

    return params