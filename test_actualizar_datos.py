"""
Batería de tests de regresión para Pistalo
===========================================

Cubre los 7 clubes que se muestran hoy en pistalo.com:

  1. 7Padel Valencia          (sietepadel_parsear)
  2. One Pádel Valencia       (matchpoint_combinado_parsear)
  3. Origen Pádel Club        (matchpoint_combinado_parsear)
  4. Padel Paiporta           (paiporta_parsear)
  5. Padel Sedaví             (sedavi_parsear)
  6. Polideportivo Beteró     (matchpoint_parsear)
  7. Tu Pádel Valencia        (tupadel_parsear)

Cada caso de este archivo reproduce una situación REAL confirmada por el
cliente, o un comportamiento documentado del propio scraper — no hay datos
inventados "a ojo". Donde el dato viene de un cURL/JSON real, se indica la
fecha en que se confirmó.

NORMA DEL PROYECTO: antes de cambiar cualquier regla de negocio en
actualizar_datos.py, añade aquí el caso real que la motiva, y comprueba que
sigue pasando TODO este archivo antes de publicar el cambio. Si una regla
nueva contradice un test ya existente, es señal de que hay que revisar
ambos con más datos reales antes de decidir cuál es el correcto — nunca se
borra un test para que "pase" sin entender antes por qué chocaba.

Ejecutar con:  python3 test_actualizar_datos.py
(No necesita pytest ni nada fuera de la librería estándar.)
"""

from __future__ import annotations

import sys
import unittest
from datetime import date

sys.path.insert(0, ".")
from actualizar_datos import (
    MatchpointClient,
    matchpoint_combinado_parsear,
    matchpoint_parsear,
    sietepadel_parsear,
    paiporta_parsear,
    tupadel_parsear,
    sedavi_parsear,
    agrupar_por_pista,
    _numero_pista,
    _normalizar_nombre_pista,
    DiaNoDisponible,
    EstructuraCambiada,
)

FECHA = date(2026, 11, 11)
STR_FECHA = "11/11/2026"


def _dia_matchpoint(columnas, apertura="08:00", cierre="12/11/2026 00:00",
                     ini_res=None, fin_res=None, fecha_inhabil=False):
    """Fabrica el objeto "d" que devuelve ObtenerCuadro, con valores por
    defecto que no interfieren en los tests (ventana de reservas ya abierta
    y sin límite práctico, salvo que el test diga lo contrario)."""
    return {
        "StrFecha": STR_FECHA,
        "FechaInhabil": fecha_inhabil,
        "StrHoraAperturaCentro": f"{STR_FECHA} {apertura}",
        "StrHoraCierreCentro": cierre,
        "StrFechaHoraInicioReservas": ini_res or f"{STR_FECHA} 00:01",
        "StrFechaHoraFinReservas": fin_res or "27/11/2026 00:00",
        "Columnas": columnas,
    }


def _col_combinado(nombre, fijos=(), ocup=(), combina=True):
    return {
        "TextoPrincipal": nombre,
        "CombinaHorariosFijosYLibres": combina,
        "HorariosFijos": [
            {"StrHoraInicio": a, "StrHoraFin": b, "Clickable": True} for a, b in fijos
        ],
        "Ocupaciones": [{"StrHoraInicio": a, "StrHoraFin": b} for a, b in ocup],
    }


def _col_sietepadel(nombre, fijos_clickable=(), fijos_ocupados=(), ocup=()):
    fijos = [
        {"StrHoraInicio": a, "StrHoraFin": b, "Clickable": True} for a, b in fijos_clickable
    ] + [
        {"StrHoraInicio": a, "StrHoraFin": b, "Clickable": False} for a, b in fijos_ocupados
    ]
    return {
        "TextoPrincipal": nombre,
        "HorariosFijos": fijos,
        "Ocupaciones": [{"StrHoraInicio": a, "StrHoraFin": b} for a, b in ocup],
    }


def _franja(franjas, pista, hhmm):
    for f in franjas:
        if f["pista"] == pista and f["hora_inicio"] == hhmm:
            return f
    return None


