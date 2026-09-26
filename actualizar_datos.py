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


def generar_datos() -> tuple[dict, list]:
    resultado_por_fecha = {}
    errores = []

    for i in range(DIAS_A_CONSULTAR):
        fecha = date.today() + timedelta(days=i)
        fecha_str = fecha.isoformat()
        clubs_del_dia = []

        try:
            franjas_paiporta = paiporta_snapshot(fecha)
            clubs_del_dia.append({
                "club": "Padel Paiporta",
                "zona": "Paiporta, Valencia",
                "url": "https://www.padelpaiporta.com/",
                "real": True,
                "pistas": agrupar_por_pista(franjas_paiporta),
            })
        except requests.RequestException as e:
            mensaje = f"Fallo consultando Padel Paiporta para {fecha_str}: {e}"
            print(f"[AVISO] {mensaje}")
            errores.append({"club": "Padel Paiporta", "fecha": fecha_str, "mensaje": str(e)})

        time.sleep(3)

        try:
            franjas_tupadel = tupadel_snapshot(fecha)
            clubs_del_dia.append({
                "club": "Tu Padel Valencia — Picanya",
                "zona": "Picanya, Valencia",
                "url": "https://www.tupadelvalencia.com/Partidas_Padel.aspx",
                "real": True,
                "pistas": agrupar_por_pista(franjas_tupadel),
            })
        except requests.RequestException as e:
            mensaje = f"Fallo consultando Tu Padel Valencia para {fecha_str}: {e}"
            print(f"[AVISO] {mensaje}")
            errores.append({"club": "Tu Padel Valencia — Picanya", "fecha": fecha_str, "mensaje": str(e)})

        resultado_por_fecha[fecha_str] = clubs_del_dia
        time.sleep(3)

    return resultado_por_fecha, errores


NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "pistalo-scraping-vy8k2m")


def enviar_alerta(errores: list) -> None:
    resumen = "; ".join(f"{e['club']} ({e['fecha']})" for e in errores[:5])
    mas = f" y {len(errores) - 5} más" if len(errores) > 5 else ""
    mensaje = f"⚠️ Pistalo: fallo al actualizar {resumen}{mas}"
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=mensaje.encode("utf-8"),
            headers={"Title": "Pistalo — fallo de scraping", "Priority": "high"},
            timeout=10,
        )
        print(f"[ALERTA] Notificación enviada a ntfy.sh/{NTFY_TOPIC}")
    except requests.RequestException as e:
        print(f"[AVISO] No se pudo enviar la notificación de alerta: {e}")


if __name__ == "__main__":
    from datetime import datetime, timezone

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
        print(f"[AVISO] Se han detectado {len(errores)} fallos durante esta ejecución.")
        enviar_alerta(errores)
    else:
        print("Sin errores en esta ejecución.")
