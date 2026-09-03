import email
from email.header import decode_header
import imaplib
import os

# Credenciales de la cuenta del BOT LECTOR
EMAIL_BOT = "YOU EMAIL HERE"  # Reemplaza con el correo del bot
PASSWORD_BOT = "YOUR GMAIL APP PASSWORD HERE"  # Reemplaza con la contraseña


DOWNLOAD_FOLDER = "./reportes_descargados"
RENAME_TO = "datos_extraidos.json"


def procesar_reportes():
    print("Conectando al bot lector...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")

    try:
        mail.login(EMAIL_BOT, PASSWORD_BOT)
        mail.select("inbox")

        # Buscar correos no leídos con el asunto clave
        status, messages = mail.search(
            None, '(UNSEEN SUBJECT "REPORTE BIMESTRAL")'
        )
        email_ids = messages[0].split()

        if not email_ids:
            print("No hay nuevos reportes reenviados.")
            return

        print(f"¡Se encontraron {len(email_ids)} nuevo(s) reporte(s)!")
        os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

        for e_id in email_ids:
            status, data = mail.fetch(e_id, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])

            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding if encoding else "utf-8")

            print(f"Procesando: {subject}")

            # Extraer los PDFs adjuntos
            for part in msg.walk():
                if (
                    part.get_content_maintype() == "multipart"
                    or part.get("Content-Disposition") is None
                ):
                    continue

                filename = part.get_filename()
                if filename:
                    filename_decoded, enc = decode_header(filename)[0]
                    if isinstance(filename_decoded, bytes):
                        filename = filename_decoded.decode(
                            enc if enc else "utf-8"
                        )

                    if filename.lower().endswith(".pdf"):
                        filepath = os.path.join(DOWNLOAD_FOLDER, filename)
                        with open(filepath, "wb") as f:
                            f.write(part.get_payload(decode=True))
                        print(f" -> PDF guardado: {filepath}")

            # Marcar como leído
            mail.store(e_id, "+FLAGS", "\\Seen")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        mail.logout()


if __name__ == "__main__":
    procesar_reportes()