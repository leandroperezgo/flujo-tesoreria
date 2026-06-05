import os
import re
import anthropic
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Inicializar
slack_app = App(token=os.environ["SLACK_BOT_TOKEN"])
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

CANAL_AUTORIZACIONES = "autorizaciones"

def obtener_imagen_base64(url, client):
    """Descarga una imagen de Slack y la convierte a base64"""
    import urllib.request
    import base64
    
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"})
    with urllib.request.urlopen(req) as response:
        return base64.standard_b64encode(response.read()).decode("utf-8")

def buscar_flujos_del_dia(client):
    """Busca los mensajes de Dani con imágenes en #autorizaciones"""
    try:
        # Buscar el canal
        canales = client.conversations_list(types="public_channel,private_channel")
        canal_id = None
        for canal in canales["channels"]:
            if canal["name"] == CANAL_AUTORIZACIONES:
                canal_id = canal["id"]
                break
        
        if not canal_id:
            return None, None, "No encontré el canal #autorizaciones"
        
        # Obtener mensajes recientes
        mensajes = client.conversations_history(channel=canal_id, limit=20)
        
        imagen_fd = None
        imagen_gsd = None
        
        for msg in mensajes["messages"]:
            if "files" in msg:
                for archivo in msg["files"]:
                    if archivo.get("mimetype", "").startswith("image/"):
                        url = archivo.get("url_private_download") or archivo.get("url_private")
                        img_b64 = obtener_imagen_base64(url, client)
                        
                        # Preguntar a Claude qué flujo es
                        check = anthropic_client.messages.create(
                            model="claude-sonnet-4-20250514",
                            max_tokens=50,
                            messages=[{
                                "role": "user",
                                "content": [
                                    {"type": "image", "source": {"type": "base64", "media_type": archivo.get("mimetype", "image/png"), "data": img_b64}},
                                    {"type": "text", "text": "Esta imagen es un flujo financiero. ¿Es de 'FINANZAS DIGITALES' o de 'GO SERVICIOS DIGITALES'? Responde solo con: FD o GSD"}
                                ]
                            }]
                        )
                        tipo = check.content[0].text.strip()
                        
                        if "FD" in tipo and not imagen_fd:
                            imagen_fd = (img_b64, archivo.get("mimetype", "image/png"))
                        elif "GSD" in tipo and not imagen_gsd:
                            imagen_gsd = (img_b64, archivo.get("mimetype", "image/png"))
            
            if imagen_fd and imagen_gsd:
                break
        
        return imagen_fd, imagen_gsd, None
    
    except Exception as e:
        return None, None, str(e)

def analizar_flujo(img_b64, mimetype, tipo):
    """Usa Claude para analizar el flujo y devolver acciones"""
    
    if tipo == "FD":
        prompt = """Analizá este flujo de FINANZAS DIGITALES y extraé:

1. Saldo mínimo a dejar en SANTANDER = Imp déb/créd Santander + mitad de Caja Fija
2. Saldo mínimo a dejar en GALICIA = Imp déb/créd Galicia + mitad de Caja Fija  
3. Saldo Rescate/Suscripción (si es negativo = RESCATAR, si es positivo = SUSCRIBIR)
4. FCI Galicia actual
5. Saldo Final FCI después del movimiento

Respondé EXACTAMENTE en este formato:
FECHA: [fecha del flujo]
SANTANDER_MIN: $[monto]
GALICIA_MIN: $[monto]
ACCION_FCI: [RESCATAR/SUSCRIBIR] $[monto]
FCI_ACTUAL: $[monto]
FCI_FINAL: $[monto]
EGRESOS_TOTAL: $[monto]
INGRESOS_TOTAL: $[monto]"""
    else:
        prompt = """Analizá este flujo de GO SERVICIOS DIGITALES y extraé:

1. Saldo mínimo a dejar en SANTANDER = Imp déb/créd Santander + mitad de Caja Fija
2. Saldo mínimo a dejar en MACRO = Imp déb/créd Macro
3. Saldo mínimo a dejar en SUPERVIELLE = Imp déb/créd Supervielle
4. Saldo mínimo a dejar en GALICIA = Imp déb/créd Galicia + mitad de Caja Fija
5. Saldo Rescate/Suscripción (si es negativo = RESCATAR, si es positivo = SUSCRIBIR)
6. FCI Galicia actual
7. Saldo Final Bancos
8. Saldo Final Bancos + ALYC

Respondé EXACTAMENTE en este formato:
FECHA: [fecha del flujo]
SANTANDER_MIN: $[monto]
MACRO_MIN: $[monto]
SUPERVIELLE_MIN: $[monto]
GALICIA_MIN: $[monto]
ACCION_FCI: [RESCATAR/SUSCRIBIR] $[monto]
FCI_ACTUAL: $[monto]
SALDO_FINAL_BANCOS: $[monto]
SALDO_FINAL_ALYC: $[monto]
EGRESOS_TOTAL: $[monto]
INGRESOS_TOTAL: $[monto]"""

    respuesta = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mimetype, "data": img_b64}},
                {"type": "text", "text": prompt}
            ]
        }]
    )
    return respuesta.content[0].text

