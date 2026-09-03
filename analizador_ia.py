import glob
import json
import time
os = __import__('os')
import re
from collections import Counter
from xml.sax.saxutils import escape
from google import genai
from google.genai import types
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, HRFlowable,
    Table, TableStyle, Spacer
)

UMBRAL_REPROBACION = 70
TAMANO_LOTE = 10  # máximo de alumnos por llamada a la IA
MAX_INTENTOS = 3  # reintentos máximos por materia si ocurre un fallo


def limpiar_nombre_materia(nombre_archivo):
    """Extrae y formatea un nombre legible de la materia a partir del nombre del archivo JSON."""
    base = os.path.basename(nombre_archivo)
    limpio = base.replace('_datos.json', '').replace('.json', '')
    limpio = re.sub(r'^\d+_', '', limpio)
    return limpio.replace('_', ' ').title()


def dividir_por_alumno(texto_completo):
    """
    Divide el texto crudo en un bloque por cada alumno, usando el número
    de control (8 dígitos) como marcador de inicio de cada registro.
    """
    patron = re.compile(r'\d{8}')
    coincidencias = list(patron.finditer(texto_completo))

    if not coincidencias:
        return [texto_completo] if texto_completo.strip() else []

    bloques = []
    for i, m in enumerate(coincidencias):
        inicio = m.start()
        fin = coincidencias[i + 1].start() if i + 1 < len(coincidencias) else len(texto_completo)
        bloque = texto_completo[inicio:fin].strip()
        if bloque:
            bloques.append(bloque)

    return bloques


def agrupar_en_lotes(bloques, tamano_lote=TAMANO_LOTE):
    """Agrupa los bloques de alumnos individuales en lotes de máximo `tamano_lote`."""
    return [bloques[i:i + tamano_lote] for i in range(0, len(bloques), tamano_lote)]


def _extraer_lote(client, bloques_lote, nombre_materia_sugerido):
    """
    Le pide a Gemini que extraiga los alumnos nombrando correctamente cada criterio
    (ej. Tareas, Exámenes, Asistencia) en lugar de Criterio 1, 2, 3...
    """
    texto_lote = "\n\n---\n\n".join(bloques_lote)
    n_esperados = len(bloques_lote)

    prompt = f"""
    Eres un extractor de datos académicos experto. 
    Contexto de la materia: "{nombre_materia_sugerido}".
    Vas a recibir {n_esperados} bloque(s) de texto, separados por "---". Cada bloque corresponde a UN SOLO alumno.

    Responde ÚNICAMENTE con un objeto JSON válido (sin texto adicional, sin
    explicaciones, sin markdown de bloques de código), con EXACTAMENTE este esquema:

    {{
      "materia": "{nombre_materia_sugerido}",
      "alumnos": [
        {{
          "numero_control": "<numero de control del alumno>",
          "nombre": "<nombre completo del alumno>",
          "criterios": [
            {{"nombre": "<nombre descriptivo real del criterio, ej. Tareas, Examen, Participacion, Calificacion Final>", "calificacion": 0.0}}
          ]
        }}
      ]
    }}

    REGLAS CRÍTICAS:
    - Recibiste {n_esperados} bloque(s), así que "alumnos" DEBE tener
      EXACTAMENTE {n_esperados} elemento(s), uno por cada bloque, en el
      mismo orden en que aparecen.
    - NO uses nombres genéricos como "Criterio 1", "Criterio 2". Identifica o deduce 
      el nombre real según el contexto de evaluación (por ejemplo: Tareas, Examen, Asistencia, etc.).
    - El último criterio suele ser la calificación final o general, así que asígnale un nombre claro como "Calificación Final".
    - "calificacion" debe ser un número (flotante o entero).

    Bloques a interpretar:
    {texto_lote}
    """

    respuesta = client.models.generate_content(
        model='gemini-3.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=4000,
            temperature=0,
            response_mime_type="application/json",
        ),
    )

    texto = respuesta.text.strip()
    texto = re.sub(r'^```json\s*|^```\s*|\s*```$', '', texto, flags=re.MULTILINE).strip()
    
    if not texto:
        raise ValueError("La respuesta de la IA llegó vacía.")
        
    return json.loads(texto)


