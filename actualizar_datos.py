"""
Actualizar datos — Pistalo
============================================================
Ejecuta los scrapers de Padel Paiporta y Tu Padel Valencia (solo Picanya),
y guarda el resultado en data/disponibilidad.json con el formato exacto
que consume la web (agrupado por fecha -> lista de clubes -> pistas).

Este script está pensado para ejecutarse automáticamente via GitHub
Actions, pero también puedes correrlo tú a mano:

    pip install requests beautifulsoup4 --break-system-packages
    python3 actualizar_datos.py

Genera/actualiza: data/disponibilidad.json
"""

from __future__ import annotations

import re
import os
import json
import time
import traceback
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "PistaloBot/0.1 (comparador de pistas de padel; contacto: hola@pistalo.com)"
}

DIAS_A_CONSULTAR = 7


# ----------------------------------------------------------------------
# PADEL PAIPORTA
# ----------------------------------------------------------------------

PAIPORTA_BASE_URL = "https://www.padelpaiporta.com/"


def paiporta_url_dia(fecha: date) -> str:
    return f"{PAIPORTA_BASE_URL}index.php?fecha={fecha.year}-{fecha.month}-{fecha.day}"


def paiporta_obtener_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    return resp.text


def paiporta_parsear(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    resultados = []
    bloques_franja = soup.select("#partidas .float-left .partida.rounded")

    for bloque in bloques_franja:
        h2 = bloque.find("h2")
        if not h2:
            continue

        nombre_pista = " ".join(h2.stripped_strings)
        span = bloque.find("span", recursive=False)
        if not span:
            continue
        horario = span.get_text(strip=True)
        hora_inicio, _, hora_fin = horario.partition("-")

        tipo = "Exterior" if "EXTERIOR" in nombre_pista.upper() else "Cubierta"

        rounded_b = bloque.find("div", class_="rounded_b")
        jugadores_juegan, jugadores_espera = [], []
        if rounded_b:
            ul_juegan = rounded_b.find("ul", class_="juegan")
            ul_esperan = rounded_b.find("ul", class_="esperan")
            if ul_juegan:
                jugadores_juegan = [
                    li.get_text(strip=True) for li in ul_juegan.find_all("li")
                    if li.get_text(strip=True)
                ]
            if ul_esperan:
                jugadores_espera = [
                    li.get_text(strip=True) for li in ul_esperan.find_all("li")
                    if li.get_text(strip=True)
                ]

        libre = (len(jugadores_juegan) == 0 and len(jugadores_espera) == 0)

        resultados.append({
            "pista": nombre_pista,
            "tipo": tipo,
            "hora_inicio": hora_inicio.strip(),
            "hora_fin": hora_fin.strip(),
            "libre": libre,
        })

    return resultados


def paiporta_snapshot(fecha: date) -> list[dict]:
    html = paiporta_obtener_html(paiporta_url_dia(fecha))
    return paiporta_parsear(html)


# ----------------------------------------------------------------------
# TU PADEL VALENCIA (solo centro Picanya)
# ----------------------------------------------------------------------

TUPADEL_GRID_URL = "https://www.tupadelvalencia.com/Partidas_Padel.aspx"


def tupadel_url_dia(fecha: date) -> str:
    return f"{TUPADEL_GRID_URL}?fecha={fecha.strftime('%Y.%m.%d')}"


def tupadel_obtener_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    return resp.text


def tupadel_parsear(html: str, centro_deseado: str = "PICAÑA") -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    resultados = []
    bloques_marco = soup.find_all("div", class_="marco_partida_padel")

    for marco in bloques_marco:
        nombre_div = marco.find("div", class_="partidaNombrePista_padel")
        bloque = marco.find("div", class_="partida_padel")
        if not nombre_div or not bloque:
            continue
        horario_div = bloque.find("div", class_="partidaHorario_padel")
        if not horario_div:
            continue

        nombre_pista = " ".join(nombre_div.stripped_strings)
        if centro_deseado not in nombre_pista.upper():
            continue

        horario = horario_div.get_text(strip=True)
        hora_inicio, _, hora_fin = horario.partition("-")

        jugadores = []
        texto_evento = ""
        bloque_juegan = bloque.find("div", class_="partidaJuegan_padel")
        if bloque_juegan:
            ul_jugadores = bloque_juegan.find("ul")
            if ul_jugadores:
                jugadores = [
                    li.get("title", "").strip() for li in ul_jugadores.find_all("li")
                    if li.get("title", "").strip()
                ]
            nombre_evento_div = bloque_juegan.find("div", class_="partidaNombre_padel")
            if nombre_evento_div:
                texto_evento = nombre_evento_div.get_text(strip=True)

        texto_esperan = ""
        bloque_esperan = bloque.find("div", class_="partidaEsperan_padel")
        if bloque_esperan:
            texto_esperan = bloque_esperan.get_text(strip=True).replace("Esperan", "", 1).strip()

        libre = not jugadores and not texto_evento and not texto_esperan

        resultados.append({
            "pista": nombre_pista,
            "tipo": "Cubierta",
            "hora_inicio": hora_inicio.strip(),
            "hora_fin": hora_fin.strip(),
            "libre": libre,
        })

    return resultados


def tupadel_snapshot(fecha: date) -> list[dict]:
    html = tupadel_obtener_html(tupadel_url_dia(fecha))
    return tupadel_parsear(html)


# ----------------------------------------------------------------------
# TRANSFORMAR AL FORMATO DE LA WEB
# ----------------------------------------------------------------------

def normalizar_hora(h: str) -> str:
    partes = h.split(":")
    return f"{int(partes[0]):02d}:{partes[1]}"


def agrupar_por_pista(franjas: list[dict]) -> list[dict]:
    por_pista: dict[str, list[dict]] = {}
    for f in franjas:
        por_pista.setdefault(f["pista"], []).append(f)

    pistas = []
    for nombre_pista, lista in por_pista.items():
        tipo = lista[0]["tipo"]
        nombre_limpio = (
            nombre_pista.replace(" - PICAÑA", "").replace(" - VALENCIA", "")
        )
        nombre_limpio = nombre_limpio.title() if nombre_limpio.isupper() else nombre_limpio
        slots = [normalizar_hora(f["hora_inicio"]) for f in lista]
        ocupadas = [normalizar_hora(f["hora_inicio"]) for f in lista if not f["libre"]]
        pistas.append({
            "pista": nombre_limpio,
            "tipo": tipo,
            "slots": slots,
            "ocupadas": ocupadas,
        })
    return pistas



# Cuántas pistas esperamos como mínimo en un día normal. Si un club devuelve
# menos de esto SIN dar ningún error de conexión, es señal de que algo ha
# cambiado en su web (y el scraper ya no la está leyendo bien).
PISTAS_MINIMAS_ESPERADAS = {
    "Padel Paiporta": 4,             # normalmente hay 6 (5 + exterior)
    "Tu Padel Valencia — Picanya": 4,  # normalmente hay 5
}


def procesar_club(nombre_club: str, zona: str, url: str, snapshot_fn, fecha: date) -> tuple[dict | None, dict | None]:
    """
    Intenta obtener y procesar los datos de un club para una fecha.
    Devuelve (club_dict, error_dict). Cualquiera de los dos puede ser None:
      - Si todo va bien: (club_dict, None)
      - Si falla la conexión o el parseo: (None, error_dict)
      - Si conecta bien pero los datos parecen incompletos/raros: (club_dict, error_dict)
        (se guardan igualmente los datos parciales que se hayan podido sacar,
        y además se avisa del problema)
    """
    fecha_str = fecha.isoformat()

    # --- Paso 1: intentar obtener y parsear los datos ---
    try:
        franjas = snapshot_fn(fecha)
        pistas = agrupar_por_pista(franjas)
    except requests.RequestException as e:
        return None, {
            "club": nombre_club,
            "fecha": fecha_str,
            "tipo": "conexión",
            "mensaje": f"No se pudo conectar con la web: {e}",
        }
    except Exception as e:
        # Cualquier otro fallo (parseo, HTML inesperado, etc.) — casi
        # siempre significa que la web ha cambiado de estructura.
        tb_resumida = traceback.format_exc(limit=4)
        return None, {
            "club": nombre_club,
            "fecha": fecha_str,
            "tipo": "error_inesperado",
            "mensaje": (
                f"Fallo inesperado al procesar la respuesta ({type(e).__name__}: {e}). "
                f"Es probable que la web haya cambiado de estructura."
            ),
            "traceback": tb_resumida,
        }

    club_dict = {
        "club": nombre_club,
        "zona": zona,
        "url": url,
        "real": True,
        "pistas": pistas,
    }

    # --- Paso 2: validar que los datos obtenidos tienen buena pinta ---
    minimo = PISTAS_MINIMAS_ESPERADAS.get(nombre_club, 1)
    num_pistas = len(pistas)
    pistas_sin_horarios = [p["pista"] for p in pistas if len(p["slots"]) == 0]

    problemas = []
    if num_pistas == 0:
        problemas.append(
            "no se ha encontrado ninguna pista en la página — probablemente la web "
            "ha cambiado su estructura HTML y el scraper ya no la reconoce."
        )
    elif num_pistas < minimo:
        problemas.append(
            f"solo se han encontrado {num_pistas} pistas (se esperaban {minimo} o más) — "
            f"puede que la web haya cambiado, o que varias pistas estén fuera de servicio a la vez."
        )
    if pistas_sin_horarios:
        problemas.append(
            f"estas pistas se han encontrado pero sin ningún horario dentro: {', '.join(pistas_sin_horarios)}."
        )

    if problemas:
        return club_dict, {
            "club": nombre_club,
            "fecha": fecha_str,
            "tipo": "datos_incompletos",
            "mensaje": " ".join(problemas),
        }

    return club_dict, None


def generar_datos() -> tuple[dict, list]:
    resultado_por_fecha = {}
    errores = []

    for i in range(DIAS_A_CONSULTAR):
        fecha = date.today() + timedelta(days=i)
        fecha_str = fecha.isoformat()
        clubs_del_dia = []

        club_paiporta, error_paiporta = procesar_club(
            "Padel Paiporta", "Paiporta, Valencia", "https://www.padelpaiporta.com/",
            paiporta_snapshot, fecha,
        )
        if club_paiporta:
            clubs_del_dia.append(club_paiporta)
        if error_paiporta:
            print(f"[AVISO] {error_paiporta['club']} ({fecha_str}): {error_paiporta['mensaje']}")
            errores.append(error_paiporta)

        time.sleep(3)

        club_tupadel, error_tupadel = procesar_club(
            "Tu Padel Valencia — Picanya", "Picanya, Valencia", "https://www.tupadelvalencia.com/Partidas_Padel.aspx",
            tupadel_snapshot, fecha,
        )
        if club_tupadel:
            clubs_del_dia.append(club_tupadel)
        if error_tupadel:
            print(f"[AVISO] {error_tupadel['club']} ({fecha_str}): {error_tupadel['mensaje']}")
            errores.append(error_tupadel)

        resultado_por_fecha[fecha_str] = clubs_del_dia
        time.sleep(3)

    return resultado_por_fecha, errores


NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "pistalo-scraping-vy8k2m")