# ----------------------------------------------------------------------
# 1) 7PADEL VALENCIA — sietepadel_parsear (franjas fijas de 90 min)
# ----------------------------------------------------------------------
class Test7Padel(unittest.TestCase):
    def test_franja_fija_clickable_es_libre(self):
        d = _dia_matchpoint([
            _col_sietepadel("Pista 1", fijos_clickable=[("09:00", "10:30")]),
        ])
        f = sietepadel_parsear(d, FECHA, "Cubierta")
        self.assertTrue(_franja(f, "Pista 1", "09:00")["libre"])

    def test_franja_fija_no_clickable_no_se_duplica_pero_ocupacion_si_sale(self):
        # Clickable=False no aporta nada (ya sale en Ocupaciones) — no debe
        # generar una franja "libre" fantasma.
        d = _dia_matchpoint([
            _col_sietepadel("Pista 1", fijos_ocupados=[("09:00", "10:30")],
                             ocup=[("09:00", "10:30")]),
        ])
        f = sietepadel_parsear(d, FECHA, "Cubierta")
        coincidencias = [x for x in f if x["pista"] == "Pista 1" and x["hora_inicio"] == "09:00"]
        self.assertEqual(len(coincidencias), 1)
        self.assertFalse(coincidencias[0]["libre"])

    def test_pista_10_ordena_despues_de_pista_2_no_alfabeticamente(self):
        # Bug real corregido: "Pista 10" alfabéticamente iría antes que
        # "Pista 2". _numero_pista debe ordenarlas numéricamente.
        self.assertLess(_numero_pista("Pista 2"), _numero_pista("Pista 10"))

    def test_nombre_pista_con_doble_espacio_se_normaliza(self):
        # Bug real: la web mandaba "Pista  9" (doble espacio).
        self.assertEqual(_normalizar_nombre_pista("Pista  9"), "Pista 9")


