import pytz
import re
import logging
import os
import requests
import tempfile
import time
from imap_tools import MailBox, AND
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PATH = os.getenv("EMAIL_PATH")
NNHOOK_URL = os.getenv("N8N_WEBHOOK_URL")
N8NAPI_KEY = os.getenv("N8N_API_KEY")

local_time_fuse = pytz.timezone("America/Sao_Paulo")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_msg_attachments(attachments):
    attachs = []
    if attachments:
        for att in attachments:
            attachs.append(att)
    return attachs

def save_and_create_files(attachs):
    files = []
    with tempfile.TemporaryDirectory() as temp_dir:
        for att in attachs:
            file_path = os.path.join(temp_dir, att.filename)
            with open(file_path, 'wb') as f:
                f.write(att.payload)
            files.append(('files', (att.filename, open(file_path, 'rb'), att.content_type)))
    return files

def message_extract(msg):
    message = msg[:msg.find(".brevosend.com>")]
    pattern = r"On \w{3}, \w+ \d{1,2}, \d{4} at \d{1,2}:\d{2}.*"
    message = re.sub(pattern, '', message)
    return message

def internal_code_extract(html):
    match = '<p style="color:white;display:none">'
    start_point = html.find(match)+len(match)
    code = html[start_point:]
    end_point = code.find("</p>")
    code = code[:end_point]
    return code

def remove_html_tags(text):
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text()

def process_mailbox(msg):
    date = re.sub(r"\s*\(.*?\)\s*", "", msg.date_str)
    date = datetime.strptime(date, "%a, %d %b %Y %H:%M:%S %z")
    date = date.astimezone(local_time_fuse)
    date = date.strftime("%d-%m-%Y %H:%M:%S")

    response_from = msg.from_
    response_subject = msg.subject
    response_message = message_extract(msg.text)
    internal_code = internal_code_extract(msg.html)

    # logging.info(f"UID: {msg.uid}")
    # logging.info(f"SUBJECT: {msg.subject}")
    # logging.info(f"FROM: {msg.from_}")
    # logging.info(f"DATE: {date}")
    # logging.info(f"BODY: {msg.text}")
    # logging.info(f"htmlBODY: {msg.html}")
    # logging.info(f"Código Interno: {internal_code}")
    # logging.info(f"RESPONSE MESSAGE: {response_message}")

    files = []
    if msg.attachments:
        attachs = get_msg_attachments(msg.attachments)
        if attachs:
            logging.info(f"ATTACH: {', '.join([attach.filename for attach in attachs])}")
            logging.info("===================================\n\n")
            files = save_and_create_files(attachs)

    if internal_code:
        headers = {
            "Authorization": F"Bearer {N8NAPI_KEY}",
        }
        payload = {
            "internal_code": internal_code,
            "message": response_message,
            "from": response_from,
            "subject": response_subject,
        }
        response = requests.post(NNHOOK_URL, headers=headers, data=payload, files=files)
        if response.status_code != 200:
            logging.error(response.text)

def main():
    max_retries = 50
    delay = 5

    attempt = 0
    while attempt < max_retries:
        try:
            with MailBox(EMAIL_HOST).login(EMAIL_USER, EMAIL_PASS, EMAIL_PATH) as mb:
                while True:
                    try:
                        for msg in mb.fetch(AND(seen=False), mark_seen=True):
                            process_mailbox(msg)
                    except Exception as e:
                        logging.error(f"Ocorreu um erro: {e}")
        except Exception as e:
            attempt += 1
            logging.warning(f"Attempt {attempt}/{max_retries} Error: {e}")
            if attempt < max_retries:
                logging.info(f"Trying in {delay} seconds...")
                time.sleep(delay)
            else:
                logging.error("Max tries. Out...")


if __name__ == "__main__":
    version = "v1.0"
    logging.info(f"[CA EMAIL Version {version} starded.]")
    main()
