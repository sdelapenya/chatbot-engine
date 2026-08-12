"""Servido del frontend y endpoints públicos."""

from fastapi.testclient import TestClient

import server

# Sin `with`: no se dispara el lifespan (ni cliente HTTP, ni watcher de sesiones,
# ni fichero de sesiones). Estas rutas no lo necesitan y así ningún test deja
# tareas de fondo vivas.
cliente = TestClient(server.app)


class TestPortadaPorDominio:
    def test_dominio_con_portada_propia(self):
        landing = server._host_landing("propuesta.ejemplo.test")
        assert landing is not None and landing.name == "propuesta.ejemplo.test.html"

    def test_el_puerto_no_cuenta(self):
        assert server._host_landing("propuesta.ejemplo.test:8002") is not None

    def test_da_igual_como_venga_en_mayusculas(self):
        assert server._host_landing("Propuesta.Ejemplo.TEST") is not None

    def test_dominio_sin_portada_cae_en_el_index(self):
        assert server._host_landing("otro.dominio.test") is None

    def test_cabecera_host_vacia(self):
        assert server._host_landing("") is None

    def test_no_se_puede_salir_del_directorio(self):
        # La cabecera Host la pone el cliente: hay que tratarla como entrada hostil.
        # `../index` y `../panel` apuntan a ficheros que SÍ existen un nivel por
        # encima de hosts/, así que sin validación devolverían una ruta buena.
        for hostil in ("../index", "../panel", "..", "a/../../etc/passwd",
                       "propuesta.ejemplo.test/../../index", "/etc/passwd", "a\\b"):
            assert server._host_landing(hostil) is None, hostil

    def test_la_ruta_se_sirve_de_verdad(self):
        por_defecto = cliente.get("/", headers={"host": "otro.dominio.test"})
        propia = cliente.get("/", headers={"host": "propuesta.ejemplo.test"})
        assert "portada por defecto" in por_defecto.text
        assert "portada del dominio" in propia.text


class TestEndpointsPublicos:
    def test_health(self):
        assert cliente.get("/health").json()["status"] == "ok"

    def test_config_expone_la_identidad_de_la_instancia(self):
        datos = cliente.get("/api/config").json()
        assert datos["company"] == "Elastómeros Ibérica S.L."
        assert datos["bot"] == "Nora"

    def test_config_no_filtra_secretos(self):
        crudo = cliente.get("/api/config").text
        assert "clave-falsa-de-test" not in crudo
        assert "token-de-prueba" not in crudo


class TestPanelYExportacion:
    def test_el_panel_pide_token(self):
        assert cliente.get("/api/leads").status_code == 401

    def test_token_incorrecto(self):
        assert cliente.get("/api/leads", params={"token": "otro"}).status_code == 401

    def test_el_csv_baja_con_un_nombre_ascii(self):
        # el nombre de la empresa lleva acentos y puntos; la cabecera, no
        respuesta = cliente.get("/api/leads/export", params={"token": "token-de-prueba"})
        assert respuesta.status_code == 200
        disposicion = respuesta.headers["content-disposition"]
        disposicion.encode("ascii")
        assert "leads_elastomeros_iberica_s_l_" in disposicion

    def test_el_csv_lleva_su_cabecera(self):
        respuesta = cliente.get("/api/leads/export", params={"token": "token-de-prueba"})
        assert respuesta.text.splitlines()[0].startswith("Fecha,Sesion,Canal")


class TestIdDeSesion:
    def test_acepta_un_id_normal(self):
        assert server._validate_session_id("sesion_12-34") == "sesion_12-34"

    def test_rechaza_caracteres_raros(self):
        for malo in ("../otro", "con espacio", "punto.y.coma;", "a" * 200):
            try:
                server._validate_session_id(malo)
            except ValueError:
                continue
            raise AssertionError(f"debería haber rechazado {malo!r}")