# ----------------------------------------------------------------------
# 2) ONE PÁDEL VALENCIA + 3) ORIGEN PÁDEL CLUB — matchpoint_combinado_parsear
# ----------------------------------------------------------------------
class TestMatchpointCombinado(unittest.TestCase):
    # --- Confirmado en vivo 11/11/2026: Origen Pádel Club, Pista 5 Exterior ---
    # JSON real: HorariosFijos=[], Ocupaciones=[17:30-21:30], apertura 08:00,
    # cierre medianoche. El cliente probó cada hora en la web real:
    #   21:30 -> SÍ (60 o 90 min)   22:00 -> NO   22:30 -> NO   23:00 -> SÍ (60 min)
    def test_origen_pista5_rejilla_90min_anclada_a_apertura(self):
        d = _dia_matchpoint([
            _col_combinado("Pista 5 EXTERIOR", fijos=[], ocup=[("17:30", "21:30")], combina=False),
        ])
        f = matchpoint_combinado_parsear(d, FECHA, "Indoor")
        self.assertTrue(_franja(f, "Pista 5", "21:30")["libre"])
        self.assertEqual(_franja(f, "Pista 5", "21:30")["duraciones_posibles"], [60, 90])
        self.assertIsNone(_franja(f, "Pista 5", "22:00"))
        self.assertIsNone(_franja(f, "Pista 5", "22:30"))
        self.assertTrue(_franja(f, "Pista 5", "23:00")["libre"])
        self.assertEqual(_franja(f, "Pista 5", "23:00")["duraciones_posibles"], [60])

    # --- Confirmado con JSON real 11/11/2026: One Pádel, Pista 13 ---
    # Fijos: 15:30-17:00, 17:00-18:30, 21:30-23:00. Ocupación: 19:30-21:00.
    # CombinaHorariosFijosYLibres=true. El hueco libre 18:30-19:30 (60 min,
    # pegado al FIN de un fijo pero a una RESERVA por delante, no un fijo ni
    # el cierre) NO se puede reservar en la web real.
    def test_onepadel_pista13_hueco_ajustado_hacia_otra_reserva_se_rechaza(self):
        d = _dia_matchpoint([
            _col_combinado(
                "Pista 13",
                fijos=[("15:30", "17:00"), ("17:00", "18:30"), ("21:30", "23:00")],
                ocup=[("19:30", "21:00")],
                combina=True,
            ),
        ])
        f = matchpoint_combinado_parsear(d, FECHA, "Indoor")
        self.assertIsNone(_franja(f, "Pista 13", "18:30"))

    # --- Confirmado en vivo 12/11/2026: One Pádel, pistas con fijos ---
    # Pista 11: fijo 16:00-17:30, ocupaciones 18:00-19:30, 19:30-21:00,
    # 21:00-22:30, combina=true. Pistalo ofrecía 23:00/60 y la web real NO
    # deja reservar nada tras el último fijo (ni 22:30 ni 23:00). Igual en
    # Pistas 1,5,6,9,13. Con fijos solo son reservables los fijos.
    def test_onepadel_pista11_con_fijos_no_ofrece_huecos_libres(self):
        d = _dia_matchpoint([
            _col_combinado("Pista 11", fijos=[("16:00", "17:30")],
                           ocup=[("18:00", "19:30"), ("19:30", "21:00"), ("21:00", "22:30")],
                           combina=True),
        ])
        f = matchpoint_combinado_parsear(d, FECHA, "Indoor")
        self.assertIsNone(_franja(f, "Pista 11", "22:30"))
        self.assertIsNone(_franja(f, "Pista 11", "23:00"))

    # Confirmado en vivo 12/11/2026: One Pádel Pista 6, fijo 21:30-23:00 con
    # Clickable=false (solapa con una clase 16:00-22:00). La web lo marca
    # habilitado="false" y no deja reservar: no debe salir como libre.
    def test_onepadel_pista6_fijo_no_clickable_no_es_libre(self):
        col = _col_combinado("Pista 6", fijos=[("21:30", "23:00")],
                             ocup=[("16:00", "22:00")], combina=True)
        col["HorariosFijos"][0]["Clickable"] = False
        f = matchpoint_combinado_parsear(_dia_matchpoint([col]), FECHA, "Indoor")
        self.assertFalse(_franja(f, "Pista 6", "21:30")["libre"])
        self.assertIsNone(_franja(f, "Pista 6", "23:00"))

    def test_onepadel_pista9_tras_fijo_final_no_ofrece_23(self):
        d = _dia_matchpoint([
            _col_combinado("Pista 9", fijos=[("15:30", "17:00"), ("21:30", "23:00")],
                           ocup=[("12:30", "15:30"), ("17:00", "18:30"), ("18:30", "20:00"), ("20:00", "21:30")],
                           combina=True),
        ])
        f = matchpoint_combinado_parsear(d, FECHA, "Indoor")
        self.assertTrue(_franja(f, "Pista 9", "21:30")["libre"])
        self.assertIsNone(_franja(f, "Pista 9", "23:00"))

    # --- Confirmado en vivo 12/11/2026: Origen Pista 5 (sin fijos) ---
    # Ocupaciones 17:00-19:00 y 20:30-21:30. Web real: 19:00 -> SÍ (solo 90),
    # 21:30 -> 60 o 90, 22:00 NO, 22:30 NO, 23:00 -> 60, 23:30 NO.
    def test_origen_pista5_hueco_de_90_entre_reservas_ofrece_solo_90(self):
        d = _dia_matchpoint([
            _col_combinado("Pista 5 EXTERIOR", fijos=[],
                           ocup=[("17:00", "19:00"), ("20:30", "21:30")], combina=False),
        ])
        f = matchpoint_combinado_parsear(d, FECHA, "Indoor")
        self.assertEqual(_franja(f, "Pista 5", "19:00")["duraciones_posibles"], [90])
        self.assertEqual(_franja(f, "Pista 5", "21:30")["duraciones_posibles"], [60, 90])
        for h in ("19:30", "20:00", "22:00", "22:30", "23:30"):
            self.assertIsNone(_franja(f, "Pista 5", h), h)
        self.assertEqual(_franja(f, "Pista 5", "23:00")["duraciones_posibles"], [60])

    # --- Confirmado antes (histórico): Origen Pádel Club, Pista 1 (indoor) ---
    # CombinaHorariosFijosYLibres=false + tiene fijos -> nada reservable fuera
    # de las franjas fijas, por mucho que parezca libre (la web real no
    # muestra huecos después de las 21:30 en este caso).
    def test_combina_false_con_fijos_no_genera_huecos_libres(self):
        d = _dia_matchpoint([
            _col_combinado("Pista 1 MARTICO", fijos=[("10:30", "12:00")],
                            ocup=[("18:30", "20:00")], combina=False),
        ])
        f = matchpoint_combinado_parsear(d, FECHA, "Indoor")
        # Fuera de las franjas fijas (10:30-12:00) y de la ocupación
        # (18:30-20:00) no debe aparecer NADA con duraciones_posibles: esos
        # serían huecos libres fantasma. La propia franja fija SÍ debe
        # llevar duraciones_posibles (eso es correcto, no un hueco libre).
        cubiertas = {("10:30", "12:00"), ("18:30", "20:00")}
        huecos_libres_fantasma = [
            x for x in f
            if "duraciones_posibles" in x and (x["hora_inicio"], x["hora_fin"]) not in cubiertas
        ]
        self.assertEqual(huecos_libres_fantasma, [])
        self.assertIsNotNone(_franja(f, "Pista 1", "10:30"))

    def test_pista_outdoor_quita_la_palabra_del_nombre_y_marca_tipo_exterior(self):
        d = _dia_matchpoint([
            _col_combinado("Pista 13 OUTDOOR", fijos=[], ocup=[], combina=False),
        ])
        f = matchpoint_combinado_parsear(d, FECHA, "Indoor")
        # No debe haber franjas (día vacío), pero si las hubiera el nombre
        # no debe llevar "OUTDOOR" y el tipo debe ser Exterior.
        d2 = _dia_matchpoint([
            _col_combinado("Pista 13 OUTDOOR", fijos=[("10:00", "11:30")], combina=False),
        ])
        f2 = matchpoint_combinado_parsear(d2, FECHA, "Indoor")
        self.assertEqual(f2[0]["pista"], "Pista 13")
        self.assertEqual(f2[0]["tipo"], "Exterior")

    def test_duracion_de_franja_fija_se_incluye_para_mostrar_en_pistas_mixtas(self):
        # Bug real corregido: una franja fija dentro de una pista "mixta"
        # (con huecos libres también) se quedaba sin "duraciones_posibles",
        # así que agrupar_por_pista la excluía del diccionario "duraciones".
        d = _dia_matchpoint([
            _col_combinado("Pista 12", fijos=[("22:30", "00:00")], ocup=[("08:00", "22:30")]),
        ])
        f = matchpoint_combinado_parsear(d, FECHA, "Indoor")
        franja_fija = _franja(f, "Pista 12", "22:30")
        self.assertEqual(franja_fija["duraciones_posibles"], [90])


