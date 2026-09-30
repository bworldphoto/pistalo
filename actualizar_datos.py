"""
Actualizar datos — Pistalo
============================================================
Ejecuta los scrapers de Padel Paiporta, Tu Padel Valencia (solo Picanya) y
Padel Sedaví y el Polideportivo Verge del Carme (Beteró), y guarda el resultado en data/disponibilidad.json con el formato
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
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "PistaloBot/0.1 (comparador de pistas de padel; contacto: hola@pistalo.com)"
}

DIAS_A_CONSULTAR = 7

# El robot de GitHub corre en hora UTC, que de madrugada va un día por detrás de
# España. Las webs de los clubes cuentan los días en hora española, así que
# calculamos «hoy» en hora de Madrid (si no hay zonas horarias, hora local).
try:
    from zoneinfo import ZoneInfo
    _TZ_MADRID = ZoneInfo("Europe/Madrid")
except Exception:  # Python < 3.9 o sin base de zonas horarias
    _TZ_MADRID = None


def hoy() -> date:
    return datetime.now(_TZ_MADRID).date() if _TZ_MADRID else datetime.now().date()


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
SEDAVI_CLASES_OCUPADA = {"partida-reservada", "partida-participante-libre", "partida-reserva", "partida-abierta"}

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
# POLIDEPORTIVO VERGE DEL CARME — BETERÓ (plataforma Matchpoint)
# ----------------------------------------------------------------------
# AUTORIZACIÓN: el robots.txt de esta web no permite el acceso automatizado.
# Pistalo ha recibido el visto bueno de los responsables (comunicado de forma
# verbal el 28-09-2026). Conviene conservar constancia por escrito.
#
# La web usa la plataforma Matchpoint, que ofrece una API JSON en vez de HTML:
#   1) GET  /Booking/Grid.aspx                 -> la página trae una «key»
#   2) POST /booking/srvc.aspx/ObtenerCuadro   {"idCuadro", "fecha" (d/m/aaaa), "key"}
#
# La respuesta NO trae franjas fijas: por cada pista trae los RANGOS OCUPADOS
# (con duración variable: 60, 90, 120 min...). Pistalo trabaja con franjas
# (horas de inicio), así que aquí las calculamos: para cada hora de inicio
# posible (cada MATCHPOINT_PASO_MIN dentro del horario del centro), la pista
# está libre si una sesión de MATCHPOINT_DURACION_MIN minutos cabe entera sin
# solaparse con ninguna ocupación y dentro del periodo en que se admiten
# reservas (hoy no se puede reservar en el pasado, y el último día solo hasta
# cierta hora). Cualquier tipo de ocupación (clase, reserva, partida...) cuenta
# como ocupada.

FHCV_BASE_URL = "https://reservas.fhcv.es"
FHCV_ID_CUADRO = "3"  # "PADEL" del Polideportivo Verge del Carme-Beteró
FHCV_CLUB = "Polideportivo Verge del Carme — Beteró"
FHCV_ZONA = "Beteró, Valencia"

# ⚠️ PENDIENTE DE CONFIRMAR: la web no dice si las pistas son cubiertas o
# exteriores. Valores posibles: "Cubierta" o "Exterior".
FHCV_TIPO_PISTAS = "Exterior"

# ----------------------------------------------------------------------
# 7PADEL VALENCIA (plataforma Matchpoint, franjas fijas de 90 min)
# ----------------------------------------------------------------------
# AUTORIZACIÓN: el club ha dado su consentimiento expreso (28-09-2026).
SIETEPADEL_BASE_URL = "https://www.7padel.com"
SIETEPADEL_ID_CUADRO = "4"  # "Padel"
SIETEPADEL_CLUB = "7Padel Valencia"
SIETEPADEL_ZONA = "Valencia"
SIETEPADEL_TIPO_PISTAS = "Cubierta"

# ----------------------------------------------------------------------
# ONE PÁDEL VALENCIA (plataforma Matchpoint, fijas de 90 min + huecos
# variables combinados en las mismas pistas — ver matchpoint_combinado_parsear)
# ----------------------------------------------------------------------
ONEPADEL_BASE_URL = "https://onepadelvalencia.com"
ONEPADEL_ID_CUADRO = "4"  # "Pádel Dobles"
ONEPADEL_CLUB = "One Pádel Valencia"
ONEPADEL_ZONA = "Paterna"
ONEPADEL_TIPO_PISTAS = "Cubierta"  # las "OUTDOOR" se detectan solas por el nombre

MATCHPOINT_PASO_MIN = 30
# Duración mínima de una reserva (la más corta que hemos visto en los datos es
# de 60 min). Si el club solo permitiera reservas de 90 min, subir este valor.
MATCHPOINT_DURACION_MIN = 60

_MATCHPOINT_CLAVE = re.compile(r"""=\s*['"]([A-Za-z0-9+/]{40,}={0,2})['"]\s*;""")
_RE_MP_FECHA = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_RE_MP_FECHA_HORA = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})")
_RE_MP_HORA = re.compile(r"^\s*(\d{1,2}):(\d{2})")


