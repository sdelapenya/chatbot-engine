"""Configuración por instancia: palabras clave, textos y nombre de fichero."""

from pathlib import Path

import server

FIXTURES = Path(__file__).parent / "fixtures"


class TestPalabrasClave:
    def test_ignora_comentarios_y_lineas_vacias(self):
        assert "# comentario que no es palabra clave" not in server.COMMERCIAL_KEYWORDS
        assert "" not in server.COMMERCIAL_KEYWORDS

    def test_normaliza_a_minusculas(self):
        # el texto del usuario se compara en minúsculas: una clave en mayúsculas
        # en el fichero no llegaría a encajar nunca
        assert "junta" in server.COMMERCIAL_KEYWORDS
        assert "JUNTA" not in server.COMMERCIAL_KEYWORDS

    def test_fichero_inexistente_no_revienta(self, monkeypatch):
        monkeypatch.setattr(server, "KEYWORDS_FILE", Path("/no/existe/keywords.txt"))
        assert server._load_keywords() == []


class TestTextosPorInstancia:
    def test_sin_textos_file_devuelve_el_valor_por_defecto(self, monkeypatch):
        monkeypatch.setattr(server, "_TEXTS", {})
        assert server._text("no_price_reply", "por defecto") == "por defecto"

    def test_con_textos_file_manda_el_fichero(self, monkeypatch):
        monkeypatch.setattr(server, "_TEXTS_FILE", str(FIXTURES / "textos.json"))
        textos = server._load_texts()
        monkeypatch.setattr(server, "_TEXTS", textos)
        assert server._text("no_price_reply", "por defecto").startswith("En la clínica")
        # una clave que el fichero no trae sigue cayendo en el valor del motor
        assert server._text("clave_que_no_esta", "por defecto") == "por defecto"

    def test_textos_file_inexistente_falla_al_arrancar(self, monkeypatch):
        monkeypatch.setattr(server, "_TEXTS_FILE", "/no/existe/textos.json")
        try:
            server._load_texts()
        except RuntimeError as e:
            assert "TEXTS_FILE" in str(e)
        else:
            raise AssertionError("un TEXTS_FILE mal puesto tiene que cantar en el arranque")

    def test_textos_file_que_no_es_un_objeto(self, monkeypatch, tmp_path):
        malo = tmp_path / "textos.json"
        malo.write_text('["esto", "es", "una", "lista"]', encoding="utf-8")
        monkeypatch.setattr(server, "_TEXTS_FILE", str(malo))
        try:
            server._load_texts()
        except RuntimeError as e:
            assert "objeto JSON" in str(e)
        else:
            raise AssertionError("una lista no vale como TEXTS_FILE")


class TestAvisoPorEmail:
    def test_sin_destinatario_no_se_intenta_enviar(self, monkeypatch):
        # el lead se guarda igual en disco; lo que no puede es reventar el turno
        monkeypatch.setattr(server, "EMAIL_TO", "")
        monkeypatch.setattr(server, "EMAIL_FROM", "")

        def no_deberia_llamarse(*a, **k):
            raise AssertionError("no debería abrir una conexión SMTP sin destinatario")

        monkeypatch.setattr(server.smtplib, "SMTP", no_deberia_llamarse)
        server._send_email_sync("sesion123", [], {"emails": ["a@b.es"]})


class TestSlug:
    # COMPANY_NAME lo pone cada instancia y trae acentos, puntos y paréntesis;
    # va a la cabecera Content-Disposition, que la RFC 6266 quiere en ASCII.
    def test_quita_acentos_y_puntuacion(self):
        assert server._slug("Elastómeros Ejemplo S.L.") == "elastomeros_ejemplo_s_l"

    def test_parentesis_y_espacios(self):
        assert server._slug("Clínica Ejemplo (demo)") == "clinica_ejemplo_demo"

    def test_resultado_siempre_ascii(self):
        for nombre in ("Clínica Dental de Ejemplo", "Señor Ñandú", "日本語", ""):
            slug = server._slug(nombre)
            slug.encode("ascii")  # revienta si se cuela un byte no-ASCII
            assert slug

    def test_nombre_sin_nada_aprovechable(self):
        assert server._slug("···") == "instancia"
