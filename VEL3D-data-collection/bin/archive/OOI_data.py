#!/Users/mika/anaconda3/envs/ooi_env/bin/python

from obspy import UTCDateTime, read, Trace, Stream
from netCDF4 import Dataset

import numpy as np
import os
import sys
import time
import urllib.request
import xml.etree.cElementTree as ET
import requests
import datetime
import ast
from numpy.ma import MaskedArray

from convertutc import *

### History ###
#  2025-11-24 Mika: OOI_data.py skips data requests if:
#  - Length of data is less than 5 samples
#  - If the end time of the data request is more recent that 24 hours before the current time

#  2025-10-01 Mika: This message can be ignored: <frozen importlib._bootstrap>:241: RuntimeWarning: numpy.ndarray size changed, may indicate binary incompatibility. Expected 16 from C header, got 96 from PyObject

#  2025-09-23 Mika: This script has been reformatted to be compatible with Python 3.10. I also fixed the indentations. My modifications are followed by MT.

#  2017-09-20 Manoch: to prevent gaps in data when getting data from M2M, we are told that our start time should not be 00:00:00 and because of that we changed it from file_end_time+0.000001 to file_end_time+0.000100 and that made a huge difference!

#  2017-09-19 Manoch: to follow the code I added comments. My comments are followed by ,mb

#  2017-09-19 Manoch: had problem where script was crashing. The problem ended up being a weak logic to compute nominal sample rate (see my comments for 2017-09-19 in the code

# Get args

if len(sys.argv) != 3:

    print(
        "Need to pass 'reference_name' (with dashes not underscores) and 'run_name' arguments"
    )
    sys.exit()

reference_name_dash = sys.argv[1] # Example: RS01SLBS-MJ01A-06-PRESTA101, log files and urls use reference_name_dash
run_name = sys.argv[2] # Examples: lily (botpt), nano (botpt), prest (seafloor pressure)

reference_name_underscore = reference_name_dash.replace("-", "_") # Example: RS01SLBS_MJ01A_06_PRESTA101, param files use reference_name_underscore

mseed_file_ext = ".seed"

rootPath = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
outputPath = os.path.join(rootPath, "output")  # Path to dataless_seed, mseed, and stationxml output

# Path to network/station (e.g, RS01SLBS_MJ01A_06_PRESTA101_LDO_01.txt), channel/reponse (e.g., RS01SLBS_MJ01A_06_PRESTA101.txt), metadata (run_metadata.txt), and run (run_prest.txt) parameter files. 
# Network/station and channel/response param files are used to fill the mseed header and name the mseed files. 
# Run param file includes information for asynchronous data request from the OOI database and the record length of the mseed files. 
paramPath = os.path.join(rootPath, "param") 

binPath = os.path.join(rootPath, "bin") # Path to executables
runPath = os.path.join(rootPath, "run") # Path to end time files
logPath = os.path.join(rootPath, "log") # Path to log files

mseedPath = os.path.join(outputPath, "mseed") # Path to miniseed files

run_file_name = "run_" + run_name + ".txt" # File includes information needed for data request and converting to miniseed files.
urlDesignator = reference_name_dash.replace("-", "/", 2)

old_stdout = sys.stdout
log_file_name = (
    str(UTCDateTime.now())[0:10] + "-" + reference_name_dash + "-" + run_name + ".log"
)
log_file = open(logPath + "/" + log_file_name, "a")
sys.stdout = log_file 
sys.path.insert(0, binPath)

from readparam import *
from mail import *

# Send test email
# sendmail('TEST EMAIL SEND FROM OPS1')
# sendmail('TEST EMAIL SEND FROM COSZO.') # MT
# sys.exit()

run = readParam(os.path.join(paramPath, run_file_name)) # File includes information needed for data request and converting to miniseed files.

print("========================== ")
print(
    str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    + " - GET OOI DATA "
    + reference_name_dash
    + "-"
    + run_name
)

# Request information for OOI sensor
print("Requesting information from OOI sensor.")  # MT

USERNAME = run["USERNAME"][0] # OOI API Username
TOKEN = run["TOKEN"][0] # OOI API Token

endtime_file_name = "endtime_" + reference_name_dash + "_" + run_name + ".txt" # File with the end time of the last data request. 
file_end_time = UTCDateTime(open(os.path.join(runPath, endtime_file_name), "r").read()) # Convert end time to a UTCDateTime object. 

#  2017-09-20 Manoch: To prevent gaps in data when getting data from M2M. A "fix" suggested by the data provider
# startTime           = str(file_end_time+0.000001)
startTime = str(file_end_time + 0.000100) # Start time of data request. If the first request of the day, start time should be midnight. MT
# startTime = str(file_end_time + 0.100000) 
print("START TIME:", startTime)