def analizar_datos_con_ia(datos_json, nombre_archivo):
    """
    Procesa el JSON analizando los bloques por alumno en lotes pequeños.
    """
    api_key = "YOUR GEMINI API KEY HERE"  # Reemplaza con tu API Key de Gemini
    client = genai.Client(api_key=api_key)

    nombre_materia_sugerido = limpiar_nombre_materia(nombre_archivo)
    texto_completo = datos_json.get("contenido_raw", "")
    bloques = dividir_por_alumno(texto_completo)
    print(f"  Se detectaron {len(bloques)} posible(s) registro(s) de alumno en el texto.")

    lotes = agrupar_en_lotes(bloques)
    print(f"  Se procesarán en {len(lotes)} lote(s) de hasta {TAMANO_LOTE} alumnos cada uno.")

    materia_final = nombre_materia_sugerido
    alumnos_totales = []
    controles_vistos = set()

    for i, lote in enumerate(lotes, start=1):
        print(f"  Procesando lote {i}/{len(lotes)} ({len(lote)} registro(s))...")
        
        # Reintentos por lote individual si la llamada a la API falla
        exito_lote = False
        intentos_lote = 0
        while not exito_lote and intentos_lote < MAX_INTENTOS:
            try:
                intentos_lote += 1
                resultado_lote = _extraer_lote(client, lote, nombre_materia_sugerido)
                exito_lote = True
            except Exception as e:
                print(f"    ⚠️ Fallo en lote {i} (Intento {intentos_lote}/{MAX_INTENTOS}): {e}")
                if intentos_lote >= MAX_INTENTOS:
                    raise RuntimeError(f"El lote {i} falló definitivamente tras {MAX_INTENTOS} intentos.")
                time.sleep(2) # Espera antes de reintentar

        if resultado_lote.get("materia") and resultado_lote["materia"] != "No especificada":
            materia_final = resultado_lote["materia"]

        extraidos_en_lote = resultado_lote.get("alumnos", [])
        for alumno in extraidos_en_lote:
            control = alumno.get("numero_control", "N/D")
            if control != "N/D" and control in controles_vistos:
                continue
            controles_vistos.add(control)
            alumnos_totales.append(alumno)

    print(f"  Total de alumnos procesados: {len(alumnos_totales)}")

    return {
        "materia": materia_final,
        "alumnos": alumnos_totales,
    }


def _a_numero(valor):
    try:
        return float(str(valor).replace('%', '').strip())
    except (TypeError, ValueError):
        return None


def calcular_estatus(alumno):
    criterios = alumno.get("criterios", [])
    if not criterios:
        return "Sin datos", []

    criterios_reprobados = []
    for criterio in criterios:
        nota = _a_numero(criterio.get("calificacion"))
        if nota is not None and nota < UMBRAL_REPROBACION:
            criterios_reprobados.append(criterio.get("nombre", "criterio sin nombre"))

    if criterios_reprobados:
        return "Reprobado", criterios_reprobados
    return "Aprobado", []


def _texto_seguro(valor):
    return escape(str(valor))


