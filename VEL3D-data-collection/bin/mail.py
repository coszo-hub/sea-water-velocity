#!/home/coszo/miniconda3/envs/ooi_env/bin/python

# This function sends a message to the operator 
# Forrest - added hostname and path from which message sent.
# Forrest - added body.
def sendmail(subject,body=""):

    import os
    import subprocess
    import socket

    # This idiom gets the parent dir of this module.
    root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../'))

    from_addr  = 'coszo@uw.edu'
    recipients = 'usherm42@uw.edu'

    subject   = str(subject)
    body      = str(body)

    hostname = socket.gethostname().upper()
    body += '\n\nMessage sent from ' + hostname + ':' + root_path

    def send_message(from_addr, recipients, subject, body):

        try:
            process = subprocess.Popen(['/usr/bin/mailx', '-s', subject, '-r', from_addr, recipients], stdin=subprocess.PIPE)
            # process = subprocess.Popen(['/usr/bin/mailx', '-s', subject, recipients], stdin=subprocess.PIPE) # BSD mailx does not support "-r" flag  that specifies the sender address, MT
        except Exception as error:
            print(error)

        # process.communicate(body)
        process.communicate(body.encode()) # MT

    send_message(from_addr, recipients, subject, body)