# Turn on/off gap file
if int(run["gap"][0]) == 1:

    gap_file_name = "gap_" + reference_name_dash + "_" + run_name + ".txt"

# Set the data request end time
endTime = str(UTCDateTime(startTime) + float(run["time_interval"][0])) # End time of data request. time_interval = duration of data request in seconds given in the run param file. Currently set to 86400 s (24 hours). This code was previously set to only request data up to midnight of the day of the request. MT

print("END TIME:", endTime)

difTime = UTCDateTime(endTime) - UTCDateTime(startTime)

if (
    run["data_endtime"][0] == "NOW"
    or UTCDateTime(run["data_endtime"][0]) >= UTCDateTime.now()
):

    # If the data_endtime given for the data request in the run param file is "Now" or a future date, then set the truncTime to Now - trunctime. Adjust the trunctime parameter based on the ingestion time of the data (time it takes for the data to become available in CI). Currently set to 60 seconds for testing. trunctime was originally set to 24 hours for the BOTPT data. MT.
    print(
        "truncTime set = now() - trunctime parameter " + str(float(run["trunctime"][0]))
    )
    truncTime = UTCDateTime.now() - float(run["trunctime"][0])

else:

    # If the end time for the data request is in the past, set the truncTime to the data_endtime in the run param file. MT
    print("truncTime set = data_endtime")
    truncTime = UTCDateTime(run["data_endtime"][0])

