"""Filtros que se aplican a la respuesta del modelo antes de enseñarla."""

import server

CONVERSACION_MADURA = (
    ("user", "busco perfil esponjoso para armarios de exterior"),
    ("assistant", "¿qué medida necesitas?"),
    ("user", "unos 20 metros de 10x10 mm"),
)


class TestPrecios:
    def test_una_cifra_en_euros_se_sustituye(self, msgs):
        salida = server._sanitize_reply("Sale a 3,50 € el metro.", msgs(*CONVERSACION_MADURA))
        assert "3,50" not in salida
        assert salida == server.NO_PRICE_REPLY

    def test_tambien_pilla_el_precio_escrito_con_letras(self, msgs):
        salida = server._sanitize_reply("Cuesta unos 120 euros.", msgs(*CONVERSACION_MADURA))
        assert "120" not in salida

    def test_una_medida_no_es_un_precio(self, msgs):
        respuesta = "El perfil de 10x10 mm va bien para 20 metros de armario."
        assert server._sanitize_reply(respuesta, msgs(*CONVERSACION_MADURA)) == respuesta

    def test_con_precios_publicos_activados_pasa(self, msgs, monkeypatch):
        monkeypatch.setattr(server, "ALLOW_PUBLIC_PRICES", True)
        # la regla de precios del prompt la lleva _user_asks_price; el filtro de
        # la respuesta es el que sigue mandando aquí
        assert not server._user_asks_price(msgs(("user", "¿qué precio tiene?")))


class TestCierreSinContacto:
    def test_no_se_cierra_sin_email_ni_telefono(self, msgs):
        salida = server._sanitize_reply("Pulsa «Enviar consulta» y te contactará comercial.",
                                        msgs(*CONVERSACION_MADURA))
        assert "Enviar consulta" not in salida
        assert "email" in salida.lower()

    def test_con_contacto_el_cierre_pasa(self, msgs):
        historial = msgs(*CONVERSACION_MADURA, ("user", "mi email es ana@empresa.es"))
        respuesta = "Perfecto, pulsa «Enviar consulta» y te contactará comercial."
        assert server._sanitize_reply(respuesta, historial) == respuesta


class TestSimpleChat:
    def test_en_modo_simple_no_se_tocan_los_precios(self, msgs, monkeypatch):
        # una clínica sí puede decir "la primera visita son 40 €"
        monkeypatch.setattr(server, "SIMPLE_CHAT", True)
        respuesta = "La primera visita son 40 €."
        assert server._sanitize_reply(respuesta, msgs(("user", "¿cuánto cuesta?"))) == respuesta

    def test_en_modo_simple_sigue_sin_cerrarse_sin_contacto(self, msgs, monkeypatch):
        monkeypatch.setattr(server, "SIMPLE_CHAT", True)
        salida = server._sanitize_reply("Te contactará el equipo comercial.", msgs(("user", "quiero cita")))
        assert "equipo comercial" not in salida

    def test_en_modo_simple_no_hay_maquina_de_fases(self, msgs, monkeypatch):
        monkeypatch.setattr(server, "SIMPLE_CHAT", True)
        extra = server._chat_system_extra(msgs(("user", "me duele una muela")))
        assert server.EARLY_CONTACT_FORBIDDEN_HINT not in extra
        assert server.NO_CONTACT_HINT in extra

    def test_con_la_maquina_de_fases_si_frena_el_contacto(self, msgs, monkeypatch):
        monkeypatch.setattr(server, "SIMPLE_CHAT", False)
        extra = server._chat_system_extra(msgs(("user", "¿hacéis juntas?")))
        assert server.EARLY_CONTACT_FORBIDDEN_HINT in extra


class TestPresentacionRepetida:
    def test_se_quita_si_el_bot_ya_habia_hablado(self, msgs):
        historial = msgs(("assistant", "¡Hola! ¿En qué te ayudo?"), ("user", "¿hacéis juntas?"))
        salida = server._strip_repeated_intro(f"Soy {server.BOT_NAME}. Sí, hacemos juntas.", historial)
        assert salida == "Sí, hacemos juntas."

    def test_en_el_primer_mensaje_se_respeta(self, msgs):
        respuesta = f"Soy {server.BOT_NAME}. Sí, hacemos juntas."
        assert server._strip_repeated_intro(respuesta, msgs(("user", "¿hacéis juntas?"))) == respuesta

    def test_si_al_quitarla_no_queda_nada_se_deja_la_original(self, msgs):
        historial = msgs(("assistant", "¡Hola!"), ("user", "¿quién eres?"))
        respuesta = f"Soy {server.BOT_NAME}."
        assert server._strip_repeated_intro(respuesta, historial) == respuesta
