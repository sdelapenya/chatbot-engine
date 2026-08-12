"""Detección de leads y umbral de palabras clave."""

import server


class TestDetectLead:
    def test_sin_email_ni_telefono_no_hay_lead(self, msgs):
        assert server.detect_lead(msgs(("user", "Hola, ¿hacéis perfiles de EPDM?"))) is None

    def test_email(self, msgs):
        lead = server.detect_lead(msgs(("user", "Escríbeme a Ana.Lopez@empresa.es")))
        assert lead["emails"] == ["ana.lopez@empresa.es"]

    def test_movil_con_separadores(self, msgs):
        # el formato 3-3-3 no es el único que escribe la gente
        for texto in ("655 33 21 09", "655.33.21.09", "655-33-21-09", "+34 655332109"):
            lead = server.detect_lead(msgs(("user", f"mi móvil es {texto}")))
            assert lead and lead["phones"], texto

    def test_no_confunde_un_numero_largo_con_un_movil(self, msgs):
        # números de pedido o importes largos no son teléfonos
        assert server.detect_lead(msgs(("user", "el pedido 6553321091234 sigue pendiente"))) is None

    def test_solo_mira_lo_que_escribe_el_usuario(self, msgs):
        # si el contacto sale del propio bot, no es un lead
        historial = msgs(("assistant", "escríbenos a info@ejemplo.es"))
        assert server.detect_lead(historial) is None

    def test_recoge_las_palabras_clave_encontradas(self, msgs):
        lead = server.detect_lead(msgs(("user", "necesito presupuesto de perfil, ana@x.es")))
        assert set(lead["keywords"]) == {"presupuesto", "perfil"}


class TestUmbralDePalabrasClave:
    def test_con_contacto_se_envia_siempre(self):
        assert server._lead_is_sendable({"emails": ["a@b.es"], "phones": [], "keywords": []})

    def test_sin_contacto_hace_falta_superar_el_umbral(self):
        # LEAD_MIN_KEYWORDS=1 en los tests: con una sola palabra no basta
        assert not server._lead_is_sendable({"emails": [], "phones": [], "keywords": ["presupuesto"]})
        assert server._lead_is_sendable({"emails": [], "phones": [], "keywords": ["presupuesto", "perfil"]})

    def test_un_resumen_con_datos_vale_aunque_no_haya_contacto(self):
        lead = {"emails": [], "phones": [], "keywords": [], "summary": {"empresa": "Talleres SL"}}
        assert server._lead_is_sendable(lead)

    def test_resumen_vacio_no_vale(self):
        lead = {"emails": [], "phones": [], "keywords": [], "summary": {"empresa": None, "producto": ""}}
        assert not server._lead_is_sendable(lead)

    def test_envio_manual_se_salta_el_umbral(self):
        assert server._lead_is_sendable({"emails": [], "phones": [], "keywords": []}, manual=True)


class TestMomentoDePedirContacto:
    def test_al_principio_todavia_no(self, msgs):
        assert not server._conversation_ready_for_contact(msgs(("user", "¿qué perfiles hacéis?")))

    def test_si_el_cliente_lo_pide_el_primero_si(self, msgs):
        assert server._conversation_ready_for_contact(msgs(("user", "quiero presupuesto")))
        assert server._conversation_ready_for_contact(msgs(("user", "que me llamen, por favor")))

    def test_con_medidas_y_dos_mensajes_si(self, msgs):
        historial = msgs(
            ("user", "busco perfil para armarios"),
            ("assistant", "¿de qué medida?"),
            ("user", "unos 20 metros de 10x10 mm"),
        )
        assert server._conversation_ready_for_contact(historial)

    def test_sin_historial_no(self):
        assert not server._conversation_ready_for_contact([])


class TestJsonDelModelo:
    def test_json_pelado(self):
        assert server._parse_json_from_llm('{"nombre": "Ana"}') == {"nombre": "Ana"}

    def test_json_envuelto_en_markdown(self):
        assert server._parse_json_from_llm('```json\n{"nombre": "Ana"}\n```') == {"nombre": "Ana"}

    def test_json_con_texto_alrededor(self):
        crudo = 'Claro, aquí tienes:\n{"nombre": "Ana"}\nEspero que sirva.'
        assert server._parse_json_from_llm(crudo) == {"nombre": "Ana"}

    def test_respuesta_sin_json_devuelve_vacio(self):
        assert server._parse_json_from_llm("no he podido extraer nada") == {}
