from datetime import timedelta

from licita.conversacion import bienvenida, es_saludo, responder
from licita.db import Calce, Empresa, Licitacion, Suscripcion, ahora, hora_chile


def _base(s, *, plan="pro", estado="prueba"):
    cierre = hora_chile(ahora()).replace(tzinfo=None) + timedelta(days=4)
    s.add_all([
        Licitacion(codigo="1-1-LE26", nombre="Artículos de aseo para CESFAM", estado_codigo=5,
                   organismo="Municipalidad de Chillán", region="Región del Ñuble", fecha_cierre=cierre),
        Licitacion(codigo="2-2-LE26", nombre="Artículos de aseo para liceo", estado_codigo=5,
                   organismo="Slep Santiago", region="Región Metropolitana de Santiago", fecha_cierre=cierre),
        Licitacion(codigo="3-3-COT26", tipo="COT", nombre="Bolsas de basura y artículos de aseo", estado_codigo=5,
                   organismo="Hospital de San Carlos", region="Región del Ñuble", fecha_cierre=cierre),
        Licitacion(codigo="4-4-LE26", nombre="Notebooks", estado_codigo=5, region="Región del Ñuble", fecha_cierre=cierre),
    ])
    e = Empresa(nombre="Aseo Sur", descripcion="Insumos de aseo", regiones=["Ñuble"], palabras_clave=["artículos de aseo"],
                whatsapp="56989299524", plan=plan)
    s.add(e)
    s.flush()
    s.add(Suscripcion(empresa_id=e.id, plan="pro", estado=estado, vigente_hasta=ahora() + timedelta(days=7)))
    s.commit()
    return e


def test_saludos():
    assert all(es_saludo(t) for t in ("hola", "hola!", "¡hola!", "buenas tardes", "buenos días", "hola 👋"))
    assert not any(es_saludo(t) for t in ("precios", "1", "¿piden boleta de garantía?", "holanda " * 10))


def test_primer_saludo_muestra_lo_abierto_hoy_para_la_empresa(Sesion):
    with Sesion() as s:
        e = _base(s)
        [texto] = responder(s, e, "texto", "Hola", url_publica="https://calza.cl")
    assert texto.startswith("¡Hola, Aseo Sur! 👋") and "el próximo día hábil" in texto
    assert "1. *Artículos de aseo para CESFAM*" in texto and "https://calza.cl/licitacion/1-1-LE26" in texto
    assert "⚡ Compra Ágil: Bolsas de basura" in texto  # plan Pro: incluye Compras Ágiles
    assert "liceo" not in texto and "Notebooks" not in texto  # otra región y otro rubro
    assert "cierra en 4 días" in texto and "Marca 👍 o 👎" in texto


def test_sin_tarjeta_recuerda_activar_la_prueba_y_sin_compras_agiles(Sesion):
    with Sesion() as s:
        e = _base(s, plan="gratis", estado="pendiente")
        texto = bienvenida(s, e)
    assert "Te falta un paso para empezar tu prueba gratis" in texto and "*CUENTA*" in texto
    assert "Compra Ágil" not in texto and "CESFAM" in texto


def test_despues_del_primer_resumen_el_saludo_muestra_la_ayuda(Sesion):
    with Sesion() as s:
        e = _base(s)
        s.add(Calce(empresa_id=e.id, licitacion_codigo="1-1-LE26", puntaje=90, notificado=True,
                    notificado_en=ahora(), posicion_resumen=1))
        s.commit()
        [texto] = responder(s, e, "texto", "hola")
    assert "Soy el asistente de Calza 🤖" in texto