ICONO_TIPO = {
    "conexión": "🔌",
    "error_inesperado": "🧩",
    "datos_incompletos": "📉",
}


def enviar_alerta(errores: list) -> None:
    lineas = []
    for e in errores[:5]:
        icono = ICONO_TIPO.get(e.get("tipo"), "⚠️")
        lineas.append(f"{icono} {e['club']} ({e['fecha']}): {e['mensaje']}")
    if len(errores) > 5:
        lineas.append(f"…y {len(errores) - 5} problema(s) más. Revisa el panel para verlos todos.")
    mensaje = "\n".join(lineas)

    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=mensaje.encode("utf-8"),
            headers={"Title": f"Pistalo — {len(errores)} problema(s) detectado(s)", "Priority": "high"},
            timeout=10,
        )
        print(f"[ALERTA] Notificación enviada a ntfy.sh/{NTFY_TOPIC}")
    except requests.RequestException as e:
        print(f"[AVISO] No se pudo enviar la notificación de alerta: {e}")


if __name__ == "__main__":
    from datetime import datetime, timezone

    try:
        datos, errores = generar_datos()

        os.makedirs("data", exist_ok=True)
        salida = {
            "actualizado_en": date.today().isoformat(),
            "ultima_ejecucion_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "errores": errores,
            "por_fecha": datos,
        }
        with open("data/disponibilidad.json", "w", encoding="utf-8") as f:
            json.dump(salida, f, ensure_ascii=False, indent=2)

        total_clubs = sum(len(v) for v in datos.values())
        print(f"Guardado data/disponibilidad.json con {len(datos)} días y {total_clubs} entradas de club en total.")

        if errores:
            print(f"[AVISO] Se han detectado {len(errores)} problema(s) durante esta ejecución.")
            enviar_alerta(errores)
        else:
            print("Sin errores en esta ejecución.")

    except Exception as e:
        # Red de seguridad: si algo revienta de una forma que no habíamos
        # previsto (un bug en el propio script, por ejemplo), esto evita que
        # el fallo pase desapercibido del todo. No se sobrescribe el archivo
        # de datos (así la web sigue mostrando los últimos datos buenos que
        # había), pero sí se manda una alerta con el máximo detalle posible.
        tb_completa = traceback.format_exc()
        print("[ERROR CRÍTICO] La ejecución ha fallado por completo:")
        print(tb_completa)
        enviar_alerta([{
            "club": "Ejecución completa",
            "fecha": date.today().isoformat(),
            "tipo": "error_inesperado",
            "mensaje": f"El script ha fallado por completo ({type(e).__name__}: {e}). No se ha actualizado ningún dato esta vez.",
        }])
        raise  # que la ejecución de GitHub Actions también quede marcada como fallida
