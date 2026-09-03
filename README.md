# Analist_IA

Specific emails are automatically forwarded to a dedicated email address.

`Bot_Procesador.py` checks the inbox every 24 hours and processes only unread messages. It extracts PDF attachments from the emails, converts their contents into JSON, and sends the resulting data to `Analizador_IA`, which generates comprehensive reports on student performance.

## Workflow

```text
Email
  ↓
Bot_Procesador.py
  ↓
PDF → JSON
  ↓
Analizador_IA
  ↓
Student Performance Report
```

## Additional Files

`Bot_Lector.py` is included as a reference example showing how emails can be accessed and read using Python. It is not part of the main processing workflow and does not perform any data processing.

> **Note:** This is a university project developed for academic purposes. It is not intended for professional or production use.