# ----------------------------------------------------------------------
# 4) POLIDEPORTIVO BETERÓ — matchpoint_parsear (huecos de duración variable)
# ----------------------------------------------------------------------
class TestBetero(unittest.TestCase):
    # Todos estos casos vienen del cURL real pedido al cliente el 11/11/2026
    # (centro: PADEL, apertura 08:30, cierre 23:30) y confirmados por él
    # haciendo clic en la web real.
    def _dia_betero(self, ocupaciones):
        return _dia_matchpoint([
            {"TextoPrincipal": "Pista", "Ocupaciones": [
                {"StrHoraInicio": a, "StrHoraFin": b} for a, b in ocupaciones
            ]},
        ], apertura="08:30", cierre=f"{STR_FECHA} 23:30")

    def test_hueco_de_90min_solo_ofrece_90_nunca_60(self):
        # Pista 1 y Pista 4 reales: último hueco 22:00-23:30 (90 min exactos).
        # Confirmado: solo 90 min a las 22:00, nada a las 22:30.
        d = self._dia_betero([("08:30", "22:00")])
        f = matchpoint_parsear(d, FECHA, "Cubierta")
        franja = _franja(f, "Pista", "22:00")
        self.assertTrue(franja["libre"])
        self.assertEqual(franja["duraciones_posibles"], [90])
        franja2230 = _franja(f, "Pista", "22:30")
        self.assertFalse(franja2230["libre"])

    def test_hueco_de_120min_se_parte_en_dos_de_60_nunca_90(self):
        # Pista 5 real: último hueco 21:30-23:30 (120 min). Confirmado:
        # 60 min a las 21:30 Y a las 22:30 (nunca 90 en ninguna de las dos).
        d = self._dia_betero([("08:30", "21:30")])
        f = matchpoint_parsear(d, FECHA, "Cubierta")
        franja_2130 = _franja(f, "Pista", "21:30")
        self.assertTrue(franja_2130["libre"])
        self.assertEqual(franja_2130["duraciones_posibles"], [60])
        franja_2200 = _franja(f, "Pista", "22:00")
        self.assertFalse(franja_2200["libre"])
        franja_2230 = _franja(f, "Pista", "22:30")
        self.assertTrue(franja_2230["libre"])
        self.assertEqual(franja_2230["duraciones_posibles"], [60])

    def test_hueco_grande_240min_19h_solo_90_1930_solo_60_20h_nada(self):
        # Pista 3 real: hueco libre 16:30-20:30 (240 min).
        # Confirmado: 19:00 -> 90 min, 19:30 -> 60 min, 20:00 -> nada.
        d = self._dia_betero([("08:30", "16:30"), ("20:30", "22:00")])
        f = matchpoint_parsear(d, FECHA, "Cubierta")
        self.assertEqual(_franja(f, "Pista", "19:00")["duraciones_posibles"], [90])
        self.assertEqual(_franja(f, "Pista", "19:30")["duraciones_posibles"], [60])
        self.assertFalse(_franja(f, "Pista", "20:00")["libre"])


