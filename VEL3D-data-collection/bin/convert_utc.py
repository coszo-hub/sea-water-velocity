#!/home/coszo/miniconda3/envs/ooi_env/bin/python

def utc(time):

    seconds = time / 1000.0
    import datetime

    baseDate = datetime.datetime(1970, 1, 1)  # January 1st, 1970 at midnight
    delta = datetime.timedelta(seconds=seconds)
    date = baseDate + delta
    return date


def utcdata1900(time):

    seconds = time
    import datetime

    baseDate = datetime.datetime(1900, 1, 1)  # January 1st, 1900 at midnight
    delta = datetime.timedelta(seconds=seconds)
    date = baseDate + delta
    return date
