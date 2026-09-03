import email
from email.header import decode_header
import imaplib
import os
import pdfplumber

# --- CONFIGURACIÓN ---
EMAIL_BOT = "YOUR EMAIL HERE"  # Reemplaza con el correo del bot
PASSWORD_BOT = "YOUR GMAIL APP PASSWORD HERE"  # Reemplaza con la contraseña o token de la cuenta del bot

DOWNLOAD_FOLDER = "./reportes_descargados"
JSON_OUTPUT_FOLDER = "./reportes_json"


def extraer_texto_pdf(pdf_path):
    """Abre el PDF y extrae todo el texto de las páginas."""
    texto_completo = ""
    with pdfplumber.open(pdf_path) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if texto:
                texto_completo += texto + "\n"
    return texto_completo.strip()


def convertir_a_json(nombre_archivo, contenido_texto, remitente, asunto):
    """Estructura el contenido en un archivo JSON independiente por cada documento."""
    os.makedirs(JSON_OUTPUT_FOLDER, exist_ok=True)

    datos = {
        "metadata": {
            "archivo_origen": nombre_archivo,
            "remitente": remitente,
            "asunto": asunto,
        },
        "contenido_raw": contenido_texto,
    }

    # Evita sobreescrituras usando el nombre base del PDF adjunto
    base_nombre = os.path.splitext(nombre_archivo)[0]
    nombre_json = f"{base_nombre}_datos.json"
    path_json = os.path.join(JSON_OUTPUT_FOLDER, nombre_json)

    with open(path_json, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=4)

    print(f" -> ¡JSON generado con éxito!: {path_json}")


def procesar_reportes():
    print("Conectando al bot lector en ...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")

    try:
        mail.login(EMAIL_BOT, PASSWORD_BOT)
        mail.select("inbox")

        # Buscar correos no leídos
        status, messages = mail.search(
            None, '(UNSEEN SUBJECT "REPORTE BIMESTRAL")'
        )
        email_ids = messages[0].split()

        if not email_ids:
            print("No hay nuevos reportes por procesar.")
            return

        print(f"Se encontraron {len(email_ids)} nuevo(s) correo(s).")
        os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

        for e_id in email_ids:
            status, data = mail.fetch(e_id, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])

            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding if encoding else "utf-8")

            remitente = msg.get("From")
            print(f"\nProcesando mensaje de: {remitente}")

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
                        # 1. Guardar PDF
                        pdf_path = os.path.join(DOWNLOAD_FOLDER, filename)
                        with open(pdf_path, "wb") as f:
                            f.write(part.get_payload(decode=True))
                        print(f" -> PDF guardado: {pdf_path}")

                        # 2. Extraer texto del PDF
                        texto_extraido = extraer_texto_pdf(pdf_path)

                        # 3. Guardar como JSON individual
                        convertir_a_json(
                            filename, texto_extraido, remitente, subject
                        )

            # Marcar correo como leído
            mail.store(e_id, "+FLAGS", "\\Seen")

    except Exception as e:
        print(f"Ocurrió un error: {e}")
    finally:
        mail.logout()


if __name__ == "__main__":
    import json
    procesar_reportes()