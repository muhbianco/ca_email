import pytz
import re
import logging
import os
import requests
import tempfile
import time
import asyncio
import aiohttp
from imap_tools import MailBox, AND
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pprint import pprint
from db import get_session

load_dotenv()

EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PATH = os.getenv("EMAIL_PATH")
NNHOOK_URL = os.getenv("N8N_WEBHOOK_URL")
N8NAPI_KEY = os.getenv("N8N_API_KEY")
CCAGIL_URL = os.getenv("CONTAAGIL_URL")

CONTAAGIL_LOGIN = os.getenv("CONTAAGIL_LOGIN")
CONTAAGIL_PASSW = os.getenv("CONTAAGIL_PASSW")

local_time_fuse = pytz.timezone("America/Sao_Paulo")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class CA_EMAIL:
    def __init__(self):
        pass

    async def log_response_error(self):
        error_message = await self.response.text()
        logging.error(f"Status Code: {self.response.status}")
        logging.error(f"Error message: {error_message}")

    async def send_message_to_n8n(self):
        headers = {
            "Authorization": F"Bearer {N8NAPI_KEY}",
        }
        payload = {
            "cod_lead": self.cod_lead,
            "message": self.response_message,
            "from_email": self.response_from_email,
            "to_email": self.response_to_email,
            "subject": self.response_subject,
            "incoming": True,
            "channel": "email",
        }
        async with aiohttp.ClientSession() as session:
            self.response = await session.post(NNHOOK_URL, headers=headers, json=payload)

        if self.response.status != 200:
            return False

        message_response = await self.response.json()
        self.message_id = message_response.get("messageId")
        logging.info(f"message_id: {self.message_id}")
        return True

    async def contaagil_login(self):
        headers = {
            "Session-Token": "",
        }
        payload = {
            "email": CONTAAGIL_LOGIN,
            "password": CONTAAGIL_PASSW,
        }
        async with aiohttp.ClientSession() as session:
            self.response = await session.post(f"{CCAGIL_URL}/Authenticator/getToken", headers=headers, json=payload)

    async def send_files_to_db(self):
        await self.contaagil_login()
        if self.response.status != 200:
            logging.error(f"Falha ao obter session_token ContaÁgil")
            return False

        login_response = await self.response.json()
        session_token = login_response.get("session_token_admin")
        headers = {
            "Session-Token": session_token,
        }
        payload = {
            "CodLead": self.cod_lead,
            "saveFile": True,
            "tipo": "ANEXO_LEAD",
            "CodMensagem": self.message_id
        }

        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            for key, value in payload.items():
                form.add_field(key, str(value))
            for field_name, (filename, fileobj, content_type) in self.files:
                form.add_field(
                    field_name,
                    fileobj,
                    filename=filename,
                    content_type=content_type,
                )
            self.response = await session.post(f"{CCAGIL_URL}/Upload/index", headers=headers, data=form)

        if self.response.status != 200:
            return False

        return True
        

    def get_msg_attachments(self, attachments):
        attachs = []
        if attachments:
            for att in attachments:
                attachs.append(att)
        return attachs

    def save_and_create_files(self, attachs):
        files = []
        with tempfile.TemporaryDirectory() as temp_dir:
            for att in attachs:
                file_path = os.path.join(temp_dir, att.filename)
                with open(file_path, 'wb') as f:
                    f.write(att.payload)
                files.append(('files[]', (att.filename, open(file_path, 'rb'), att.content_type)))
        return files

    def message_extract(self, msg):
        message = msg[:msg.find(".brevosend.com>")]
        pattern = r"On \w{3}, \w+ \d{1,2}, \d{4} at \d{1,2}:\d{2}.*"
        message = re.sub(pattern, '', message)
        return message

    def internal_code_extract(self, html):
        match = '<p class="CONTAAGIL-UUID" style="color:white;display:none">'
        start_point = html.find(match)+len(match)
        code = html[start_point:]
        end_point = code.find("</p>")
        code = code[:end_point]
        return code

    def remove_html_tags(self, text):
        soup = BeautifulSoup(text, "html.parser")
        return soup.get_text()

    async def cod_lead_from_email(self, from_):
        async with get_session() as db:
            sql = "SELECT CodLead FROM Leads WHERE Email = %s"
            response = await db.fetchone(sql, (from_, ))
            if not response:
                return None
        return response["CodLead"]

    async def process_mailbox(self, msg):
        date = re.sub(r"\s*\(.*?\)\s*", "", msg.date_str)
        date = datetime.strptime(date, "%a, %d %b %Y %H:%M:%S %z")
        date = date.astimezone(local_time_fuse)
        date = date.strftime("%d-%m-%Y %H:%M:%S")

        logging.info(f"from: {msg.from_}")

        self.cod_lead = await self.cod_lead_from_email(msg.from_)
        logging.info(f"codlead: {self.cod_lead}")
        self.response_from_email = msg.from_
        self.response_to_email = msg.to[0]
        self.response_subject = msg.subject
        self.response_message = self.message_extract(msg.text)

        if not self.cod_lead:
            return

        self.files = []
        if msg.attachments:
            attachs = self.get_msg_attachments(msg.attachments)
            if attachs:
                self.files = self.save_and_create_files(attachs)

        if not await self.send_message_to_n8n():
            logging.error(f"Erro ao enviar mensagem para o N8N")
            await self.log_response_error()
            return

        if not await self.send_files_to_db():
            logging.error(f"Erro ao enviar arquivos para o EP /Upload/index")
            await self.log_response_error()
            return


    async def main(self):
        max_retries = 50
        delay = 5

        attempt = 0
        while attempt < max_retries:
            try:
                with MailBox(EMAIL_HOST).login(EMAIL_USER, EMAIL_PASS, EMAIL_PATH) as mb:
                    logging.info("Conexão IMAP estabelecida com sucesso.")
                    attempt = 0

                    while True:
                        try:
                            for msg in mb.fetch(AND(seen=False), mark_seen=True):
                                await self.process_mailbox(msg)

                            mb.client.noop()
                            await asyncio.sleep(3)

                        except Exception as e:
                            logging.error(f"Ocorreu um erro: {e}")
                            break

            except Exception as e:
                attempt += 1
                logging.warning(f"Attempt {attempt}/{max_retries} Error: {e}")

                if attempt < max_retries:
                    logging.info(f"Trying in {delay} seconds...")
                    await asyncio.sleep(delay)
                else:
                    logging.error("Max tries. Out...")

if __name__ == "__main__":
    version = "v1.6"
    logging.info(f"[CA EMAIL Version {version} starded.]")
    asyncio.run(CA_EMAIL().main())