def _matchpoint_claves(html: str) -> list:
    """Todas las cadenas largas tipo base64 asignadas a una variable de la página."""
    vistas, claves = set(), []
    for m in _MATCHPOINT_CLAVE.finditer(html):
        if m.group(1) not in vistas:
            vistas.add(m.group(1))
            claves.append(m.group(1))
    return claves


def _mp_fecha(txt):
    m = _RE_MP_FECHA.search(txt or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _mp_fecha_hora(txt):
    m = _RE_MP_FECHA_HORA.search(txt or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), int(m.group(4)), int(m.group(5)))
    except ValueError:
        return None


def _mp_minutos(hhmm):
    m = _RE_MP_HORA.match(hhmm or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _mp_hhmm(minutos: int) -> str:
    return f"{(minutos // 60) % 24:02d}:{minutos % 60:02d}"


def _matchpoint_ventana_reservas(d: dict, inicio_dia: datetime) -> tuple[int | None, int | None]:
    """Minutos (relativos a la medianoche del día consultado) en los que se
    puede reservar: antes de ini_res ya ha pasado, después de fin_res aún
    no se admiten reservas para esa fecha."""
    def a_minutos(dt):
        return int((dt - inicio_dia).total_seconds() // 60) if dt else None

    ini_res = _mp_fecha_hora(d.get("StrFechaHoraInicioReservas"))
    fin_res = _mp_fecha_hora(d.get("StrFechaHoraFinReservas"))
    return a_minutos(ini_res), a_minutos(fin_res)


def _normalizar_nombre_pista(nombre: str) -> str:
    """Colapsa espacios dobles/sobrantes, p. ej. la propia web a veces manda «Pista  9»."""
    return " ".join(nombre.split())


def matchpoint_combinado_parsear(
    d: dict, fecha: date, tipo_pistas: str,
    duraciones_validas: tuple[int, ...] = (60, 90),
) -> list[dict]:
    """
    Para clubes donde CADA PISTA combina franjas fijas de 90 min (como 7Padel)
    con huecos de duración variable, sin plantilla (como Beteró) — ej. One Pádel
    Valencia. La propia respuesta lo anuncia con "CombinaHorariosFijosYLibres":
    true, pero el dato de verdad es más simple: cualquier tramo del horario del
    centro que NO aparezca ni en "HorariosFijos" ni en "Ocupaciones" es zona
    libre, y ahí se puede reservar 60 o 90 min empezando cada 30 min (igual que
    en Beteró, limitado por lo que venga antes: cierre, fin de la ventana de
    reservas, o el siguiente tramo ya cubierto por fijo/ocupación).
    """
    _matchpoint_dia_valido(d, fecha)
    inicio_dia = datetime(fecha.year, fecha.month, fecha.day)
    ap_min, ci_min = _matchpoint_apertura_cierre(d, inicio_dia)
    ini_res_min, fin_res_min = _matchpoint_ventana_reservas(d, inicio_dia)

    franjas = []
    for i, col in enumerate(d.get("Columnas") or [], 1):
        nombre = _normalizar_nombre_pista((col.get("TextoPrincipal") or "").strip()) or f"Pista {i}"
        # Estas dos llevan el nombre de un patrocinador en la web, pero son
        # numéricamente la 1 y la 2 del club (a continuación viene "Pista 3").
        ONEPADEL_ALIAS = {"p. lacoste": "Pista 1", "p. tecnifibre": "Pista 2"}
        nombre = ONEPADEL_ALIAS.get(nombre.lower(), nombre)
        tipo_pista = "Exterior" if "outdoor" in nombre.lower() else tipo_pistas
        if tipo_pista == "Exterior":
            # El distintivo de "Exterior" ya lo lleva el campo "tipo"; no hace
            # falta repetirlo también en el nombre de la pista.
            nombre = re.sub(r"\s*outdoor\s*$", "", nombre, flags=re.IGNORECASE).strip()

        cubiertos: list[tuple[int, int, bool]] = []  # (inicio, fin, es_franja_fija)

        for h in col.get("HorariosFijos") or []:
            ini_min = _mp_minutos(h.get("StrHoraInicio"))
            fin_min = _mp_minutos(h.get("StrHoraFin"))
            if fin_min == 0 and ini_min:
                fin_min = 24 * 60
            if ini_min is None or fin_min is None or fin_min <= ini_min:
                raise EstructuraCambiada(
                    f"Un horario fijo de «{nombre}» no tiene horas legibles "
                    f"(inicio={h.get('StrHoraInicio')!r}, fin={h.get('StrHoraFin')!r}). "
                    "Probablemente la web ha cambiado su formato."
                )
            cubiertos.append((ini_min, fin_min, True))
            libre = bool(h.get("Clickable"))
            if libre and ini_res_min is not None and ini_min < ini_res_min:
                libre = False
            if libre and fin_res_min is not None and ini_min >= fin_res_min:
                libre = False
            franjas.append({
                "pista": nombre, "tipo": tipo_pista,
                "hora_inicio": h.get("StrHoraInicio"), "hora_fin": h.get("StrHoraFin"),
                "libre": libre, "_orden": ini_min,
            })

        for o in col.get("Ocupaciones") or []:
            ini_min = _mp_minutos(o.get("StrHoraInicio"))
            fin_min = _mp_minutos(o.get("StrHoraFin"))
            if fin_min == 0 and ini_min:
                fin_min = 24 * 60
            if ini_min is None or fin_min is None or fin_min <= ini_min:
                raise EstructuraCambiada(
                    f"Una ocupación de «{nombre}» no tiene horas legibles "
                    f"(inicio={o.get('StrHoraInicio')!r}, fin={o.get('StrHoraFin')!r}). "
                    "Probablemente la web ha cambiado su formato."
                )
            cubiertos.append((ini_min, fin_min, False))
            franjas.append({
                "pista": nombre, "tipo": tipo_pista,
                "hora_inicio": o.get("StrHoraInicio"), "hora_fin": o.get("StrHoraFin"),
                "libre": False, "_orden": ini_min,
            })

        cubiertos.sort()
        fijo_termina_en = {fin for ini, fin, es_fijo in cubiertos if es_fijo}
        fijo_empieza_en = {ini for ini, fin, es_fijo in cubiertos if es_fijo}

        # Recorre el horario del centro en pasos de 30 min; cuando cae dentro de
        # un tramo ya cubierto (fijo u ocupación), salta directamente a su fin.
        t = ap_min
        while t < ci_min:
            en_cubierto = next((fin for ini, fin, _ in cubiertos if ini <= t < fin), None)
            if en_cubierto is not None:
                t = en_cubierto
                continue

            limite = ci_min
            limite_es_fijo = False  # ¿el límite viene de que empieza una franja fija?
            if fin_res_min is not None and fin_res_min < limite:
                limite, limite_es_fijo = fin_res_min, False
            siguientes = [(ini, es_fijo) for ini, _, es_fijo in cubiertos if ini > t]
            if siguientes:
                ini_siguiente, es_fijo_siguiente = min(siguientes, key=lambda x: x[0])
                if ini_siguiente < limite:
                    limite, limite_es_fijo = ini_siguiente, es_fijo_siguiente

            hueco = limite - t
            if hueco < MATCHPOINT_DURACION_MIN:
                # Ni 60 min caben antes del siguiente tramo cubierto: no hay
                # nada que reservar aquí y no merece la pena generar franja.
                t += MATCHPOINT_PASO_MIN
                continue

            if hueco < 90:
                # Hueco ajustado (solo caben 60, no 90): por lo que hemos
                # comprobado con datos reales, la web SOLO ofrece este caso
                # cuando el hueco toca directamente con una franja fija (justo
                # después de que termine una, o justo antes de que empiece
                # otra). Si el límite es el cierre del centro, el fin de la
                # ventana de reservas, o una ocupación normal, no lo ofrece
                # aunque el hueco encaje de sobra en el papel.
                pegado_a_fijo = (t in fijo_termina_en) or limite_es_fijo
                if not pegado_a_fijo:
                    t += MATCHPOINT_PASO_MIN
                    continue

            libre = True
            if ini_res_min is not None and t < ini_res_min:
                libre = False
            duraciones_posibles = ([60, 90] if hueco >= 90 else [60]) if libre else None
            franjas.append({
                "pista": nombre, "tipo": tipo_pista,
                "hora_inicio": _mp_hhmm(t), "hora_fin": _mp_hhmm(t + MATCHPOINT_DURACION_MIN),
                "libre": libre, "duraciones_posibles": duraciones_posibles, "_orden": t,
            })
            t += MATCHPOINT_PASO_MIN

    franjas.sort(key=lambda f: (_numero_pista(f["pista"]), f["pista"], f["_orden"]))
    for f in franjas:
        del f["_orden"]
    return franjas


def sietepadel_parsear(d: dict, fecha: date, tipo_pistas: str) -> list[dict]:
    """
    A diferencia del Polideportivo (huecos de duración variable), en 7Padel
    cada pista tiene un horario de franjas FIJAS de 90 min (como Padel
    Paiporta). La respuesta ya trae, por cada pista, qué franjas fijas
    están libres («HorariosFijos» con Clickable=true — el resto de
    HorariosFijos, con Clickable=false, no aporta nada nuevo: esas mismas
    horas ya aparecen ocupadas en «Ocupaciones», así que se ignoran) y
    cuáles están ocupadas («Ocupaciones», sea cual sea su tipo: reserva
    individual, partida abierta o clase — todas cuentan como ocupada).
    """
    if _mp_fecha(d.get("StrFecha")) != fecha:
        raise DiaNoDisponible(
            f"La web ha devuelto datos del {d.get('StrFecha')!r} en vez del {fecha.isoformat()}."
        )
    if d.get("FechaInhabil"):
        raise DiaNoDisponible(f"El {fecha.isoformat()} figura como día inhábil (centro cerrado).")

    inicio_dia = datetime(fecha.year, fecha.month, fecha.day)
    ini_res_min, fin_res_min = _matchpoint_ventana_reservas(d, inicio_dia)

    franjas = []
    for i, col in enumerate(d.get("Columnas") or [], 1):
        nombre = _normalizar_nombre_pista((col.get("TextoPrincipal") or "").strip()) or f"Pista {i}"

        for h in col.get("HorariosFijos") or []:
            if not h.get("Clickable"):
                continue  # esa hora ya sale ocupada en "Ocupaciones"; no se duplica
            ini_min = _mp_minutos(h.get("StrHoraInicio"))
            if ini_min is None:
                raise EstructuraCambiada(
                    f"Un horario fijo de «{nombre}» no tiene hora de inicio legible "
                    f"({h.get('StrHoraInicio')!r}). Probablemente la web ha cambiado su formato."
                )
            libre = True
            if ini_res_min is not None and ini_min < ini_res_min:
                libre = False  # ya ha pasado esa hora hoy
            if fin_res_min is not None and ini_min >= fin_res_min:
                libre = False  # todavía no se abren reservas para esa hora
            franjas.append({
                "pista": nombre,
                "tipo": tipo_pistas,
                "hora_inicio": h.get("StrHoraInicio"),
                "hora_fin": h.get("StrHoraFin"),
                "libre": libre,
                "_orden": ini_min,
            })

        for o in col.get("Ocupaciones") or []:
            ini_min = _mp_minutos(o.get("StrHoraInicio"))
            if ini_min is None or o.get("StrHoraFin") is None:
                raise EstructuraCambiada(
                    f"Una ocupación de «{nombre}» no tiene horas legibles "
                    f"(inicio={o.get('StrHoraInicio')!r}, fin={o.get('StrHoraFin')!r}). "
                    "Probablemente la web ha cambiado su formato."
                )
            franjas.append({
                "pista": nombre,
                "tipo": tipo_pistas,
                "hora_inicio": o.get("StrHoraInicio"),
                "hora_fin": o.get("StrHoraFin"),
                "libre": False,
                "_orden": ini_min,
            })

    franjas.sort(key=lambda f: (_numero_pista(f["pista"]), f["pista"], f["_orden"]))
    for f in franjas:
        del f["_orden"]
    return franjas


def _matchpoint_dia_valido(d: dict, fecha: date) -> None:
    """Comprobaciones comunes a los tres analizadores de Matchpoint."""
    if _mp_fecha(d.get("StrFecha")) != fecha:
        raise DiaNoDisponible(
            f"La web ha devuelto datos del {d.get('StrFecha')!r} en vez del {fecha.isoformat()}."
        )
    if d.get("FechaInhabil"):
        raise DiaNoDisponible(f"El {fecha.isoformat()} figura como día inhábil (centro cerrado).")


def _matchpoint_apertura_cierre(d: dict, inicio_dia: datetime) -> tuple[int, int]:
    """Minutos de apertura y cierre del centro, relativos a la medianoche del día consultado."""
    def a_minutos(dt: datetime) -> int:
        return int((dt - inicio_dia).total_seconds() // 60)

    apertura = _mp_fecha_hora(d.get("StrHoraAperturaCentro"))
    cierre = _mp_fecha_hora(d.get("StrHoraCierreCentro"))
    if apertura and cierre:
        ap_min, ci_min = a_minutos(apertura), a_minutos(cierre)
    else:
        ap_min, ci_min = _mp_minutos(d.get("StrHoraInicio")), _mp_minutos(d.get("StrHoraFin"))
    if ap_min is None or ci_min is None or ci_min <= ap_min:
        raise EstructuraCambiada(
            "No se han podido leer las horas de apertura y cierre del centro "
            f"(apertura={d.get('StrHoraAperturaCentro')!r}, cierre={d.get('StrHoraCierreCentro')!r}). "
            "Probablemente la web ha cambiado su formato. No se han publicado datos de este día."
        )
    return ap_min, ci_min


def matchpoint_parsear(d: dict, fecha: date, tipo_pistas: str) -> list[dict]:
    """Convierte la respuesta de ObtenerCuadro en franjas (hora de inicio + libre/ocupada)."""
    _matchpoint_dia_valido(d, fecha)
    inicio_dia = datetime(fecha.year, fecha.month, fecha.day)
    ap_min, ci_min = _matchpoint_apertura_cierre(d, inicio_dia)
    ini_res_min, fin_res_min = _matchpoint_ventana_reservas(d, inicio_dia)

    franjas = []
    for i, col in enumerate(d.get("Columnas") or [], 1):
        nombre = (col.get("TextoPrincipal") or "").strip() or f"Pista {i}"


        ocupadas = []
        for oc in col.get("Ocupaciones") or []:
            ini = _mp_minutos(oc.get("StrHoraInicio"))
            fin = _mp_minutos(oc.get("StrHoraFin"))
            if fin == 0 and ini:  # una reserva que termina a las 00:00
                fin = 24 * 60
            if ini is None or fin is None or fin <= ini:
                raise EstructuraCambiada(
                    f"Una ocupación de «{nombre}» no tiene horas legibles "
                    f"(inicio={oc.get('StrHoraInicio')!r}, fin={oc.get('StrHoraFin')!r}). "
                    "No se han publicado datos de este día."
                )
            ocupadas.append((ini, fin))

        t = ap_min
        while t + MATCHPOINT_DURACION_MIN <= ci_min:
            fin_t = t + MATCHPOINT_DURACION_MIN
            libre = not any(t < fin and fin_t > ini for ini, fin in ocupadas)
            if ini_res_min is not None and t < ini_res_min:
                libre = False  # ya ha pasado: no se puede reservar
            if fin_res_min is not None and fin_t > fin_res_min:
                libre = False  # aún no se admiten reservas para esa hora

            duraciones_posibles = None
            if libre:
                # Hasta dónde llega el hueco de verdad: lo limita lo que venga
                # antes entre el cierre del centro, el fin de la ventana de
                # reservas, o el inicio de la siguiente reserva REAL (no el
                # siguiente marca de 30 min descartada del grid, que es otra
                # cosa: ver el caso "08:30 libre, 09:00 tachada" comentado
                # más abajo, en la constante MATCHPOINT_DURACION_MIN).
                limite = ci_min
                if fin_res_min is not None:
                    limite = min(limite, fin_res_min)
                siguientes_inicios = [ini for ini, _ in ocupadas if ini > t]
                if siguientes_inicios:
                    limite = min(limite, min(siguientes_inicios))
                duracion_hueco = limite - t
                # Solo se puede reservar en bloques de 60 o 90 min. El hueco
                # ya garantiza al menos 60 (es la condición del bucle); si
                # además da para 90, esa también es una opción VÁLIDA A LA
                # VEZ (no en vez de 60) — el jugador elige cuál reservar.
                duraciones_posibles = [60, 90] if duracion_hueco >= 90 else [60]

            franjas.append({
                "pista": nombre,
                "tipo": tipo_pistas,
                "hora_inicio": _mp_hhmm(t),
                "hora_fin": _mp_hhmm(fin_t),
                "libre": libre,
                "duraciones_posibles": duraciones_posibles,
            })
            t += MATCHPOINT_PASO_MIN
    return franjas


class MatchpointClient:
    """
    Mantiene una sesión y una «key» para varias consultas seguidas (como hace
    el navegador al cambiar de día sin recargar la página), y las renueva una
    vez si el servidor devuelve una respuesta vacía.
    """

    def __init__(self, base_url: str, id_cuadro: str, tipo_pistas: str, parser=None):
        self.base = base_url.rstrip("/")
        self.grid_url = f"{self.base}/Booking/Grid.aspx"
        self.api_url = f"{self.base}/booking/srvc.aspx/ObtenerCuadro"
        self.id_cuadro = id_cuadro
        self.tipo_pistas = tipo_pistas
        self.parser = parser or matchpoint_parsear
        self.sesion = None
        self.claves: list = []

    def _cabeceras(self, api: bool) -> dict:
        # Las mismas que envía un navegador (idioma incluido: sin él, el servidor
        # puede leer las fechas d/m/aaaa en formato inglés), pero nos seguimos
        # identificando con nuestro propio nombre.
        cab = {"User-Agent": HEADERS["User-Agent"], "Accept-Language": "es-ES,es;q=0.9"}
        if api:
            cab.update({
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Origin": self.base,
                "Referer": self.grid_url,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/json; charset=UTF-8",
            })
        return cab

    def _iniciar(self) -> None:
        self.sesion, self.claves = None, []
        sesion = requests.Session()
        resp = sesion.get(self.grid_url, headers=self._cabeceras(False), timeout=15)
        resp.raise_for_status()
        claves = _matchpoint_claves(resp.text)
        if not claves:
            raise EstructuraCambiada(
                "No se ha encontrado la clave de acceso («key») en la página principal de reservas. "
                "Probablemente la web ha cambiado su estructura."
            )
        self.sesion, self.claves = sesion, claves

    def _pedir(self, fecha: date) -> dict:
        ultimo: dict = {}
        for clave in self.claves[:3]:
            cuerpo = {"idCuadro": self.id_cuadro, "fecha": f"{fecha.day}/{fecha.month}/{fecha.year}", "key": clave}
            resp = self.sesion.post(self.api_url, headers=self._cabeceras(True), data=json.dumps(cuerpo), timeout=15)
            resp.raise_for_status()
            datos = resp.json()["d"]
            ultimo = datos
            if datos.get("Columnas"):
                return datos
        return ultimo

    def snapshot(self, fecha: date) -> list[dict]:
        if self.sesion is None:
            self._iniciar()
        datos = self._pedir(fecha)
        if not datos.get("Columnas") and not datos.get("FechaInhabil"):
            self._iniciar()  # un reintento con sesión y clave nuevas
            datos = self._pedir(fecha)
        if not datos.get("Columnas"):
            if datos.get("FechaInhabil"):
                raise DiaNoDisponible(f"El {fecha.isoformat()} figura como día inhábil (centro cerrado).")
            raise EstructuraCambiada(
                "La API ha devuelto una respuesta vacía, incluso tras renovar la sesión "
                f"(Nombre={datos.get('Nombre')!r}, StrFecha={datos.get('StrFecha')!r}, "
                f"TieneClienteAcceso={datos.get('TieneClienteAcceso')!r}). Puede que el servidor "
                "rechace la clave o el formato de la fecha, o que la web haya cambiado."
            )
        return self.parser(datos, fecha, self.tipo_pistas)


# ----------------------------------------------------------------------
# TRANSFORMAR AL FORMATO DE LA WEB
# ----------------------------------------------------------------------

def normalizar_hora(h: str) -> str:
    partes = h.split(":")
    return f"{int(partes[0]):02d}:{partes[1]}"


_RE_NUM_PISTA = re.compile(r"(\d+)")


def _numero_pista(nombre: str) -> float:
    """Para ordenar «Pista 2» antes que «Pista 10» (si se ordenara como
    texto, «10» iría antes que «2» porque el carácter '1' es menor que '2')."""
    m = _RE_NUM_PISTA.search(nombre)
    return int(m.group(1)) if m else float("inf")


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
        pista_dict = {
            "pista": nombre_limpio,
            "tipo": tipo,
            "slots": slots,
            "ocupadas": ocupadas,
        }
        duraciones = {}
        for f in lista:
            dur = _duracion_franja_min(f)
            if dur:
                duraciones[normalizar_hora(f["hora_inicio"])] = dur
        if duraciones:
            pista_dict["duraciones"] = duraciones
        pistas.append(pista_dict)
    pistas.sort(key=lambda p: (_numero_pista(p["pista"]), p["pista"]))
    return pistas


def _duracion_franja_min(f: dict) -> list[int] | None:
    """Duración (en minutos) de una franja libre, como lista de opciones.
    Los clubes de "hueco variable" (ver MatchpointClient) ya traen esto
    calculado a partir de las reservas reales («duraciones_posibles»); para
    el resto (franjas de duración fija), se calcula de la diferencia entre
    hora_inicio y hora_fin."""
    if not f.get("libre"):
        return None
    if "duraciones_posibles" in f:
        return f["duraciones_posibles"] or None
    ini = _mp_minutos(f.get("hora_inicio"))
    fin = _mp_minutos(f.get("hora_fin"))
    if ini is None or fin is None:
        return None
    if fin <= ini:
        fin += 24 * 60  # la franja cruza la medianoche (p. ej. 23:00 -> 00:30)
    return [fin - ini]




# Cuántas pistas esperamos como mínimo en un día normal. Si un club devuelve
# menos de esto SIN dar ningún error de conexión, es señal de que algo ha
# cambiado en su web (y el scraper ya no la está leyendo bien).
PISTAS_MINIMAS_ESPERADAS = {
    "Padel Paiporta": 4,             # normalmente hay 6 (5 + exterior)
    "Tu Padel Valencia — Picanya": 4,  # normalmente hay 5
    "Padel Sedaví": 5,               # en el HTML se ven al menos 8 columnas de pista
    FHCV_CLUB: 4,                    # el club tiene 5 pistas en total (confirmado 28-09-2026)
    SIETEPADEL_CLUB: 8,              # el club tiene 11 pistas en total (confirmado 28-09-2026)
    ONEPADEL_CLUB: 12,                # el club tiene 15 pistas en total (confirmado 01-10-2026)
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
      - Si ese día en concreto no está disponible por un motivo CONOCIDO y
        esperado (día inhábil, fuera del rango que ofrece la web...): (None, None).
        Este es el ÚNICO caso en el que se devuelve (None, None); generar_datos()
        se apoya en eso para no confundirlo con un club que da la callada por
        respuesta sin explicación.
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
    fhcv = MatchpointClient(FHCV_BASE_URL, FHCV_ID_CUADRO, FHCV_TIPO_PISTAS)
    sietepadel = MatchpointClient(
        SIETEPADEL_BASE_URL, SIETEPADEL_ID_CUADRO, SIETEPADEL_TIPO_PISTAS,
        parser=sietepadel_parsear,
    )
    onepadel = MatchpointClient(
        ONEPADEL_BASE_URL, ONEPADEL_ID_CUADRO, ONEPADEL_TIPO_PISTAS,
        parser=matchpoint_combinado_parsear,
    )
    # Clubes que en algún día han dado (None, None): ese día en
    # concreto no está disponible por un motivo YA EXPLICADO (ver
    # docstring de procesar_club), así que no cuentan como "sospechosos".
    clubs_con_ausencia_explicada: set[str] = set()

    for i in range(DIAS_A_CONSULTAR):
        fecha = hoy() + timedelta(days=i)
        fecha_str = fecha.isoformat()
        clubs_del_dia = []

        club_paiporta, error_paiporta = procesar_club(
            "Padel Paiporta", "Paiporta, Valencia", "https://www.padelpaiporta.com/",
            paiporta_snapshot, fecha,
        )
        if club_paiporta is None and error_paiporta is None:
            clubs_con_ausencia_explicada.add("Padel Paiporta")
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
        if club_tupadel is None and error_tupadel is None:
            clubs_con_ausencia_explicada.add("Tu Padel Valencia — Picanya")
        if club_tupadel:
            clubs_del_dia.append(club_tupadel)
        if error_tupadel:
            print(f"[AVISO] {error_tupadel['club']} ({fecha_str}): {error_tupadel['mensaje']}")
            errores.append(error_tupadel)

        club_sedavi, error_sedavi = procesar_club(
            "Padel Sedaví", "Sedaví, Valencia", SEDAVI_BASE_URL,
            sedavi_snapshot, fecha,
        )
        if club_sedavi is None and error_sedavi is None:
            clubs_con_ausencia_explicada.add("Padel Sedaví")
        if club_sedavi:
            clubs_del_dia.append(club_sedavi)
        if error_sedavi:
            print(f"[AVISO] {error_sedavi['club']} ({fecha_str}): {error_sedavi['mensaje']}")
            errores.append(error_sedavi)

        club_fhcv, error_fhcv = procesar_club(
            FHCV_CLUB, FHCV_ZONA, f"{FHCV_BASE_URL}/Booking/Grid.aspx",
            fhcv.snapshot, fecha,
        )
        if club_fhcv:
            # A diferencia de los demás clubes (franjas de duración fija), aquí
            # cada franja libre solo garantiza un mínimo de 60 min: puede que
            # en realidad haya más tiempo libre a continuación, o puede que no.
            # Con esta marca, la web calcula y muestra cuánto hay de verdad.
            club_fhcv["pasos_de_30min"] = True
        if club_fhcv is None and error_fhcv is None:
            clubs_con_ausencia_explicada.add(FHCV_CLUB)
        if club_fhcv:
            clubs_del_dia.append(club_fhcv)
        if error_fhcv:
            print(f"[AVISO] {error_fhcv['club']} ({fecha_str}): {error_fhcv['mensaje']}")
            errores.append(error_fhcv)

        time.sleep(3)

        club_sietepadel, error_sietepadel = procesar_club(
            SIETEPADEL_CLUB, SIETEPADEL_ZONA, f"{SIETEPADEL_BASE_URL}/Booking/Grid.aspx",
            sietepadel.snapshot, fecha,
        )
        if club_sietepadel is None and error_sietepadel is None:
            clubs_con_ausencia_explicada.add(SIETEPADEL_CLUB)
        if club_sietepadel:
            clubs_del_dia.append(club_sietepadel)
        if error_sietepadel:
            print(f"[AVISO] {error_sietepadel['club']} ({fecha_str}): {error_sietepadel['mensaje']}")
            errores.append(error_sietepadel)

        time.sleep(3)

        club_onepadel, error_onepadel = procesar_club(
            ONEPADEL_CLUB, ONEPADEL_ZONA, f"{ONEPADEL_BASE_URL}/Booking/Grid.aspx",
            onepadel.snapshot, fecha,
        )
        if club_onepadel is None and error_onepadel is None:
            clubs_con_ausencia_explicada.add(ONEPADEL_CLUB)
        if club_onepadel:
            clubs_del_dia.append(club_onepadel)
        if error_onepadel:
            print(f"[AVISO] {error_onepadel['club']} ({fecha_str}): {error_onepadel['mensaje']}")
            errores.append(error_onepadel)

        resultado_por_fecha[fecha_str] = clubs_del_dia
        time.sleep(3)

    # --- Avisos globales, una sola vez por ejecución ---
    if sedavi_clases_desconocidas:
        errores.append({
            "club": "Padel Sedaví",
            "fecha": hoy().isoformat(),
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
            "fecha": hoy().isoformat(),
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
        if nombre in clubs_con_ausencia_explicada:
            continue  # su ausencia ya tiene una explicación conocida (ver arriba)
        if nombre not in clubs_con_datos and nombre not in clubs_con_error:
            errores.append({
                "club": nombre,
                "fecha": hoy().isoformat(),
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
            headers={"Title": f"Pistalo - {len(errores)} problema(s) detectado(s)", "Priority": "high"},
            timeout=10,
        )
        print(f"[ALERTA] Notificación enviada a ntfy.sh/{NTFY_TOPIC}")
    except Exception as e:
        # Enviar la notificación es "mejor esfuerzo": si falla por lo que sea
        # (red, codificación de caracteres, lo que no hayamos previsto...),
        # nunca debe tirar abajo el resto de la ejecución. El fallo real de
        # scraping ya se ha guardado en el JSON y se ha impreso arriba.
        print(f"[AVISO] No se pudo enviar la notificación de alerta: {type(e).__name__}: {e}")


if __name__ == "__main__":
    from datetime import timezone

    try:
        datos, errores = generar_datos()

        os.makedirs("data", exist_ok=True)
        salida = {
            "actualizado_en": hoy().isoformat(),
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
            "fecha": hoy().isoformat(),
            "tipo": "error_inesperado",
            "mensaje": f"El script ha fallado por completo ({type(e).__name__}: {e}). No se ha actualizado ningún dato esta vez.",
        }])
        raise  # que la ejecución de GitHub Actions también quede marcada como fallida