# ----------------------------------------------------------------------
# 5) PADEL PAIPORTA — paiporta_parsear (HTML, franjas fijas)
# ----------------------------------------------------------------------
class TestPaiporta(unittest.TestCase):
    HTML_BASE = """
    <div id="partidas">
      <div class="float-left">
        <div class="partida rounded">
          <h2>PISTA 1</h2><span>9:00-10:30</span>
          <div class="rounded_b">
            <ul class="juegan">{juegan}</ul>
            <ul class="esperan">{esperan}</ul>
          </div>
        </div>
      </div>
    </div>
    """

    def _html(self, juegan="", esperan=""):
        li_vacios_j = "<li></li>" * (4 - juegan.count("<li>"))
        li_vacios_e = "<li></li>" * (4 - esperan.count("<li>"))
        return self.HTML_BASE.format(juegan=juegan + li_vacios_j, esperan=esperan + li_vacios_e)

    def test_pista_libre_cuando_juegan_y_esperan_vacios(self):
        f = paiporta_parsear(self._html())
        self.assertTrue(f[0]["libre"])

    def test_pista_ocupada_si_hay_alguien_en_juegan(self):
        f = paiporta_parsear(self._html(juegan="<li>Pepe</li>"))
        self.assertFalse(f[0]["libre"])

    def test_pista_ocupada_si_hay_alguien_en_esperan_aunque_juegan_este_vacio(self):
        f = paiporta_parsear(self._html(esperan="<li>Ana</li>"))
        self.assertFalse(f[0]["libre"])

    def test_pista_exterior_se_detecta_por_el_nombre(self):
        html = self.HTML_BASE.replace("PISTA 1", "PISTA EXTERIOR").format(juegan="<li></li>" * 4, esperan="<li></li>" * 4)
        f = paiporta_parsear(html)
        self.assertEqual(f[0]["tipo"], "Exterior")


# ----------------------------------------------------------------------
# 6) TU PÁDEL VALENCIA (Picanya) — tupadel_parsear (HTML)
# ----------------------------------------------------------------------
class TestTuPadel(unittest.TestCase):
    def _html(self, centro="PICAÑA", jugadores_li="", texto_evento="", esperan=""):
        return f"""
        <div class="marco_partida_padel">
          <div class="partidaNombrePista_padel">Pista 4 - {centro}</div>
          <div class="partida_padel">
            <div class="partidaHorario_padel">08:30 - 10:00</div>
            <div class="partidaJuegan_padel">
              <div class="partidaJuegaTitulo">Jugadores</div>
              <ul>{jugadores_li}</ul>
              {f'<div class="partidaNombre_padel">{texto_evento}</div>' if texto_evento else ''}
            </div>
            <div class="partidaEsperan_padel">
              <div class="partidaEsperanTitulo_padel">Esperan</div>
              <div class="partidaEsperanMarco_padel">{esperan}</div>
            </div>
          </div>
        </div>
        """

    def test_libre_cuando_todo_vacio(self):
        f = tupadel_parsear(self._html())
        self.assertTrue(f[0]["libre"])

    def test_ocupada_por_jugador_con_nombre(self):
        f = tupadel_parsear(self._html(jugadores_li='<li class="jugador_reservado_padel" title="milla93"></li>'))
        self.assertFalse(f[0]["libre"])

    def test_ocupada_por_clase_con_texto_suelto(self):
        f = tupadel_parsear(self._html(texto_evento="10h Esc Joshua"))
        self.assertFalse(f[0]["libre"])

    def test_centro_valencia_se_ignora_si_pedimos_picanya(self):
        f = tupadel_parsear(self._html(centro="VALENCIA"), centro_deseado="PICAÑA")
        self.assertEqual(f, [])