def generar_pdf_analisis(datos_analisis, nombre_pdf_salida):
    os.makedirs("reportes_pdf", exist_ok=True)
    ruta_pdf = os.path.join("reportes_pdf", nombre_pdf_salida)

    doc = SimpleDocTemplate(
        ruta_pdf, pagesize=A4,
        rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30
    )
    story = []
    styles = getSampleStyleSheet()

    titulo_style = ParagraphStyle(
        'Titulo', parent=styles['Heading1'], fontSize=15,
        textColor=colors.HexColor('#1e3a8a'), spaceAfter=2
    )
    subtitulo_style = ParagraphStyle(
        'Subtitulo', parent=styles['Normal'], fontSize=11,
        textColor=colors.HexColor('#475569'), spaceAfter=10
    )
    celda_style = ParagraphStyle('Celda', parent=styles['Normal'], fontSize=9, leading=11)
    celda_header_style = ParagraphStyle(
        'CeldaHeader', parent=styles['Normal'], fontSize=9,
        leading=11, textColor=colors.white, fontName='Helvetica-Bold'
    )

    materia = datos_analisis.get("materia", "No especificada")
    todos_los_alumnos = datos_analisis.get("alumnos", [])

    reprobados = []
    sin_datos = []
    for alumno in todos_los_alumnos:
        estatus, criterios_reprobados = calcular_estatus(alumno)
        if estatus == "Reprobado":
            alumno["_criterios_reprobados"] = criterios_reprobados
            alumno["_estatus"] = "Reprobado"
            reprobados.append(alumno)
        elif estatus == "Sin datos":
            alumno["_criterios_reprobados"] = []
            alumno["_estatus"] = "Sin datos"
            sin_datos.append(alumno)

    alumnos_a_mostrar = reprobados + sin_datos

    story.append(Paragraph("REPORTE DE ALUMNOS REPROBADOS", titulo_style))
    story.append(Paragraph(
        f"Materia: {_texto_seguro(materia)}  |  "
        f"Reprobados: {len(reprobados)}  |  "
        f"Sin datos suficientes: {len(sin_datos)}  |  "
        f"Total procesados: {len(todos_los_alumnos)}",
        subtitulo_style
    ))
    story.append(HRFlowable(width='100%', thickness=1.5, color=colors.HexColor('#991b1b'), spaceAfter=12))

    if not alumnos_a_mostrar:
        story.append(Paragraph("No hay alumnos reprobados en esta materia.", celda_style))
        doc.build(story)
        return ruta_pdf

    nombres_criterios = []
    for alumno in alumnos_a_mostrar:
        for criterio in alumno.get("criterios", []):
            nombre_c = criterio.get("nombre", "").strip()
            if nombre_c and nombre_c not in nombres_criterios:
                nombres_criterios.append(nombre_c)

    encabezado = [
        Paragraph("No. Control", celda_header_style),
        Paragraph("Nombre", celda_header_style),
    ]
    for nc in nombres_criterios:
        encabezado.append(Paragraph(_texto_seguro(nc), celda_header_style))
    encabezado.append(Paragraph("Estatus", celda_header_style))

    filas = [encabezado]
    estatus_reprobado_style = ParagraphStyle(
        'EstatusReprobado', parent=celda_style,
        textColor=colors.HexColor('#991b1b'), fontName='Helvetica-Bold'
    )
    estatus_sindatos_style = ParagraphStyle(
        'EstatusSinDatos', parent=celda_style,
        textColor=colors.HexColor('#b45309'), fontName='Helvetica-Bold'
    )

    for alumno in alumnos_a_mostrar:
        criterios_dict = {c.get("nombre", "").strip(): c.get("calificacion", "-") for c in alumno.get("criterios", [])}
        criterios_reprobados = set(alumno.get("_criterios_reprobados", []))

        fila = [
            Paragraph(_texto_seguro(alumno.get("numero_control", "N/D")), celda_style),
            Paragraph(_texto_seguro(alumno.get("nombre", "-")), celda_style),
        ]
        for nc in nombres_criterios:
            valor_criterio = criterios_dict.get(nc, "-")
            texto_criterio = _texto_seguro(valor_criterio)
            if nc in criterios_reprobados:
                texto_criterio = f'<font color="#991b1b"><b>{texto_criterio}</b></font>'
            fila.append(Paragraph(texto_criterio, celda_style))

        if alumno.get("_estatus") == "Sin datos":
            fila.append(Paragraph("Sin datos", estatus_sindatos_style))
        else:
            fila.append(Paragraph("Reprobado", estatus_reprobado_style))

        filas.append(fila)

    ancho_disponible = A4[0] - 60
    ancho_no_control = 60
    ancho_estatus = 65
    ancho_restante = ancho_disponible - ancho_no_control - ancho_estatus
    ancho_nombre = ancho_restante * 0.45
    ancho_criterio = (ancho_restante * 0.55) / max(len(nombres_criterios), 1)

    anchos_columnas = [ancho_no_control, ancho_nombre] + \
        [ancho_criterio] * len(nombres_criterios) + \
        [ancho_estatus]

    tabla = Table(filas, colWidths=anchos_columnas, repeatRows=1)
    tabla.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#991b1b')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fef2f2')]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(tabla)

    doc.build(story)
    return ruta_pdf