# If the endTime (end time of data request) is less than or equal to the truncTime and the difTime (endTime - startTime) is greater than or equal to the diftime in the run param file (currently 5 seconds), then make data request. MT
if UTCDateTime(endTime) <= UTCDateTime(truncTime) and difTime >= float(
    run["diftime"][0]
):

    # Example URL:
    # 'https://ooinet.oceanobservatories.org/api/m2m/12576/sensor/inv/RS03CCAL/MJ03F/05-BOTPTA301/streamed/botpt_nano_sample?beginDT=2017-12-12T00:00:00.000Z&endDT=2017-12-12T01:00:00.000Z&include_provenance=true&format=application/netcdf'

    baseUrl = run["baseUrl"][0] # Used for requesting deployment information
    dataUrl = run["dataUrl"][0] # Used for requesting data
    
    # Build stream tag for data request url
    # Added stream tag for PREST data - MT
    if 'prest' in run_name:
        streamTag = (
            "streamed/" + run_name
            + "_real_time?include_provenance=true&format=application/netcdf"
        )
    elif 'lily' in run_name or 'nano' in run_name:
        streamTag = (
        "streamed/botpt_"
        + run_name
        + "_sample?include_provenance=true&format=application/netcdf"
        )
    
    # Build url to get deployment ID    
    url = "/".join([baseUrl, urlDesignator])

    # Get deployment ID
    response = requests.get(url, auth=(USERNAME, TOKEN))
    deploymentId = response.json()[-1] # Most recent deployment, MT
    # deploymentId = response.json()[0]

    print("url for deploymentId: ", url, "response", response.status_code)
    print('Most recent deploymentId:', deploymentId)
    
    # Build url to get deployment information
    url = "/".join([url, str(deploymentId)])
    
    # Request deployment information
    response = requests.get(url, auth=(USERNAME, TOKEN))
    deploymentInfo = response.json()[0]

    print("url for deploymentInfo: ", url, "response", response.status_code)

    # Build data request url
    dateTag = "".join(["beginDT=", startTime, "&", "endDT=", endTime])
    url = "/".join([dataUrl, urlDesignator, streamTag])
    url = "&".join([url, dateTag])

    # Make asynchronous data request 
    response = requests.get(url, auth=(USERNAME, TOKEN))

    print("url for data: ", url, "response", response.status_code)

    # If request returns "No data for request", an email is sent to the operator to investigate. 

    # Example response if NO data:
    #   { "status": "No data for request", "code": 200 }
    # Notice status code is 200! But no data returns.
    # However this may indicate an error condition - data collection may have failed at OOI.
    # It might also be a gap.

    if "No data for request" in response.json():

        print(
            "No data for request. Sending email to investigate and alert OOI helpdesk."
        ) 
        sendmail(
            "OOI WARNING: No data for request! " + reference_name_dash + "-" + run_name,
            "OOI data collection may have failed! Investigate and alert OOI Help Desk "
            + url,
        )
        sys.exit()
        
    # If "allURLs" not in response then the data request has failed. 
    
    # Example response if data:
    #   { "allURLs": [ "https://opendap.oceanobservatories.org/thredds/catalog/ooi/manoch@iris.washington.edu/20171213T200504-RS03CCAL-MJ03F-05-BOTPTA301-streamed-botpt_nano_sample/catalog.html" ... }

    if "allURLs" not in response.json():

        # Failure
        print(
            "1 - Request [FAILED] - exiting! Skipping this time interval:",
            next(iter(response.json().keys())) + ":",
            next(iter(response.json().values())),
        )       
        
        # Set time in endtime_ file to the startTime + time_interval from run param file. I.e., Skip this window of data.
        with open(os.path.join(runPath, endtime_file_name), "w") as output:
            output.write(str(UTCDateTime(startTime) + float(run["time_interval"][0])))
            print(
                endtime_file_name,
                "set =",
                str(UTCDateTime(startTime) + float(run["time_interval"][0])),
            )

        # Add startTime, startTime + time_interval to gap file. 
        if int(run["gap"][0]) == 1:

            with open(os.path.join(runPath, gap_file_name), "a") as output:
                output.write(
                    "%s %s %s\n"
                    % (
                        "1 - Data request failed. Skipping this time interval:",
                        str(UTCDateTime(startTime)),
                        str(UTCDateTime(startTime) + float(run["time_interval"][0])),
                    )
                )

        # Send email if there is an error in the data request. 
        if response.status_code >= 400:

            print("Error! Response = " + str(response.status_code))  
            
            sendmail(
                "OOI ERROR: OOI request "
                + reference_name_dash
                + "-"
                + run_name
                + " got status code "
                + str(response.status_code),
                "URL [" + url + "]",
            )

        sys.exit()

    responseUrl = response.json()["allURLs"][1] # This url is a direct link to a web server which you can use to quickly download files if you don't want to go through THREDDS.
    statusUrl = "/".join([responseUrl, "status.json"]) # link to file with status of data request.

    # Wait maximum of 5 status cycles.
    count = 0
    maxCycle = int(run["maxCycle"][0]) # currently 5 cycles.
    delay = int(
        run["delay"][0]
    )  # wait time in seconds (currently 50 seconds)
    success = False
    response_codes = ""

    # Check request status 5 times (maxCycle in run params), but delay by 50 seconds (delay in run params) but sleep between each as it can take a while for the status URL to replay without a 404.
    while count <= maxCycle:

        count += 1
        time.sleep(delay) # currently 50 seconds, MT
        status = requests.get(statusUrl)
        print("url for status", statusUrl, "response", status.status_code)
        print(status.content)

        if status.status_code == 200:
            
            # Successful request
            print(
                str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                "[TRY]",
                str(count),
                "[DONE]",
                "Success",
            )
            success = True
            break

        else:

            # Request incomplete
            print(
                str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                "[TRY]",
                str(count),
                "[DONE]",
                "Not yet",
                "Status code",
                str(status.status_code),
            )
            response_codes = response_codes + "," + str(status.status_code)

    if not success:

        # Here we need to notify the operator via email if unable to complete data request after 5 tries. 
        # Updated email message. The old one implied that the request was sent 5 times and failed each time, which is inaccurate. MT
        sendmail(
        "OOI WARNING: After 5 status requests, data request was still incomplete: "
        + reference_name_dash
        + "-"
        + run_name,
        "Status URL [" + statusUrl + "] got response codes " + response_codes,
        ) 
        
        # Updated email message. The old one implied that the request was sent 5 times and failed each time, which is inaccurate. MT
        print(
            "2 - [FAILED]! COUNTS =",
            count,
            "Data request still incomplete after 5 status requests. Skipping this time interval",
            "Response codes:",
            response_codes,
        ) 

        # Set time in endtime_ file to startTime + time_interval. 
        with open(os.path.join(runPath, endtime_file_name), "w") as output:
            output.write(str(UTCDateTime(startTime) + float(run["time_interval"][0])))
            print(
                endtime_file_name,
                "set =",
                str(UTCDateTime(startTime) + float(run["time_interval"][0])),
            )

        # Add startTime, startTime + time_interval to gap file.    
        if int(run["gap"][0]) == 1:
            with open(os.path.join(runPath, gap_file_name), "a") as output:
                output.write(
                    "%s %s %s\n"
                    % (
                        "2 - Request incomplete after 5 status requests. Skipping this time interval:",
                        str(UTCDateTime(startTime)),
                        str(
                            UTCDateTime(startTime) + float(run["time_interval"][0])
                        ),
                    )
                )

        sys.exit()

    # Request was a success, we now read the netCDF file if it is complete
    print("Reading netCDF file.")  

    complete = []
    dataTag = status.json()
    for key in dataTag.keys():
        fileTag = key.encode("ascii", "ignore")

        if isinstance(dataTag[key], str):
            #    complete = dataTag[key].encode('ascii', 'ignore')
            complete = dataTag[key]  # Fixed so output would be a string, not bytes. MT

    if complete == "complete":
        
        # If tidal seafloor pressure
        if 'prest' in run_name:
            ncmlUrl = "deployment%04i_%s%s%s%s%s" % (
                deploymentId,
                reference_name_dash,
                "-streamed-",
                run_name,
                "_real_time",
                ".ncml",
            )
        # If BOTPT tilt or pressure
        elif 'lily' in run_name or 'nano' in run_name:
            ncmlUrl = "deployment%04i_%s%s%s%s%s" % (
                deploymentId,
                reference_name_dash,
                "-streamed-botpt_",
                run_name,
                "_sample",
                ".ncml",
            )
        
        # Build .ncml url
        ncmlUrl = "/".join([responseUrl, ncmlUrl])
        print(ncmlUrl)

        # Read the ncml file to get the netCDF file location
        ncml = urllib.request.urlopen(ncmlUrl)
        tree = ET.ElementTree(file=ncml)
        ncml.close()
        root = tree.getroot()
        netcdfUrl = None

        for child in root:

            if "aggregation" in child.tag:

                for element in child:
                    netCDF = element.get("location").strip()
                    print(netCDF)
                    # Build netCDF url
                    netcdfUrl = "/".join([responseUrl, netCDF])

        # To read the netCDF file from URL without downloading it, we have to access the OPENDAP server
        # here I am converting the HTTPServer URL to OPENDAP URL. This is NOT the proper way of doing it
        # we should find a way to get the OPENDAP URL directly from the service
        # I updated the HTTPSever_server and OPENDAP_server urls in the run param file , MT
        netcdfUrl = netcdfUrl.replace(
            run["HTTPServer_server"][0], run["OPENDAP_server"][0]
        )  
        
        # Get data from netCDF file
        # fh = Dataset(netcdfUrl)
        # Code was failing silently, added the try/except block to try and catch silent failure, MT
        try:
            fh = Dataset(netcdfUrl) 
            #netCDF4.Dataset (and Variable[:]) returns time as raw numeric values (a NumPy masked array) stored in the file—typically “X units since Y-epoch” (e.g., seconds since 1900-01-01), with masking based on _FillValue.
            #xarray (by default) decodes CF time variables into actual numpy.datetime64[ns] timestamps using the variable’s units and calendar attributes.
            t = fh.variables[str(run["datatime"][0])][:] # datatime = time; netCDF4.Dataset returns time as raw numeric values (numpy masked array). These are converted to UTCDateTimes at line 496.
            print('Number of data points in NetCDF file:', len(t))
            print("Successfully opened NetCDF file.")
        except Exception as e:
            print("Failed to open NetCDF file:", netcdfUrl)
            print("Error:", str(e))
            sys.exit()

        # for i in range(len(t)):
        #     print(t[i], utcdata1900(float(t[i])) )
        #     quit()

        # Manoch 2017-09-19: The following logic to find the nominal sampling is weak. In a particular day we started having gaps between the selected samples and sampling was coming out wrong and the script was trying to split data to individual samples the following logic appears to be more stable, unless there are too few samples.
        
        # If there are data_sp (currently 5) or more samples in the netCDF file, find the sampling interval.
        if len(t) >= int(run["data_sp"][0]):

            sp = round(np.median(np.diff(t)), 2) # Fix mentioned above. This value comes out to 1.0 when I checked 2 24-hour datasets. MT
            # sp=round(float(t[2])-float(t[1]),2) # Old logic
            print('Sampling interval:', sp)

        # If there are fewer than data_sp (currently 5) samples in the netCDF file, then do not create miniSEED file. 
        elif len(t) < int(run["data_sp"][0]):
            
            print(
                str(UTCDateTime(startTime))
                + "  "
                + str(UTCDateTime(startTime) + float(run["time_interval"][0]))
                + ": ",
                "3 - DATA POINT is less than " + run["data_sp"][0] + "! SKIP IT!",
            )
            
            # Write startTime + time_interval to endtime_ file    
            with open(os.path.join(runPath, endtime_file_name), "w") as output:
                output.write(
                    str(UTCDateTime(startTime) + float(run["time_interval"][0]))
                )
                
            print(
                endtime_file_name,
                "set =",
                str(UTCDateTime(startTime) + float(run["time_interval"][0])),
            )

            # Add startTime, startTime + time_interval to gap file
            if int(run["gap"][0]) == 1:
                with open(os.path.join(runPath, gap_file_name), "a") as output:
                    output.write(
                        "%s %s %s\n"
                        % (
                            "3 - Fewer than 5 samples in data received. Skipping this time interval:",
                            str(UTCDateTime(startTime)),
                            str(
                                UTCDateTime(startTime) + float(run["time_interval"][0])
                            ),
                        )
                    )

            sys.exit()

        sp_rate = 1.0 / sp
        statment = []
        utc_t = []
        new_t = []
        requested_start = UTCDateTime(startTime)
        requested_end = UTCDateTime(endTime)

        # converting sample times to UTC ,mb
        for i in range(len(t)):
            utc_t.append((UTCDateTime(str(utcdata1900(float(t[i]))))))
        data_start = utc_t[0]
        data_end = utc_t[-1]

        new_utc_t = []
        start = 0

        # Checking to see if we received complete interval. New_t holds time (in seconds) of data received ,mb
        if requested_start != data_start or requested_end != data_end:

            start = np.searchsorted(utc_t, requested_start, "left")
            end = np.searchsorted(utc_t, requested_end, "right")
            new_t = t[start:end]

        else:

            new_t = t

        # There is a minimum number of samples we accept (data_sp) ,mb
        # If number of samples is less than data_sp (currently 5), then do not create miniSEED file. MT
        if len(new_t) < int(run["data_sp"][0]):

            print(
                str(UTCDateTime(str(utcdata1900(float(new_t[0])))))
                + "  "
                + str(UTCDateTime(str(utcdata1900(float(new_t[-1])))))
                + ": ",
                "4 - DATA POINT is less than " + run["data_sp"][0] + "! SKIP IT!",
            )

            # Write the last element of new_t to endtime_ file.
            with open(os.path.join(runPath, endtime_file_name), "w") as output:
                output.write(str(UTCDateTime(str(utcdata1900(float(new_t[-1]))))))
                print(
                    endtime_file_name,
                    "set =",
                    str(UTCDateTime(str(utcdata1900(float(new_t[-1]))))),
                )
                
            # Add first and last element of new_t to gap file. 
            if int(run["gap"][0]) == 1:
                with open(os.path.join(runPath, gap_file_name), "a") as output:
                    output.write(
                        "%s %s %s\n"
                        % (
                            "4 - Fewer than 5 samples in data received. Skipping this time interval:",
                            str(UTCDateTime(str(utcdata1900(float(new_t[0]))))),
                            str(UTCDateTime(str(utcdata1900(float(new_t[-1]))))),
                        )
                    )

            sys.exit()

        # Check for irregular sampling intervals, MT
        # Go through the times and check ,mb
        new_utc_t = []
        statement = []

        # Precompute differences and rounding constant
        dt = np.diff(new_t)                # length N-1
        rd = int(run["round_digit"][0])
        sp_r = round(float(sp), rd)        # ensure same rounding on reference spacing

        for p in range(len(new_t)):
            # Convert p-th sample to ObsPy UTC string
            dt1900 = utcdata1900(new_t[p])
            new_utc_t.append(str(UTCDateTime(dt1900)))

            # Gap detection
            if p == 0:
                statement.append(False)
            else:
                gap = round(float(dt[p - 1]), rd) != sp_r
                statement.append(gap)
                if statement[p] == True:
                    print('Gap:', round(float(dt[p - 1]), rd), 's')

        # Indices where we split (before each True index)
        split_idx = np.where(statement)[0]
        print(">>> gap indices:", split_idx)

        # Split into contiguous segments
        new_utc_arr = np.array(new_utc_t, dtype=object)
        data_split = np.split(new_utc_arr, split_idx)

        if (
            str(file_end_time)[11:27] != "23:59:59.999999Z"
            and file_end_time + sp != UTCDateTime(data_split[0][0]) # Check that the expected time stamp of the first sample is not the same as the actual time stamp of the first element of the data_split. MT
            and UTCDateTime(data_split[0][0]) - (file_end_time + sp) < sp # Check if interval is smaller than the sample interval(1.0 s)
        ):

            # Gap is less than sp
            print(
                # str(file_end_time + sp) + "  " + str(file_end_time + sp) + ": ", "GAP"
                "5* - Time stamp of first sample < sp from time stamp of last sample of previous data request. Dropping first sample." + str(file_end_time + sp) + "  " + str(data_split[0][0])  # This is what CoPilot says the code is doing. MT
            )
            
            # Add file_end_time + sp, file_end_time + sp to gap file
            if int(run["gap"][0]) == 1:

                with open(os.path.join(runPath, gap_file_name), "a") as output:
                    # output.write(
                    #     "%s %s %s\n" % ("5 - Gap between first sample and beginning of data split < sp:", str(file_end_time + sp), str(file_end_time + sp))
                    # )
                    output.write(
                        "%s %s %s\n" % ("5* - Time stamp of first sample < sp from time stamp of last sample of previous data request. Dropping first sample.", str(file_end_time + sp), str(data_split[0][0])) # This is what CoPilot says the code is doing. MT
                    )
        elif (
            str(file_end_time)[11:27] != "23:59:59.999999Z"
            and file_end_time + sp != UTCDateTime(data_split[0][0]) # Check that the expected time stamp of the first sample is not the same as the actual time stamp of the first element of the data_split. MT
            and UTCDateTime(data_split[0][0]) - (file_end_time + sp) >= sp # Check if interval is larger than the sample interval (1.0 s)
        ):

            # Gap is greater than sp
            print(
                "6* - Gap between first sample and beginning of data split > sp. Splitting data set at: "
                + str(file_end_time + sp)
                + "  "
                + str(UTCDateTime(data_split[0][0]))
            )

            # Add file_end_time + sp, data_split[0][0] - sp
            if int(run["gap"][0]) == 1:

                with open(os.path.join(runPath, gap_file_name), "a") as output:
                    # output.write(
                    #     "%s %s %s\n"
                    #     % (
                    #         "6* - Gap between first sample and beginning of data split > sp: ",
                    #         str(file_end_time + sp),
                    #         str(UTCDateTime(data_split[0][0]) - sp)
                    #     )
                    # )
                    output.write(
                        "%s %s %s\n"
                        % (
                            "6* - Gap between first sample and beginning of data split > sp. Splitting data set at: ",
                            str(file_end_time + sp),
                            str(UTCDateTime(data_split[0][0])) # More accurate? MT
                        )
                    )
                            
        elif (
            str(file_end_time)[11:27] != "23:59:59.999999Z"
            and requested_start != UTCDateTime(data_split[0][0]) # Check that start time is not the same as the actual time stamp of the first element of the data_split. MT
            and UTCDateTime(data_split[0][0]) - requested_start < sp # Check if interval is smaller than the sample interval (1.0 s)
        ):
            # Gap is less than sp 
            # print(startTime + "  " + startTime + ": ", "GAP")
            print("7 - Gap between start time and beginning of first data split < sp. Splitting data set at: " + startTime + "  " + data_split[0][0]) # More accurate? MT

            # Add startTime, startTime to gap file
            if int(run["gap"][0]) == 1:

                with open(os.path.join(runPath, gap_file_name), "a") as output:
                    output.write("%s %s %s\n" % ("7 - Gap between start time and beginning of first data split < sp. Splitting data set at: ", startTime, data_split[0][0]))
                    
        elif (
            str(file_end_time)[11:27] == "23:59:59.999999Z"
            and requested_start != UTCDateTime(data_split[0][0]) # Check that start time is not the same as the actual time stamp of the first element of the data_split. MT
            and UTCDateTime(data_split[0][0]) - requested_start >= sp # Check if interval is larger than the sample interval (1.0 s)
        ):

            # Gap is greater than sp
            # print(
            #     "8 - Gap between start time and an beginning of first data split >= sp: " + startTime + "  " + str(UTCDateTime(data_split[0][0]) - sp)
            # )
            print(
                "8* - Gap between start time and beginning of first data split >= sp. Splitting data set at: " + startTime + "  " + str(UTCDateTime(data_split[0][0]))
            ) # More accurate? MT
            
            # Add startTime, data_split[0][0] - sp to gap file
            if int(run["gap"][0]) == 1:

                with open(os.path.join(runPath, gap_file_name), "a") as output:
                    output.write(
                        "%s %s %s\n" % ("8* - Gap between start time and beginning of first data split >= sp. Splitting data set at:", startTime, str(UTCDateTime(data_split[0][0])))
                    )

        # If there is only one data split
        if len(data_split) == 1:

            # Write the end time of the first data segment to the endtime_ file
            with open(os.path.join(runPath, endtime_file_name), "w") as output:
                output.write(data_split[0][-1])

                print(endtime_file_name, "set =", data_split[0][-1])

        # If there are multiple data splits
        else:

            for j in range(len(data_split)):

                # If this is the final data split
                if j == len(data_split) - 1:

                    ti = data_split[j]
                    start_time = ti[0]
                    end_time = ti[-1]
                    
                    # If the length of the data split segment is greater than data_sp (currently 5)
                    if len(ti) >= int(run["data_sp"][0]):
                        
                        # Write the end time of the data split segment to endtime_ file
                        with open(
                            os.path.join(runPath, endtime_file_name), "w"
                        ) as output:
                            output.write(end_time)
                            print(endtime_file_name, "set =", end_time)

                    else:
                        
                        # Length of segment is less than data_sp. Do not create miniSEED file.
                        print(
                            start_time + "  " + end_time + ": ",
                            "9 - GAP DATA POINT is less than "
                            + run["data_sp"][0]
                            + "! SKIP IT! (Last data split segment)",
                        )

                        # Add start_time (of data split segment), end_time (of data split segment) to gap file. 
                        if int(run["gap"][0]) == 1:

                            with open(
                                os.path.join(runPath, gap_file_name), "a"
                            ) as output:
                                output.write("%s %s %s\n" % ("9 - Data segment is less than 5 samples (last data split segment). Skipping this data segment:", start_time, end_time))

                else:

                    ti = data_split[j]
                    start_time = ti[0]
                    end_time = ti[-1]
                    ti2 = data_split[j + 1]
                    start_time2 = ti2[0]

                    # If the length of the data split segment is greater than data_sp (currently 5)
                    if len(ti) >= int(run["data_sp"][0]):
                        
                        # Write end_time (of data split segment) to endtime_ file
                        with open(
                            os.path.join(runPath, endtime_file_name), "w"
                        ) as output:

                            output.write(end_time)
                            print(endtime_file_name, "set =", end_time)

                        if int(run["gap"][0]) == 1:

                            with open(
                                os.path.join(runPath, gap_file_name), "a"
                            ) as output:

                                # output.write(
                                #     "%s %s %s\n"
                                #     % (
                                #         "10* - Data segment is less than 5 samples (multiple data splits): ",
                                #         str(UTCDateTime(end_time) + sp),
                                #         str(UTCDateTime(start_time2) - sp),
                                #     )
                                # )
                                # print(
                                #     "10* - Data segment is less than 5 samples (multiple data splits): "
                                #     + str(UTCDateTime(end_time) + sp)
                                #     + "  "
                                #     + str(UTCDateTime(start_time2) - sp)
                                # )

                                output.write(
                                    "%s %s %s\n"
                                    % (
                                        "10* - Gap between end of one segment and start of next segment: ",
                                        str(UTCDateTime(end_time)),
                                        str(UTCDateTime(start_time2)),
                                    ) # More accurate. MT
                                )
                                print(
                                    "10* - Gap between end of one segment and start of next segment: "
                                    + str(UTCDateTime(end_time))
                                    + "  "
                                    + str(UTCDateTime(start_time2)) # More accurate. MT
                                )
                    else:
                        
                        # Length of segment is less than data_sp. Do not create miniSEED file.
                        print(
                            start_time + "  " + end_time + ": ",
                            "GAP DATA POINT is less than "
                            + run["data_sp"][0]
                            + "! SKIP IT!",
                        )

                        # Add end_time (ti[-1]) + sp, start_time2 (ti2[0]) - sp 
                        if int(run["gap"][0]) == 1:

                            # with open(
                            #     os.path.join(runPath, gap_file_name), "a"
                            # ) as output:
                            #     output.write("%s %s %s\n" % ("11* - Data segment is less than 5 samples (first data split segment): ", start_time, end_time))

                            # with open(
                            #     os.path.join(runPath, gap_file_name), "a"
                            # ) as output:
                            #     output.write(
                            #         "%s %s %s\n"
                            #         % (
                            #             "11* - Data segment is less than 5 samples (other data split segments): ",
                            #             str(UTCDateTime(end_time) + sp),
                            #             str(UTCDateTime(start_time2) - sp),
                            #         )
                            #     )
                                
                            #     print(
                            #         "11* - Data segment is less than 5 samples (multiple data splits): "
                            #         + str(UTCDateTime(end_time) + sp)
                            #         + "  "
                            #         + str(UTCDateTime(start_time2) - sp)
                            #     )

                            with open(
                                os.path.join(runPath, gap_file_name), "a"
                            ) as output:
                                output.write("%s %s %s\n" % ("11* - Data segment is less than 5 samples. Skipping this segment: ", start_time, end_time)) # TODO: Why is logged here? MT

                            with open(
                                os.path.join(runPath, gap_file_name), "a"
                            ) as output:
                                output.write(
                                    "%s %s %s\n"
                                    % (
                                        "11* - Data segment is less than 5 samples. Skipping this segment: ",
                                        str(UTCDateTime(end_time)),
                                        str(UTCDateTime(start_time2)),
                                    )
                                ) # More accurate? MT
                                
                                print(
                                    "11* - Data segment is less than 5 samples. Skipping this segment: "
                                    + str(UTCDateTime(end_time))
                                    + "  "
                                    + str(UTCDateTime(start_time2))
                                ) # More accurate? MT                

        channel_ls = run["Channel"][0][1:-1].split(",") # Channel list from run param file
        datatype_ls = run["datatype"][0] # Data type list from run param file
        param = readParam(os.path.join(paramPath, reference_name_underscore + ".txt")) # Network/station param file

        for k in range(len(channel_ls)):
            
            # Converts datatype_ls to a dictionary, looks up name based on the channel. E.g., if channel_ls[k] == 'LDO_01', then name == 'absolute_pressure'. TODO: if datatype_ls is already a dict, ast.literal_eval will raise a TypeError.
            name = ast.literal_eval(datatype_ls)[channel_ls[k]] 
            data_point = fh.variables[name][:] * float(run["unit_convert"][0]) # Unit_convert currently set to 1, so this doesn't do anything. Added data unit conversion below. MT
            l = start # Actual start time of data

            for s in range(len(data_split)):

                ti = data_split[s]
                end_time = ti[-1]
                start_time = ti[0]

                if len(ti) >= int(run["data_sp"][0]):

                    data_i_point = data_point[l : l + len(ti)] # Grab the data from the netCDF file within the data_split window
                    data = data_i_point

                    # Masked array writing is not supported for miniSEED. Converting to a normal array, MT
                    if isinstance(data, MaskedArray):
                        data = data.filled(np.nan)

                    # Fill header attributes
                    print("Filling miniSEED header attributes.")  
                    param2 = readParam(
                        os.path.join(
                            paramPath,
                            reference_name_underscore + "_" + channel_ls[k] + ".txt",
                        )
                    )
                    stats = {
                        "network": param["Net"][0],
                        "station": param["Sta"][0],
                        "location": param2["C_loc"][0],
                        "channel": param2["Cha"][0],
                        "npts": len(data),
                        "sampling_rate": sp_rate,
                        "mseed": {"dataquality": run["dataquality"][0]},
                    }

                    # Convert data to desired units
                    data = data / float(param2["R_Value"][0]) # e.g., absolute_pressure from PSI to Pa
                    
                    print('Number of data points in MSEED file:', len(data))
                    
                    # Set current time
                    stats["starttime"] = start_time # start time of the data_split segment
                    st = Stream([Trace(data=data, header=stats)])
                    encoding_name = str(st[0].data.dtype).upper()

                    print("Writing miniSEED file.")
                    st.write(
                        mseedPath
                        + "/"
                        + param["Net"][0]
                        + "."
                        + param["Sta"][0]
                        + "."
                        + param2["C_loc"][0]
                        + "."
                        + param2["Cha"][0]
                        + "."
                        + start_time[0:4]
                        + "."
                        + "{:0>3}".format(
                            str(time.strptime(start_time[0:10], "%Y-%m-%d").tm_yday)
                        )
                        + "."
                        + start_time[11:23].replace(":", ".")
                        + "-"
                        + end_time[0:4]
                        + "."
                        + "{:0>3}".format(
                            str(time.strptime(end_time[0:10], "%Y-%m-%d").tm_yday)
                        )
                        + "."
                        + end_time[11:23].replace(":", ".")
                        + mseed_file_ext,
                        format="MSEED",
                        encoding=encoding_name,
                        reclen=int(run["reclen"][0]),
                    )

                    l = l + len(ti)

                elif len(ti) < int(run["data_sp"][0]):

                    l = l + len(ti)

# If the difference between the startTime and endTime is less than diftime from the run param file, skip the request. 
elif difTime < float(run["diftime"][0]):

    print("SKIP request: difTime LT [" + str(run["diftime"][0]) + "]")

    with open(os.path.join(runPath, endtime_file_name), "w") as output:
        output.write(str(UTCDateTime(endTime)))
        print(endtime_file_name, "set =", str(UTCDateTime(endTime)))
        
    # TODO: Add sys.exit()?

# If the end time of the data request is after the truncTime (now() - trunctime [currently 60 seconds]), skip the data request.
elif UTCDateTime(endTime) > UTCDateTime(truncTime):

    print(
        "SKIP request: end time ["
        + UTCDateTime(endTime).strftime("%Y-%m-%d %H:%M:%S")
        + "] GT truncation time ["
        + UTCDateTime(truncTime).strftime("%Y-%m-%d %H:%M:%S")
        + "] so no data requested"
    )

    sys.exit()

sys.stdout = old_stdout