# ----------------------------------------------------------------------
# 7) PADEL SEDAVÍ — sedavi_parsear (HTML + lista de espera)
# ----------------------------------------------------------------------
class TestSedavi(unittest.TestCase):
    def _html(self, clases="", data_pista="1", data_hora="09:00", espera_n=0):
        href = f"/partidas/padel/{FECHA.isoformat()}#partida_1_09-00"
        boton = (
            f'<button class="lista" data-pista-id="{data_pista}" data-hora="{data_hora}">'
            f'Lista de espera <span class="count">{espera_n}</span></button>'
        )
        return f"""
        <li class="partida partida-deporte-padel {clases}">
          <a href="{href}">reservar</a>
        </li>
        {boton}
        """

    def test_libre_sin_clase_y_sin_gente_en_espera(self):
        f = sedavi_parsear(self._html(), FECHA)
        self.assertTrue(f[0]["libre"])

    def test_ocupada_por_clase_partida_reservada(self):
        f = sedavi_parsear(self._html(clases="partida-reservada"), FECHA)
        self.assertFalse(f[0]["libre"])

    def test_ocupada_por_clase_partida_abierta(self):
        # Clase añadida tras un bug real: "partida-abierta" no estaba en el
        # conjunto de clases conocidas y se contaba (mal) como libre.
        f = sedavi_parsear(self._html(clases="partida-abierta"), FECHA)
        self.assertFalse(f[0]["libre"])

    def test_libre_en_clases_pero_con_gente_en_lista_de_espera_pasa_a_ocupada(self):
        f = sedavi_parsear(self._html(espera_n=2), FECHA)
        self.assertFalse(f[0]["libre"])

    def test_sin_ningun_boton_de_espera_lanza_estructura_cambiada(self):
        html_sin_boton = """
        <li class="partida partida-deporte-padel">
          <a href="/partidas/padel/2026-11-11#partida_1_09-00">reservar</a>
        </li>
        """
        with self.assertRaises(EstructuraCambiada):
            sedavi_parsear(html_sin_boton, FECHA)

    def test_dia_distinto_al_pedido_lanza_dia_no_disponible(self):
        html = self._html().replace(FECHA.isoformat(), "2026-11-12")
        with self.assertRaises(DiaNoDisponible):
            sedavi_parsear(html, FECHA)


# ----------------------------------------------------------------------
# HELPERS COMPARTIDOS
# ----------------------------------------------------------------------
class TestHelpersCompartidos(unittest.TestCase):
    def test_numero_pista_ordena_numericamente(self):
        nombres = ["Pista 10", "Pista 2", "Pista 1"]
        nombres.sort(key=_numero_pista)
        self.assertEqual(nombres, ["Pista 1", "Pista 2", "Pista 10"])

    def test_agrupar_por_pista_no_incluye_duraciones_si_ninguna_franja_las_trae(self):
        # Clubes de franjas fijas (7Padel, Paiporta...) no deben llevar la
        # clave "duraciones" — se quitó a propósito tras pedirlo el cliente.
        franjas = [{"pista": "Pista 1", "tipo": "Cubierta", "hora_inicio": "09:00", "libre": True}]
        pistas = agrupar_por_pista(franjas)
        self.assertNotIn("duraciones", pistas[0])

    def test_agrupar_por_pista_incluye_duraciones_si_alguna_franja_las_trae(self):
        franjas = [
            {"pista": "Pista 1", "tipo": "Indoor", "hora_inicio": "21:30", "libre": True,
             "duraciones_posibles": [60, 90]},
        ]
        pistas = agrupar_por_pista(franjas)
        self.assertIn("duraciones", pistas[0])
        self.assertEqual(pistas[0]["duraciones"]["21:30"], [60, 90])


# ----------------------------------------------------------------------
# VERIFICACIÓN CONTRA EL SERVIDOR DEL CLUB (Origen)
# ----------------------------------------------------------------------
class _RespFalsa:
    def __init__(self, d): self._d = d
    def raise_for_status(self): pass
    def json(self): return {"d": self._d}