def generar_reporte_global_critico(historial_materias):
    """
    Lee los resultados consolidados de todas las materias, detecta alumnos con 3 o más
    materias reprobadas y calcula en qué criterios suelen fallar comúnmente.
    """
    os.makedirs("reportes_pdf", exist_ok=True)
    ruta_pdf = os.path.join("reportes_pdf", "reporte_global_critico.pdf")

    doc = SimpleDocTemplate(
        ruta_pdf, pagesize=A4,
        rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30
    )
    story = []
    styles = getSampleStyleSheet()

    titulo_style = ParagraphStyle(
        'TituloGlobal', parent=styles['Heading1'], fontSize=16,
        textColor=colors.HexColor('#7f1d1d'), spaceAfter=4
    )
    subtitulo_style = ParagraphStyle(
        'SubGlobal', parent=styles['Normal'], fontSize=10,
        textColor=colors.HexColor('#475569'), spaceAfter=12
    )
    seccion_style = ParagraphStyle(
        'SeccionGlobal', parent=styles['Heading2'], fontSize=12,
        textColor=colors.HexColor('#1e3a8a'), spaceBefore=10, spaceAfter=6
    )
    celda_style = ParagraphStyle('CeldaG', parent=styles['Normal'], fontSize=9, leading=11)
    celda_header_style = ParagraphStyle(
        'CeldaHeaderG', parent=styles['Normal'], fontSize=9,
        leading=11, textColor=colors.white, fontName='Helvetica-Bold'
    )

    alumnos_info = {}
    contador_criterios_globales = Counter()

    for materia, lista_alumnos in historial_materias.items():
        for alumno in lista_alumnos:
            estatus, criterios_reprobados = calcular_estatus(alumno)
            if estatus == "Reprobado":
                control = alumno.get("numero_control", "N/D")
                nombre = alumno.get("nombre", "Desconocido")

                if control not in alumnos_info:
                    alumnos_info[control] = {
                        "nombre": nombre,
                        "materias": set(),
                        "criterios": []
                    }
                
                alumnos_info[control]["materias"].add(materia)
                for crit in criterios_reprobados:
                    if "final" not in crit.lower():
                        alumnos_info[control]["criterios"].append(crit)
                        contador_criterios_globales[crit] += 1

    alumnos_criticos = []
    for control, info in alumnos_info.items():
        if len(info["materias"]) >= 3:
            criterios_alumno = info["criterios"]
            criterio_comun = "General"
            if criterios_alumno:
                criterio_comun = Counter(criterios_alumno).most_common(1)[0][0]

            alumnos_criticos.append({
                "numero_control": control,
                "nombre": info["nombre"],
                "total_materias": len(info["materias"]),
                "lista_materias": ", ".join(info["materias"]),
                "criterio_frecuente": criterio_comun
            })

    story.append(Paragraph("REPORTE GLOBAL DE ALUMNOS EN RIESGO CRÍTICO", titulo_style))
    story.append(Paragraph("Estudiantes con 3 o más materias reprobadas y análisis de criterios de fallo común.", subtitulo_style))
    story.append(HRFlowable(width='100%', thickness=1.5, color=colors.HexColor('#7f1d1d'), spaceAfter=12))

    story.append(Paragraph("Análisis General de Criterios con Mayor Incidencia de Reprobación", seccion_style))
    if contador_criterios_globales:
        criterio_top, conteo_top = contador_criterios_globales.most_common(1)[0]
        texto_analisis = (
            f"Tras consolidar las evaluaciones, se detecta que el criterio específico en el que "
            f"<b>comúnmente suelen fallar más los alumnos</b> es <b>{_texto_seguro(criterio_top)}</b> "
            f"(acumulando {conteo_top} incidencias de reprobación)."
        )
    else:
        texto_analisis = "No hay suficientes datos de criterios reprobados registrados."
    
    story.append(Paragraph(texto_analisis, celda_style))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Alumnos con 3 o más Materias Reprobadas", seccion_style))

    if not alumnos_criticos:
        story.append(Paragraph("Excelente noticia: No se encontraron alumnos con 3 o más materias reprobadas en el período.", celda_style))
        doc.build(story)
        return ruta_pdf

    encabezado = [
        Paragraph("No. Control", celda_header_style),
        Paragraph("Nombre del Alumno", celda_header_style),
        Paragraph("Materias Reprobadas", celda_header_style),
        Paragraph("Cant.", celda_header_style),
        Paragraph("Criterio más afectado", celda_header_style),
    ]

    filas = [encabezado]
    for ac in alumnos_criticos:
        filas.append([
            Paragraph(_texto_seguro(ac["numero_control"]), celda_style),
            Paragraph(_texto_seguro(ac["nombre"]), celda_style),
            Paragraph(_texto_seguro(ac["lista_materias"]), celda_style),
            Paragraph(str(ac["total_materias"]), celda_style),
            Paragraph(_texto_seguro(ac["criterio_frecuente"]), celda_style),
        ])

    ancho_disponible = A4[0] - 60
    anchos_cols = [60, 130, 164, 35, 115]

    tabla_criticos = Table(filas, colWidths=anchos_cols, repeatRows=1)
    tabla_criticos.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#7f1d1d')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fef2f2')]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))

    story.append(tabla_criticos)
    doc.build(story)
    return ruta_pdf


