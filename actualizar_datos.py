"""
Actualizar datos — Pistalo
============================================================
Ejecuta los scrapers de Padel Paiporta, Tu Padel Valencia (solo Picanya) y
Padel Sedaví, y guarda el resultado en data/disponibilidad.json con el formato
exacto que consume la web (agrupado por fecha -> lista de clubes -> pistas).

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
# PADEL SEDAVÍ
# ----------------------------------------------------------------------
# La web sirve el HTML ya escrito. Cada pista es un <ul class="partidas"> y
# cada franja un <li class="partida ..."> con un enlace cuyo href acaba en
#   /partidas/padel/AAAA-MM-DD#partida_<nº pista>_<HH>-<MM>
# El estado de la franja va en las clases del <li>:
#   (ninguna extra)              -> "Pista libre": nadie ha reservado
#   partida-participante-libre   -> "Partida abierta": alguien ha reservado y busca gente
#   partida-reservada            -> "Partida cerrada": reservada
#   partida-reserva              -> reserva (viene siempre junto a partida-reservada)
# Además, cada franja tiene su tarjeta con un botón «Lista de espera (N)»
# (atributos data-pista-id y data-hora). Si N > 0 hay gente esperando esa
# pista, y para Pistalo cuenta como NO disponible aunque no lleve clase de estado.
# Para Pistalo, la pista solo está LIBRE si no lleva ninguna clase de estado
# Y además nadie está en su lista de espera.

SEDAVI_BASE_URL = "https://www.padelsedavi.com/partidas/padel"

# ⚠️ PENDIENTE DE CONFIRMAR: la web no dice si las pistas son cubiertas o
# exteriores. Valores posibles: "Cubierta" o "Exterior".
SEDAVI_TIPO_PISTAS = "Cubierta"

SEDAVI_CLASES_BASE = {"partida", "partida-deporte-padel"}
SEDAVI_CLASES_OCUPADA = {"partida-reservada", "partida-participante-libre", "partida-reserva"}

# Clases de estado que el scraper no conoce. Se tratan como «ocupada» por
# seguridad, y se avisa una sola vez al final de la ejecución.
sedavi_clases_desconocidas: set[str] = set()

# Franjas que parecían libres pero cuya lista de espera no hemos podido
# comprobar (no se encontró su botón). Se marcan como no disponibles.
sedavi_franjas_sin_lista: list[str] = []

_SEDAVI_ENLACE = re.compile(r"(\d{4}-\d{2}-\d{2})#partida_(\d+)_(\d{2})-(\d{2})")


class DiaNoDisponible(Exception):
    """La web no ofrece ese día (por ejemplo, ya pasó o aún no está abierto)."""


class EstructuraCambiada(Exception):
    """La página no tiene la estructura esperada (probable cambio en la web)."""


def sedavi_url_dia(fecha: date) -> str:
    return f"{SEDAVI_BASE_URL}/{fecha.isoformat()}"


def sedavi_obtener_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.text


def _sumar_minutos(hora: int, minuto: int, minutos: int = 90) -> str:
    total = hora * 60 + minuto + minutos
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def _sedavi_esperas(soup) -> dict:
    """Devuelve {(nº pista, "HH:MM"): personas en lista de espera}."""
    esperas: dict[tuple[int, str], int] = {}
    for boton in soup.select("button.lista[data-hora][data-pista-id]"):
        if "espera" not in boton.get_text(" ", strip=True).lower():
            continue
        try:
            pista = int(boton["data-pista-id"])
            hh, _, mm = boton["data-hora"].partition(":")
            hora = f"{int(hh):02d}:{int(mm):02d}"
        except ValueError:
            continue
        contador = boton.select_one("span.count")
        m = re.search(r"\d+", contador.get_text(strip=True)) if contador else None
        n = int(m.group()) if m else 0
        # Si por lo que sea el mismo botón sale dos veces, nos quedamos con el mayor
        esperas[(pista, hora)] = max(esperas.get((pista, hora), 0), n)
    return esperas


def sedavi_parsear(html: str, fecha: date) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    esperas = _sedavi_esperas(soup)
    franjas = []
    ya_vistas = set()
    fechas_vistas = set()

    for li in soup.select("li.partida.partida-deporte-padel"):
        enlace = li.find("a", href=True)
        m = _SEDAVI_ENLACE.search(enlace["href"]) if enlace else None
        if not m:
            continue
        fecha_enlace = m.group(1)
        num_pista, hh, mm = int(m.group(2)), int(m.group(3)), int(m.group(4))
        fechas_vistas.add(fecha_enlace)

        # Solo nos quedamos con las franjas del día que hemos pedido: si la
        # web nos devuelve otro día (p. ej. al pedir una fecha pasada nos da
        # la de hoy), no queremos etiquetar esos datos con la fecha equivocada.
        if fecha_enlace != fecha.isoformat():
            continue
        if (num_pista, hh, mm) in ya_vistas:
            continue
        ya_vistas.add((num_pista, hh, mm))

        clases = set(li.get("class", []))
        desconocidas = clases - SEDAVI_CLASES_BASE - SEDAVI_CLASES_OCUPADA
        sedavi_clases_desconocidas.update(desconocidas)
        candidata_libre = not (clases & SEDAVI_CLASES_OCUPADA) and not desconocidas

        franjas.append({
            "_orden": (num_pista, hh, mm),
            "_candidata": candidata_libre,
            "pista": f"Pista {num_pista}",
            "tipo": SEDAVI_TIPO_PISTAS,
            "hora_inicio": f"{hh:02d}:{mm:02d}",
            "hora_fin": _sumar_minutos(hh, mm),
            "libre": False,
        })

    if not franjas and fechas_vistas:
        raise DiaNoDisponible(
            f"La web no ofrece el {fecha.isoformat()} (ha devuelto datos de: {', '.join(sorted(fechas_vistas))})."
        )

    # Si hay franjas pero no encontramos NINGÚN botón de «Lista de espera»,
    # no podemos saber si hay gente esperando: mejor no publicar nada que
    # publicar pistas «libres» que quizá no lo son.
    if franjas and not esperas:
        raise EstructuraCambiada(
            "Se han encontrado franjas pero ningún botón de «Lista de espera» en la página, "
            "así que no se puede saber si hay gente esperando cada pista. "
            "Probablemente la web ha cambiado su estructura. No se han publicado datos de este día."
        )

    sin_lista = []
    for f in franjas:
        if not f["_candidata"]:
            continue
        clave = (f["_orden"][0], f["hora_inicio"])
        if clave in esperas:
            f["libre"] = esperas[clave] == 0
        else:
            # Parece libre pero no podemos comprobar su lista de espera: no disponible
            sin_lista.append(f"{fecha.isoformat()} {f['pista']} {f['hora_inicio']}")
    sedavi_franjas_sin_lista.extend(sin_lista)

    franjas.sort(key=lambda f: f["_orden"])
    for f in franjas:
        del f["_orden"]
        del f["_candidata"]
    return franjas


def sedavi_snapshot(fecha: date) -> list[dict]:
    html = sedavi_obtener_html(sedavi_url_dia(fecha))
    return sedavi_parsear(html, fecha)


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
    "Padel Sedaví": 5,               # en el HTML se ven al menos 8 columnas de pista
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
    except DiaNoDisponible as e:
        # No es un fallo: simplemente ese día no está publicado. Se omite
        # sin alerta (si ocurre con TODOS los días, se avisa al final).
        print(f"[INFO] {nombre_club} ({fecha_str}): {e}")
        return None, None
    except EstructuraCambiada as e:
        return None, {
            "club": nombre_club,
            "fecha": fecha_str,
            "tipo": "datos_incompletos",
            "mensaje": str(e),
        }
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
    sedavi_clases_desconocidas.clear()
    sedavi_franjas_sin_lista.clear()

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

        club_sedavi, error_sedavi = procesar_club(
            "Padel Sedaví", "Sedaví, Valencia", SEDAVI_BASE_URL,
            sedavi_snapshot, fecha,
        )
        if club_sedavi:
            clubs_del_dia.append(club_sedavi)
        if error_sedavi:
            print(f"[AVISO] {error_sedavi['club']} ({fecha_str}): {error_sedavi['mensaje']}")
            errores.append(error_sedavi)

        resultado_por_fecha[fecha_str] = clubs_del_dia
        time.sleep(3)

    # --- Avisos globales, una sola vez por ejecución ---
    if sedavi_clases_desconocidas:
        errores.append({
            "club": "Padel Sedaví",
            "fecha": date.today().isoformat(),
            "tipo": "datos_incompletos",
            "mensaje": (
                "La web usa clases de estado que el scraper no conoce: "
                f"{', '.join(sorted(sedavi_clases_desconocidas))}. "
                "Esas franjas se han marcado como «ocupadas» por seguridad. "
                "Revisa qué significan (por ejemplo, un tipo de reserva nuevo)."
            ),
        })

    if sedavi_franjas_sin_lista:
        ejemplos = ", ".join(sedavi_franjas_sin_lista[:5])
        mas = f" (y {len(sedavi_franjas_sin_lista) - 5} más)" if len(sedavi_franjas_sin_lista) > 5 else ""
        errores.append({
            "club": "Padel Sedaví",
            "fecha": date.today().isoformat(),
            "tipo": "datos_incompletos",
            "mensaje": (
                f"{len(sedavi_franjas_sin_lista)} franja(s) que parecían libres no tienen su botón de "
                "«Lista de espera» en la página, así que no se ha podido comprobar si hay gente esperando. "
                f"Se han marcado como no disponibles por seguridad. Ejemplos: {ejemplos}{mas}."
            ),
        })

    clubs_con_datos = {c["club"] for clubs in resultado_por_fecha.values() for c in clubs}
    clubs_con_error = {e["club"] for e in errores}
    for nombre in PISTAS_MINIMAS_ESPERADAS:
        if nombre not in clubs_con_datos and nombre not in clubs_con_error:
            errores.append({
                "club": nombre,
                "fecha": date.today().isoformat(),
                "tipo": "datos_incompletos",
                "mensaje": (
                    "Este club no ha devuelto datos de ningún día en esta ejecución, "
                    "aunque tampoco ha dado un error de conexión. Puede que la web "
                    "haya cambiado la forma de indicar las fechas."
                ),
            })

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