class _SesionFalsa:
    """Imita a ObtenerInformacionHuecoLibre de Origen (confirmado 12/11/2026):
    Pista 5: 19:00 -> 90; 21:30 -> 60 y 90; 23:00 -> 60; el resto sin opciones."""
    VALIDOS = {"19:00": ["90min Online"], "21:30": ["60min Online", "90min Online"], "23:00": ["60min Online"]}

    def __init__(self): self.horas = []

    def post(self, url, headers=None, data=None, timeout=None):
        import json as _j
        c = _j.loads(data)
        self.horas.append(c["hora"])
        ops = [{"Token": "x", "Descripcion": t} for t in self.VALIDOS.get(c["hora"], [])]
        return _RespFalsa({"Opciones": ops})


class TestVerificacionServidor(unittest.TestCase):
    def _cliente(self):
        c = MatchpointClient("https://x.example", "4", "Indoor",
                             parser=matchpoint_combinado_parsear, verificar_huecos=True)
        c.sesion, c.clave_valida = _SesionFalsa(), "K"
        return c

    def test_modo_denso_propone_candidatos_cada_30_min(self):
        d = _dia_matchpoint([{**_col_combinado("Pista 5 EXTERIOR", ocup=[("17:00", "19:00"), ("20:30", "21:30")], combina=False),
                              "Id": "17", "IdModalidadFijaParaReservas": 8}])
        f = matchpoint_combinado_parsear(d, FECHA, "Indoor", candidatos_densos=True)
        horas = {x["hora_inicio"] for x in f if x.get("_verif")}
        self.assertTrue({"19:00", "19:30", "22:00", "22:30", "23:00"} <= horas)
        self.assertEqual(next(x for x in f if x.get("_verif"))["_verif"], ("17", 8))

    def test_solo_se_muestra_lo_que_el_servidor_permite(self):
        d = _dia_matchpoint([{**_col_combinado("Pista 5 EXTERIOR", ocup=[("17:00", "19:00"), ("20:30", "21:30")], combina=False),
                              "Id": "17", "IdModalidadFijaParaReservas": 8}])
        c = self._cliente()
        f = matchpoint_combinado_parsear(d, FECHA, "Indoor", candidatos_densos=True)
        libres = {x["hora_inicio"]: x["duraciones_posibles"] for x in c._verificar(f, FECHA) if x["libre"]}
        self.assertEqual(libres, {"19:00": [90], "21:30": [60, 90], "23:00": [60]})

    def test_fijos_no_se_consultan_y_se_conservan(self):
        d = _dia_matchpoint([{**_col_combinado("Pista 3", fijos=[("22:00", "23:30")], combina=True),
                              "Id": "15", "IdModalidadFijaParaReservas": 5}])
        c = self._cliente()
        f = c._verificar(matchpoint_combinado_parsear(d, FECHA, "Indoor", candidatos_densos=True), FECHA)
        self.assertTrue(_franja(f, "Pista 3", "22:00")["libre"])
        self.assertNotIn("22:00", c.sesion.horas)  # el fijo no se consulta
        self.assertIn("08:30", c.sesion.horas)  # también se prueba fuera de la rejilla de 90

    # Confirmado en vivo 12/11/2026 pinchando el hueco en One Pádel: a las
    # 23:00 se reserva (solo 60) en Pistas 1,5,6,9,13 y NO en la 11.
    def test_onepadel_23h_decide_el_servidor_pista9_si_pista11_no(self):
        class Sesion(_SesionFalsa):
            def post(self, url, headers=None, data=None, timeout=None):
                import json as _j
                c = _j.loads(data)
                ok = c["idRecurso"] == "21" and c["hora"] == "23:00"  # 21 = Pista 9
                return _RespFalsa({"Opciones": [{"Descripcion": "60min Online"}] if ok else []})
        d = _dia_matchpoint([
            {**_col_combinado("Pista 9", fijos=[("15:30", "17:00"), ("21:30", "23:00")],
                              ocup=[("12:30", "15:30"), ("17:00", "21:30")], combina=True),
             "Id": "21", "IdModalidadFijaParaReservas": 4},
            {**_col_combinado("Pista 11", fijos=[("16:00", "17:30")],
                              ocup=[("18:00", "22:30")], combina=True),
             "Id": "23", "IdModalidadFijaParaReservas": 4},
        ])
        c = self._cliente()
        c.sesion = Sesion()
        f = c._verificar(matchpoint_combinado_parsear(d, FECHA, "Indoor", candidatos_densos=True), FECHA)
        self.assertEqual(_franja(f, "Pista 9", "23:00")["duraciones_posibles"], [60])
        self.assertIsNone(_franja(f, "Pista 11", "23:00"))
        self.assertTrue(_franja(f, "Pista 9", "21:30")["libre"])  # el fijo se conserva

    # Beteró (idCuadro 3, idmodalidad 49), 12/11/2026: mismo endpoint. Hueco
    # 22:00-24:00 libre: el servidor solo permite 90 a las 22:00.
    def test_betero_modo_denso_y_servidor_decide(self):
        d = _dia_matchpoint([{
            "TextoPrincipal": "Pista 5", "Id": "6", "IdModalidadFijaParaReservas": 49,
            "HorariosFijos": [], "Ocupaciones": [{"StrHoraInicio": "08:00", "StrHoraFin": "22:00"}],
        }])
        c = MatchpointClient("https://x.example", "3", "Outdoor", verificar_huecos=True)
        c.sesion, c.clave_valida = _SesionFalsa(), "K"
        c.sesion.VALIDOS = {"22:00": ["90min Online"]}
        f = matchpoint_parsear(d, FECHA, "Outdoor", candidatos_densos=True)
        self.assertEqual(c.sesion.horas, [])
        libres = {x["hora_inicio"]: x["duraciones_posibles"] for x in c._verificar(f, FECHA) if x["libre"]}
        self.assertEqual(libres, {"22:00": [90]})
        self.assertEqual(c.sesion.horas, ["22:00", "22:30", "23:00"])

    # Confirmado en vivo 12/11/2026 (para el 13/11): One Pádel Lacoste (Pista 1)
    # permite reservar a las 08:30 (60 min) en el hueco 08:00-09:30, que no
    # está en la rejilla de 90 min. Hay que proponerlo al servidor.
    def test_onepadel_pista1_0830_se_propone_al_servidor(self):
        class Sesion(_SesionFalsa):
            def post(self, url, headers=None, data=None, timeout=None):
                import json as _j
                c = _j.loads(data)
                self.horas.append(c["hora"])
                ok = {"08:00": ["60min Online", "90min Online"], "08:30": ["60min Online"]}.get(c["hora"], [])
                return _RespFalsa({"Opciones": [{"Descripcion": t} for t in ok]})
        d = _dia_matchpoint([
            {**_col_combinado("P. Lacoste", fijos=[("15:30", "17:00")], ocup=[("09:30", "11:00")], combina=True),
             "Id": "13", "IdModalidadFijaParaReservas": 7},
        ])
        c = self._cliente()
        c.sesion = Sesion()
        f = c._verificar(matchpoint_combinado_parsear(d, FECHA, "Indoor", candidatos_densos=True), FECHA)
        self.assertEqual(_franja(f, "Pista 1", "08:30")["duraciones_posibles"], [60])
        self.assertEqual(_franja(f, "Pista 1", "08:00")["duraciones_posibles"], [60, 90])

    # Confirmado en vivo 12/11/2026: Origen Pistas 2 y 4 aparecían libres a las
    # 08:00 y la web no deja reservar. Traen fijos y CombinaHorariosFijosYLibres
    # =false: no se debe ni proponer ni mostrar ningún hueco libre.
    def test_origen_pistas_con_fijos_y_combina_false_no_proponen_huecos(self):
        d = _dia_matchpoint([
            {**_col_combinado("Pista 2 JD", fijos=[("09:30", "11:00")], ocup=[("20:00", "21:00")], combina=False),
             "Id": "14", "IdModalidadFijaParaReservas": 5},
            {**_col_combinado("Pista 4", fijos=[("09:30", "11:00")], ocup=[("20:00", "21:30")], combina=False),
             "Id": "16", "IdModalidadFijaParaReservas": 5},
        ])
        f = matchpoint_combinado_parsear(d, FECHA, "Indoor", candidatos_densos=True)
        self.assertEqual([x for x in f if x.get("_verif")], [])
        self.assertIsNone(_franja(f, "Pista 2", "08:00"))
        self.assertIsNone(_franja(f, "Pista 4", "08:00"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