if __name__ == '__main__':
    patron_busqueda = os.path.join("reportes_json", "*_datos.json")
    archivos_json = glob.glob(patron_busqueda)

    if not archivos_json:
        print("No se encontraron archivos JSON para analizar en reportes_json/")
        exit()

    print(f"Se encontraron {len(archivos_json)} archivo(s) JSON para procesar.\n")

    historial_general_materias = {}

    for archivo_path in archivos_json:
        nombre_base = os.path.basename(archivo_path)
        print("=" * 50)
        print(f"Procesando archivo: {nombre_base}")
        print("=" * 50)

        # Bucle de reintentos completos para todo el archivo/materia
        exito_materia = False
        intentos_materia = 0

        while not exito_materia and intentos_materia < MAX_INTENTOS:
            try:
                intentos_materia += 1
                with open(archivo_path, 'r', encoding='utf-8') as f:
                    datos_crudos = json.load(f)

                print(f"Enviando datos a Gemini para extracción (Intento {intentos_materia}/{MAX_INTENTOS})...")
                datos_analisis = analizar_datos_con_ia(datos_crudos, nombre_base)

                nombre_materia = datos_analisis.get("materia", "Materia")
                historial_general_materias[nombre_materia] = datos_analisis.get("alumnos", [])

                nombre_pdf_salida = f"analisis_{nombre_base.replace('_datos.json', '')}.pdf"
                
                print("Generando PDF individual con reprobados...")
                ruta_final = generar_pdf_analisis(datos_analisis, nombre_pdf_salida)

                print(f"✔ Proceso exitoso. PDF guardado en: {ruta_final}\n")
                exito_materia = True

            except Exception as e:
                print(f"❌ Error procesando {nombre_base} en el intento {intentos_materia}: {e}")
                if intentos_materia < MAX_INTENTOS:
                    print(f"🔄 Reintentando materia {nombre_base} en 3 segundos...\n")
                    time.sleep(3)
                else:
                    print(f"⛔ Se agotaron los {MAX_INTENTOS} intentos para {nombre_base}. Se omite esta materia para continuar con las demás.\n")

    if historial_general_materias:
        print("=" * 50)
        print("Generando Reporte Global Consolidado (Alumnos con 3+ reprobaciones)...")
        print("=" * 50)
        try:
            ruta_global = generar_reporte_global_critico(historial_general_materias)
            print(f"✔ Reporte global generado con éxito: {ruta_global}\n")
        except Exception as e:
            print(f"❌ Error generando el reporte global: {e}\n")