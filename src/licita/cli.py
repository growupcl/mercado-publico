"""Línea de comandos de Calza.

Ejemplos:
  licita sync --fecha 2026-10-06
  licita clasificar
  licita empresa-agregar --nombre "Aseo Sur" --descripcion "Vendemos insumos de aseo..." --regiones "Biobío,Ñuble"
  licita calce --empresa 1
  licita resumen --empresa 1
  licita analizar --pdf bases.pdf --codigo 1234-56-LE26
  licita precios --codigo 1234-56-LE26
  licita ciclo
  licita whatsapp-enviar
  licita servidor --puerto 8000
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from sqlalchemy import select

from .config import Config
from .db import Empresa, crear_sesiones
from .analisis import DocumentoInvalido, LimiteAlcanzado
from .mercadopago import ErrorMercadoPago
from .ia import ErrorIA
from .mercadopublico import MercadoPublicoError
from .whatsapp import ErrorWhatsApp, normalizar_telefono


def _ia(config: Config):
    from .ia import AsistenteIA

    return AsistenteIA(modelo=config.modelo_clasificacion)


def cmd_sync(args, config: Config, Sesion) -> int:
    from .mercadopublico import MercadoPublicoClient
    from .sync import sincronizar_licitaciones, sincronizar_ordenes_de_compra

    cliente = MercadoPublicoClient(config.ticket)
    from .db import hoy_en_chile

    fecha = date.fromisoformat(args.fecha) if args.fecha else hoy_en_chile()
    with Sesion() as s:
        r = sincronizar_licitaciones(s, cliente, fecha, max_detalles=args.max_detalles)
        print(f"Licitaciones {fecha}: {r.nuevas} nuevas, {r.actualizadas} actualizadas, {r.sin_cambios} sin cambios, "
              f"{r.omitidas} omitidas, {r.pendientes} pendientes, {r.errores} errores")
        if args.ordenes:
            from .calce import interes_de_clientes

            r = sincronizar_ordenes_de_compra(s, cliente, fecha, max_detalles=args.max_detalles,
                                              interes=None if args.todas_las_ordenes else interes_de_clientes(s))
            print(f"Órdenes de compra {fecha}: {r.nuevas} nuevas, {r.sin_cambios} ya guardadas, "
                  f"{r.omitidas} sin relación con clientes, {r.pendientes} pendientes, {r.errores} errores")
    return 0


def cmd_clasificar(args, config: Config, Sesion) -> int:
    from .ia import clasificar_pendientes

    with Sesion() as s:
        ok, errores = clasificar_pendientes(s, _ia(config), limite=args.limite)
    print(f"Clasificadas: {ok} · Errores: {errores}")
    return 0


def cmd_empresa_agregar(args, config: Config, Sesion) -> int:
    perfil = _ia(config).extraer_perfil(args.descripcion)
    empresa = Empresa(
        nombre=args.nombre,
        descripcion=args.descripcion,
        regiones=[r.strip() for r in args.regiones.split(",") if r.strip()] if args.regiones else [],
        monto_min=args.monto_min,
        monto_max=args.monto_max,
        palabras_clave=perfil.palabras_clave + perfil.rubros,
        whatsapp=normalizar_telefono(args.whatsapp or ""),
        plan=args.plan,
    )
    with Sesion() as s:
        s.add(empresa)
        s.commit()
        print(f"Empresa #{empresa.id} creada: {empresa.nombre}")
        print("Palabras clave detectadas:", ", ".join(empresa.palabras_clave))
    return 0


def cmd_empresas(args, config: Config, Sesion) -> int:
    with Sesion() as s:
        for e in s.scalars(select(Empresa).order_by(Empresa.id)):
            regiones = ", ".join(e.regiones) or "todas"
            print(f"#{e.id} {e.nombre} · regiones: {regiones} · {len(e.palabras_clave)} palabras clave")
    return 0


def _empresa(s, empresa_id: int) -> Empresa:
    empresa = s.get(Empresa, empresa_id)
    if empresa is None:
        raise SystemExit(f"No existe la empresa #{empresa_id}")
    return empresa


def cmd_ciclo(args, config: Config, Sesion) -> int:
    from .mercadopublico import MercadoPublicoClient
    from .tareas import ejecutar_ciclo

    cliente = MercadoPublicoClient(config.ticket) if config.ticket else None
    with Sesion() as s:
        r = ejecutar_ciclo(s, cliente, _ia(config), max_detalles=args.max_detalles)
    print(f"Licitaciones nuevas: {r.licitaciones_nuevas} · Clasificadas: {r.clasificadas} · Calces: {r.calces} · Errores: {len(r.errores)}")
    return 1 if r.errores else 0


def cmd_calce(args, config: Config, Sesion) -> int:
    from .calce import buscar_calces

    with Sesion() as s:
        calces = buscar_calces(s, _empresa(s, args.empresa), _ia(config), limite=args.limite)
        if not calces:
            print("No hay licitaciones nuevas que calcen con este perfil.")
        for c in calces:
            print(f"{c.puntaje:>3}%  {c.licitacion_codigo}  {c.razon}")
    return 0


def cmd_resumen(args, config: Config, Sesion) -> int:
    from .resumen import resumen_diario

    with Sesion() as s:
        texto = resumen_diario(s, _empresa(s, args.empresa), umbral=args.umbral, marcar_notificado=args.marcar)
    print(texto or "Nada nuevo que enviar hoy.")
    return 0


def _whatsapp(config: Config):
    from .whatsapp import ClienteWhatsApp

    return ClienteWhatsApp(config.whatsapp_token, config.whatsapp_phone_number_id, version=config.whatsapp_api_version)


def cmd_whatsapp_enviar(args, config: Config, Sesion) -> int:
    from .notificaciones import enviar_resumenes

    with Sesion() as s:
        r = enviar_resumenes(
            s, _whatsapp(config), plantilla=config.whatsapp_plantilla_resumen, idioma=config.whatsapp_idioma,
            umbral=args.umbral,
        )
    print(f"Plantillas: {r.plantillas} · Textos gratis (ventana abierta): {r.textos} · Sin novedades: {r.sin_novedades} · Errores: {r.errores}")
    return 0


def _servicio_analisis(config: Config):
    from .analisis import AlmacenDocumentos, AnalizadorBases, ServicioAnalisis

    return ServicioAnalisis(
        AlmacenDocumentos(config.dir_documentos), AnalizadorBases(modelo=config.modelo_analisis),
        limite_mensual=config.limite_analisis_mes,
    )


def cmd_analizar(args, config: Config, Sesion) -> int:
    from pathlib import Path

    from .analisis import textos_analisis

    contenido = Path(args.pdf).read_bytes()
    with Sesion() as s:
        empresa = _empresa(s, args.empresa) if args.empresa else None
        analisis, reutilizado = _servicio_analisis(config).analizar(
            s, contenido, empresa=empresa, nombre_archivo=Path(args.pdf).name, codigo=args.codigo,
        )
        if reutilizado:
            print("(Análisis reutilizado: este PDF ya se había analizado; no tuvo costo de IA.)\n")
        else:
            print(f"(Tokens: {analisis.tokens_entrada} de entrada, {analisis.tokens_salida} de salida.)\n")
        print("\n\n".join(textos_analisis(analisis.resultado)))
    return 0


def cmd_precios(args, config: Config, Sesion) -> int:
    from .db import Licitacion
    from .precios import informe_precios, texto_precios

    with Sesion() as s:
        lic = s.get(Licitacion, args.codigo)
        if lic is None:
            raise SystemExit(f"No existe la licitación {args.codigo} en la base. Sincronízala primero con licita sync.")
        print(texto_precios(informe_precios(s, lic)))
    return 0


def cmd_reconstruir_precios(args, config: Config, Sesion) -> int:
    from .precios import reconstruir_precios

    with Sesion() as s:
        print(f"Precios agregados: {reconstruir_precios(s)}")
    return 0


def cmd_whatsapp_plantillas(args, config: Config, Sesion) -> int:
    import json

    from .plantillas_whatsapp import PLANTILLAS

    seleccion = [PLANTILLAS[n] for n in args.nombres] if args.nombres else list(PLANTILLAS.values())
    if not args.crear:
        for p in seleccion:
            print(f"# {p.nombre}: {p.uso}")
            print(json.dumps(p.definicion_meta(config.whatsapp_idioma), ensure_ascii=False, indent=2), end="\n\n")
        print("Para enviarlas a revisión de Meta: licita whatsapp-plantillas --crear")
        return 0
    if not config.whatsapp_waba_id:
        raise SystemExit("Falta WHATSAPP_WABA_ID (ID de la cuenta de WhatsApp Business de Calza).")
    wa, errores = _whatsapp(config), 0
    for p in seleccion:
        try:
            r = wa.crear_plantilla(config.whatsapp_waba_id, p.definicion_meta(config.whatsapp_idioma))
            print(f"✓ {p.nombre}: {r.get('status', 'enviada')} (categoría {r.get('category', 'UTILITY')})")
        except ErrorWhatsApp as e:
            errores += 1
            print(f"✗ {e}")
    return 1 if errores else 0


def cmd_migrar(args, config: Config, Sesion) -> int:
    # crear_sesiones() ya aplicó las migraciones pendientes al iniciar el comando.
    if args.nueva:
        from .migrar import nueva_migracion

        nueva_migracion(config.database_url, args.nueva)
        print("Migración creada en src/licita/migraciones/versions/. Revísala antes de publicarla.")
    else:
        print("La base de datos está en la última versión.")
    return 0


def cmd_servidor(args, config: Config, Sesion) -> int:
    import uvicorn

    from .ia import AsistenteIA
    from .servidor import crear_app
    from .web import crear_router_web

    mp = None
    if config.mercadopago_access_token:
        from .mercadopago import ClienteMercadoPago

        mp = ClienteMercadoPago(config.mercadopago_access_token)
    wa = _whatsapp(config) if config.whatsapp_token else None
    router_web = crear_router_web(
        Sesion, mp=mp, ia=AsistenteIA(modelo=config.modelo_clasificacion),
        url_publica=config.url_publica, whatsapp_publico=config.whatsapp_publico,
        mp_webhook_secreto=config.mercadopago_webhook_secret, prestador=config.prestador,
        wa=wa, idioma_whatsapp=config.whatsapp_idioma,
    )
    app = crear_app(
        Sesion, wa, verify_token=config.whatsapp_verify_token,
        app_secret=config.whatsapp_app_secret, url_registro=config.url_registro or f"{config.url_publica}/registro",
        phone_number_id=config.whatsapp_phone_number_id, analisis=_servicio_analisis(config) if wa else None,
        url_publica=config.url_publica, router_web=router_web,
    )
    if wa is None:
        print("Aviso: WhatsApp no está configurado; el servidor solo atiende el sitio web.")
    if mp is None:
        print("Aviso: Mercado Pago no está configurado; los pagos en línea están desactivados.")
    # Detrás de Caddy (HTTPS): confiar en sus encabezados para conocer el esquema y la IP real.
    uvicorn.run(app, host=args.host, port=args.puerto, proxy_headers=True, forwarded_allow_ips="*")
    return 0


def cmd_suscripciones(args, config: Config, Sesion) -> int:
    from .suscripciones import por_vencer, revisar_vencimientos

    with Sesion() as s:
        for e in revisar_vencimientos(s):
            print(f"Vencida → plan gratis: #{e.id} {e.nombre}")
        for e, sus in por_vencer(s, dias=args.dias):
            print(f"Por vencer ({sus.vigente_hasta:%d-%m-%Y}, {sus.estado}): #{e.id} {e.nombre} · {e.email}")
        if config.whatsapp_token:
            from .avisos import avisar_cobros_rechazados

            enviados = avisar_cobros_rechazados(s, _whatsapp(config), url_publica=config.url_publica, idioma=config.whatsapp_idioma)
            print(f"Avisos de cobro rechazado enviados: {enviados}")
    return 0


def cmd_facturas(args, config: Config, Sesion) -> int:
    from . import facturas

    with Sesion() as s:
        if args.emitida is not None:
            if not args.folio:
                raise ValueError("Indica el folio con --folio.")
            pago = facturas.marcar_emitida(s, args.emitida, args.folio)
            s.commit()
            print(f"Pago #{pago.id}: factura folio {pago.factura_folio} registrada.")
            return 0
        lista = facturas.pendientes(s)
        if args.csv:
            sys.stdout.write(facturas.csv_facturas(lista))
            return 0
        if not lista:
            print("No hay facturas pendientes.")
            return 0
        print(f"Facturas por emitir en el SII ({config.prestador}): {len(lista)}\n")
        for f in lista:
            print(facturas.texto(f) + "\n")
        print("Cuando emitas una: licita facturas --emitida PAGO_ID --folio NUMERO")
    return 0


def cmd_compra_agil(args, config: Config, Sesion) -> int:
    from .compra_agil import ClienteCompraAgil
    from .tareas import ciclo_compra_agil

    cliente = ClienteCompraAgil(config.ticket) if config.ticket else None
    wa = _whatsapp(config) if config.whatsapp_token and not args.sin_alertas else None
    with Sesion() as s:
        r = ciclo_compra_agil(s, cliente, _ia(config), wa, idioma=config.whatsapp_idioma, max_detalles=args.max_detalles)
    if r.sync:
        print(f"Compras Ágiles: {r.sync.nuevas} nuevas, {r.sync.actualizadas} actualizadas, "
              f"{r.sync.detalles} detalles ({r.sync.pendientes} interesantes quedaron sin detalle)")
    print(f"Calces nuevos: {r.calces}")
    if r.alertas:
        if r.alertas.fuera_de_horario:
            print("Alertas: fuera de horario (8:00 a 21:00), no se envió nada")
        else:
            print(f"Alertas enviadas: {r.alertas.alertas} ({r.alertas.plantillas} plantillas, {r.alertas.textos} textos, "
                  f"{r.alertas.errores} errores)")
    for e in r.errores:
        print(f"Error: {e}", file=sys.stderr)
    return 1 if r.errores else 0


def cmd_compra_agil_precios(args, config: Config, Sesion) -> int:
    from .calce import interes_de_clientes
    from .compra_agil import ClienteCompraAgil, actualizar_precios_compra_agil
    from .planes import PLANES_CON_PRECIOS

    with Sesion() as s:
        interes = interes_de_clientes(s, planes=PLANES_CON_PRECIOS)
        r = actualizar_precios_compra_agil(s, ClienteCompraAgil(config.ticket), interes=interes,
                                           horas=args.horas, max_detalles=args.max_detalles)
    print(f"Compras Ágiles cerradas revisadas: {r.cerradas} · detalles: {r.detalles} "
          f"({r.pendientes} quedaron para otro día) · con cotizaciones: {r.con_cotizaciones} · precios guardados: {r.cotizaciones}")
    return 1 if r.errores else 0


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="licita", description="Copiloto de licitaciones de Mercado Público")
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("sync", help="Sincroniza licitaciones (y opcionalmente órdenes de compra) de un día")
    s.add_argument("--fecha", help="AAAA-MM-DD (por defecto hoy)")
    s.add_argument("--ordenes", action="store_true", help="También sincroniza órdenes de compra (las relacionadas con los clientes)")
    s.add_argument("--todas-las-ordenes", action="store_true", help="Con --ordenes: no filtrar por los rubros de los clientes")
    s.add_argument("--max-detalles", type=int, help="Máximo de detalles a pedir (cuida el límite diario del ticket)")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("clasificar", help="Clasifica con IA las licitaciones publicadas pendientes")
    s.add_argument("--limite", type=int, default=100)
    s.set_defaults(fn=cmd_clasificar)

    s = sub.add_parser("empresa-agregar", help="Registra una empresa y extrae su perfil con IA")
    s.add_argument("--nombre", required=True)
    s.add_argument("--descripcion", required=True, help="Qué vende la empresa, en lenguaje natural")
    s.add_argument("--regiones", help="Separadas por coma; vacío = todas")
    s.add_argument("--monto-min", type=float)
    s.add_argument("--monto-max", type=float)
    s.add_argument("--whatsapp")
    s.add_argument("--plan", choices=["gratis", "pyme", "pro", "consultora"], default="pyme")
    s.set_defaults(fn=cmd_empresa_agregar)

    s = sub.add_parser("empresas", help="Lista las empresas registradas")
    s.set_defaults(fn=cmd_empresas)

    s = sub.add_parser("ciclo", help="Sincroniza, clasifica y busca calces para todas las empresas activas")
    s.add_argument("--max-detalles", type=int, help="Máximo de detalles de licitaciones a pedir por día sincronizado")
    s.set_defaults(fn=cmd_ciclo)

    s = sub.add_parser("compra-agil", help="Compras Ágiles nuevas, calce con IA y alertas urgentes por WhatsApp (plan Pro)")
    s.add_argument("--max-detalles", type=int, default=25, help="Máximo de detalles a pedir (cada uno demora ~20 s)")
    s.add_argument("--sin-alertas", action="store_true", help="Solo sincroniza y evalúa; no envía WhatsApp")
    s.set_defaults(fn=cmd_compra_agil)

    s = sub.add_parser("compra-agil-precios", help="Guarda las cotizaciones de las Compras Ágiles que cerraron (precios de referencia)")
    s.add_argument("--horas", type=int, default=26, help="Revisa las que cambiaron en estas últimas horas")
    s.add_argument("--max-detalles", type=int, default=100, help="Máximo de detalles a pedir (cada uno demora ~20 s)")
    s.set_defaults(fn=cmd_compra_agil_precios)

    s = sub.add_parser("calce", help="Busca y evalúa licitaciones para una empresa")
    s.add_argument("--empresa", type=int, required=True)
    s.add_argument("--limite", type=int, default=20, help="Candidatas que evalúa la IA")
    s.set_defaults(fn=cmd_calce)

    s = sub.add_parser("resumen", help="Muestra el resumen diario (formato WhatsApp)")
    s.add_argument("--empresa", type=int, required=True)
    s.add_argument("--umbral", type=int, default=60, help="Puntaje mínimo de calce")
    s.add_argument("--marcar", action="store_true", help="Marca los calces como notificados")
    s.set_defaults(fn=cmd_resumen)

    s = sub.add_parser("analizar", help="Analiza con IA un PDF de bases de licitación")
    s.add_argument("--pdf", required=True, help="Ruta al PDF de las bases")
    s.add_argument("--codigo", help="Código de la licitación (opcional)")
    s.add_argument("--empresa", type=int, help="Empresa a la que se le descuenta del límite mensual (opcional)")
    s.set_defaults(fn=cmd_analizar)

    s = sub.add_parser("precios", help="Precios de referencia para los ítems de una licitación")
    s.add_argument("--codigo", required=True, help="Código de la licitación")
    s.set_defaults(fn=cmd_precios)

    s = sub.add_parser("reconstruir-precios", help="Llena la tabla de precios con lo ya sincronizado")
    s.set_defaults(fn=cmd_reconstruir_precios)

    s = sub.add_parser("suscripciones", help="Vence suscripciones impagas, lista las por vencer y avisa cobros rechazados")
    s.add_argument("--dias", type=int, default=3, help="Días de anticipación para listar las por vencer")
    s.set_defaults(fn=cmd_suscripciones)

    s = sub.add_parser("facturas", help="Lista los pagos por facturar en el portal del SII o registra el folio emitido")
    s.add_argument("--csv", action="store_true", help="Exporta las pendientes en CSV (separado por punto y coma)")
    s.add_argument("--emitida", type=int, metavar="PAGO_ID", help="Marca la factura de este pago como emitida")
    s.add_argument("--folio", help="Con --emitida: folio que entregó el SII")
    s.set_defaults(fn=cmd_facturas)

    s = sub.add_parser("migrar", help="Aplica las migraciones pendientes de la base de datos")
    s.add_argument("--nueva", metavar="MENSAJE", help="Genera una migración nueva a partir de los cambios en los modelos")
    s.set_defaults(fn=cmd_migrar)

    s = sub.add_parser("whatsapp-plantillas", help="Muestra las plantillas de WhatsApp o las envía a revisión de Meta")
    s.add_argument("--crear", action="store_true", help="Enviarlas a revisión (requiere WHATSAPP_WABA_ID)")
    s.add_argument("nombres", nargs="*", help="Solo estas plantillas (por defecto, todas)")
    s.set_defaults(fn=cmd_whatsapp_plantillas)

    s = sub.add_parser("whatsapp-enviar", help="Envía el resumen diario por WhatsApp a todas las empresas")
    s.add_argument("--umbral", type=int, default=60, help="Puntaje mínimo de calce")
    s.set_defaults(fn=cmd_whatsapp_enviar)

    s = sub.add_parser("servidor", help="Inicia el sitio web y el receptor de mensajes de WhatsApp")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--puerto", type=int, default=8000)
    s.set_defaults(fn=cmd_servidor)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # httpx registra cada URL, y la de Mercado Público lleva el ticket: no debe quedar en los registros.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = construir_parser().parse_args(argv)
    config = Config.desde_entorno()
    Sesion = crear_sesiones(config.database_url)
    try:
        return args.fn(args, config, Sesion)
    except (MercadoPublicoError, ErrorIA, ErrorWhatsApp, ErrorMercadoPago, DocumentoInvalido, LimiteAlcanzado, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
