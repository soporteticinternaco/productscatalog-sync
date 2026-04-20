import os
import ssl
from ftplib import FTP_TLS
from urllib.parse import urlparse
import logging

def download_all_files(ftp, remote_path, local_path):
    os.makedirs(local_path, exist_ok=True)
    ftp.cwd(remote_path)
    file_list = ftp.nlst()

    for filename in file_list:
        
        if filename.lower().endswith('.xlsx'):
            local_filepath = os.path.join(local_path, filename)
            try:
                with open(local_filepath, 'wb') as f:
                    print(f"Downloading {filename} to {local_filepath}...")
                    ftp.retrbinary(f"RETR {filename}", f.write)

                    # Move file on remote server to old2/
                    new_remote_path = f"old2/{filename}"
                    print(f"Moving remote file to {new_remote_path}...")
                    ftp.rename(filename, new_remote_path)

            except Exception as e:
                print(f"Error processing {filename}: {e}")


def main():
    ftp_url = os.getenv('EXCEL_FILES_FTP_URL')
    ftp_user = os.getenv('EXCEL_FILES_FTP_USER')
    ftp_pass = os.getenv('EXCEL_FILES_FTP_PASS')
    remote_path = os.getenv('EXCEL_FILES_FTP_REMOTE_PATH')
    local_path = os.getenv('EXCEL_FILES_FTP_LOCAL_PATH')

    if not ftp_url or not ftp_user or not ftp_pass or not remote_path or not local_path:
        print("Please set FTP_URL, FTP_USER, and FTP_PASS environment variables")
        return

    # Parse FTP URL to get hostname and port
    parsed_url = urlparse(ftp_url)
    hostname = parsed_url.hostname
    port = parsed_url.port or 21

    print("Connecting to {} {} {} {}", ftp_url, hostname, port, ftp_user, ftp_pass)

    # Connect to FTP server with TLS (accept all certs)
    context = ssl._create_unverified_context()
    ftps = FTP_TLS(context=context)
    
    ftps.connect(host=hostname, port=port)
    ftps.auth()
    ftps.login(user=ftp_user, passwd=ftp_pass)
    ftps.prot_p()  # Switch to secure data connection
    print(f"Connected to FTP server: {hostname}")

    # Download all files from remote_path
    download_all_files(ftps, remote_path, local_path)

    ftps.quit()
    print("FTP download completed.")

if __name__ == "__main__":
    main()
