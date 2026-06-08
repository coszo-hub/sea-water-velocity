#!/home/coszo/miniconda3/envs/ooi_env/bin/python

import os
import time
import glob
import socket
import sys

from mail import *

# Forrest: add endtime* and gap* run file patterns,  organize messages by pattern.
# Forrest: send only 1 email for all files found in each run - it used to send 1 email per file.

# This idiom gets the parent dir of this module.
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))

# Get all 'txt' files in 'run' dir.
run_path = os.path.join(root_path, "run")

# Only check recency of endtime*.txt files - we may not get gap*,txt updates for a long time.
run_file_patterns = ["endtime*.txt"]
# [ 'endtime*.txt', 'gap*.txt' ];

now = time.time()

# Max delay is 1 day.
maxDelay = 86400
hoursDelay = int(maxDelay / 3600)

subject = "Some OOI 'run' files in " + run_path + " have not recently run"
body = ""

for run_file_pattern in run_file_patterns:

    print("Checking file pattern: " + run_file_pattern)

    run_files = glob.glob(os.path.join(run_path, run_file_pattern))

    # Find which run files have not been updated recently.
    latefile_msgs = []

    for run_file in run_files:

        # Time in seconds since last modification of file
        file_mod_time = os.stat(run_file).st_mtime
        file_age = abs(now - file_mod_time)
        path, run_filename = os.path.split(run_file)

        print("Checking file " + run_filename + " age " + str(file_age))

        if file_age > maxDelay:
            # ?? if (file_mod_time < now) :

            file_age_days = int(file_age / 86400.0)
            file_age_hrs = int((file_age - file_age_days * 86400.0) / 3600.0)
            file_age_min = int(
                (file_age - file_age_days * 86400.0 - file_age_hrs * 3600.0) / 60.0
            )
            latefile_msgs.append(
                " - file %s - last updated %d days %d hours %d min ago"
                % (run_filename, file_age_days, file_age_hrs, file_age_min)
            )

    # Append any message for this pattern onto the message body.
    if len(latefile_msgs) > 0:

        body += (
            "CRITICAL: these OOI "
            + run_file_pattern
            + " files have not recently run:\n\n"
        )
        body += "\n".join(latefile_msgs)

if len(body) > 0:

    sendmail(subject, body)

else:

    print("All files have been run within " + str(hoursDelay) + " hours ago")