def formatear_resumen_fd(datos):
    """Formatea el resumen de FD para Slack"""
    lineas = {}
    for linea in datos.strip().split("\n"):
        if ":" in linea:
            k, v = linea.split(":", 1)
            lineas[k.strip()] = v.strip()
    
    accion = lineas.get("ACCION_FCI", "")
    emoji_fci = "🔴 RESCATAR" if "RESCATAR" in accion else "🟢 SUSCRIBIR"
    monto_fci = accion.replace("RESCATAR", "").replace("SUSCRIBIR", "").strip()
    
    return f"""
💼 *FINANZAS DIGITALES — {lineas.get('FECHA', '')}*
━━━━━━━━━━━━━━━━━━━━━━
*Saldos mínimos a mantener:*
🏦 Santander: `{lineas.get('SANTANDER_MIN', '')}`
🏦 Galicia: `{lineas.get('GALICIA_MIN', '')}`

*Movimiento FCI Galicia:*
{emoji_fci}: `{monto_fci}`
📊 FCI actual: `{lineas.get('FCI_ACTUAL', '')}`
📊 FCI final: `{lineas.get('FCI_FINAL', '')}`

*Resumen del día:*
📥 Ingresos: `{lineas.get('INGRESOS_TOTAL', '')}`
📤 Egresos: `{lineas.get('EGRESOS_TOTAL', '')}`
"""

def formatear_resumen_gsd(datos):
    """Formatea el resumen de GSD para Slack"""
    lineas = {}
    for linea in datos.strip().split("\n"):
        if ":" in linea:
            k, v = linea.split(":", 1)
            lineas[k.strip()] = v.strip()
    
    accion = lineas.get("ACCION_FCI", "")
    emoji_fci = "🔴 RESCATAR" if "RESCATAR" in accion else "🟢 SUSCRIBIR"
    monto_fci = accion.replace("RESCATAR", "").replace("SUSCRIBIR", "").strip()
    
    return f"""
🏢 *GO SERVICIOS DIGITALES — {lineas.get('FECHA', '')}*
━━━━━━━━━━━━━━━━━━━━━━
*Saldos mínimos a mantener:*
🏦 Santander: `{lineas.get('SANTANDER_MIN', '')}`
🏦 Macro: `{lineas.get('MACRO_MIN', '')}`
🏦 Supervielle: `{lineas.get('SUPERVIELLE_MIN', '')}`
🏦 Galicia: `{lineas.get('GALICIA_MIN', '')}`

*Movimiento FCI Galicia:*
{emoji_fci}: `{monto_fci}`
📊 FCI actual: `{lineas.get('FCI_ACTUAL', '')}`

*Saldos finales:*
🏛️ Bancos: `{lineas.get('SALDO_FINAL_BANCOS', '')}`
🏛️ Bancos + ALYC: `{lineas.get('SALDO_FINAL_ALYC', '')}`

*Resumen del día:*
📥 Ingresos: `{lineas.get('INGRESOS_TOTAL', '')}`
📤 Egresos: `{lineas.get('EGRESOS_TOTAL', '')}`
"""

@slack_app.command("/flujo")
def comando_flujo(ack, say, client, command):
    ack()
    
    say("🔍 Buscando los flujos del día en #autorizaciones... Dame un momento.")
    
    imagen_fd, imagen_gsd, error = buscar_flujos_del_dia(client)
    
    if error:
        say(f"❌ Error al buscar los flujos: {error}")
        return
    
    if not imagen_fd and not imagen_gsd:
        say("⚠️ No encontré imágenes de flujos de hoy en #autorizaciones. ¿Ya los publicó Dani?")
        return
    
    resumen = "📊 *RESUMEN DE ACCIONES DEL DÍA*\n\n"
    
    if imagen_fd:
        datos_fd = analizar_flujo(imagen_fd[0], imagen_fd[1], "FD")
        resumen += formatear_resumen_fd(datos_fd)
    else:
        resumen += "⚠️ _No se encontró el flujo de Finanzas Digitales_\n\n"
    
    if imagen_gsd:
        datos_gsd = analizar_flujo(imagen_gsd[0], imagen_gsd[1], "GSD")
        resumen += formatear_resumen_gsd(datos_gsd)
    else:
        resumen += "⚠️ _No se encontró el flujo de Go Servicios Digitales_\n\n"
    
    say(resumen)

if __name__ == "__main__":
    handler = SocketModeHandler(slack_app, os.environ["SLACK_APP_TOKEN"])
    print("⚡ Bot Flujo Tesoreria corriendo...")
    handler.start()
