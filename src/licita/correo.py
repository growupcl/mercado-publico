"""Envío de correos transaccionales (avisos de cuenta) por SMTP.

En producción se usa Google Workspace (hola@calza.cl): smtp.gmail.com, puerto 587 con STARTTLS, y una contraseña de
aplicación del usuario que envía. Ver docs/correo.md.
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid


class ErrorCorreo(Exception):
    pass


@dataclass
class ClienteCorreo:
    host: str
    puerto: int
    usuario: str
    clave: str
    remitente: str  # dirección que ve el cliente (debe estar autorizada para el usuario en Gmail: "Enviar como")
    nombre_remitente: str = "Calza"
    timeout: float = 30

    def mensaje(self, destinatario: str, asunto: str, texto: str, html: str | None = None) -> EmailMessage:
        m = EmailMessage()
        m["From"] = formataddr((self.nombre_remitente, self.remitente))
        m["To"] = destinatario
        m["Reply-To"] = self.remitente
        m["Subject"] = asunto
        m["Date"] = formatdate(localtime=False)
        m["Message-ID"] = make_msgid(domain=self.remitente.rsplit("@", 1)[-1])
        m["Auto-Submitted"] = "auto-generated"  # evita respuestas automáticas en bucle
        m.set_content(texto)
        if html:
            m.add_alternative(html, subtype="html")
        return m

    def enviar(self, destinatario: str, asunto: str, texto: str, html: str | None = None) -> str:
        """Envía el correo y devuelve su Message-ID."""
        m = self.mensaje(destinatario, asunto, texto, html)
        contexto = ssl.create_default_context()
        try:
            if self.puerto == 465:
                servidor = smtplib.SMTP_SSL(self.host, self.puerto, timeout=self.timeout, context=contexto)
            else:
                servidor = smtplib.SMTP(self.host, self.puerto, timeout=self.timeout)
            with servidor:
                if self.puerto != 465:
                    servidor.starttls(context=contexto)
                if self.usuario:
                    servidor.login(self.usuario, self.clave)
                servidor.send_message(m)
        except (smtplib.SMTPException, OSError) as e:
            raise ErrorCorreo(f"No se pudo enviar el correo a {destinatario}: {e}") from e
        return m["Message-ID"]
